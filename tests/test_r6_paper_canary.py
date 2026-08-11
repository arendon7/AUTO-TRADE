from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from autotrade.brokers.alpaca_paper_canary import (
    PaperCanaryContext,
    PaperCanaryGate,
    PaperCanaryPolicy,
    PaperCanaryRejected,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionBinding,
    SQLitePaperSubmissionRegistry,
)
from autotrade.domain import OrderIntent, OrderRecord, OrderStatus, OrderType, Side
from autotrade.persistence import SQLiteRuntime


UTC = timezone.utc
NOW = datetime(2026, 8, 11, 7, 30, tzinfo=UTC)
TRACKS = ("R0", "R1", "R2", "R3", "R4", "R5")


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def order(
    *,
    side: Side = Side.BUY,
    order_type: OrderType = OrderType.LIMIT,
    quantity: str = "1",
    limit_price: str | None = "10",
    stop_price: str | None = None,
    status: OrderStatus = OrderStatus.VALIDATED,
    broker_order_id: str | None = None,
) -> OrderRecord:
    intent = OrderIntent(
        intent_id="canary-intent-001",
        strategy_id="canary-strategy",
        symbol="AAPL",
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit_price) if limit_price is not None else None,
        stop_price=Decimal(stop_price) if stop_price is not None else None,
        idempotency_key="canary-idempotency-001",
        created_at=NOW - timedelta(seconds=1),
    )
    return OrderRecord(
        order_id="canary-order-001",
        intent=intent,
        status=status,
        risk_decision_id="canary-risk-001",
        broker_order_id=broker_order_id,
        updated_at=NOW - timedelta(milliseconds=500),
    )


def attestation(*, at: datetime | None = None, portfolio_value: str = "100000") -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="e6fe16f3-64a4-4921-8928-cadf02f92f98",
        account_reference=h("paper-account"),
        credential_reference=h("paper-key-id"),
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal(portfolio_value),
        shorting_enabled=True,
        attested_at=at or NOW,
        request_id="account-request-001",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )


def prepared_components(tmp_path, *, current_order: OrderRecord | None = None, current_attestation: AlpacaPaperAccountAttestation | None = None):
    current_order = current_order or order()
    current_attestation = current_attestation or attestation()
    binding = PaperSubmissionBinding.from_order(
        order=current_order,
        account_attestation_fingerprint=current_attestation.fingerprint,
        order_payload_hash=h("canonical-order-payload"),
        created_at=NOW - timedelta(milliseconds=400),
    )
    registry = SQLitePaperSubmissionRegistry(SQLiteRuntime(tmp_path / "canary.sqlite"))
    state = registry.prepare(binding)
    return current_order, current_attestation, binding, state, registry


def context(tmp_path, **overrides) -> PaperCanaryContext:
    current_order = overrides.pop("order", None)
    current_attestation = overrides.pop("account_attestation", None)
    order_value, attestation_value, binding_value, state_value, _ = prepared_components(
        tmp_path,
        current_order=current_order,
        current_attestation=current_attestation,
    )
    values = {
        "order": order_value,
        "binding": binding_value,
        "submission_state": state_value,
        "account_attestation": attestation_value,
        "now": NOW,
        "certified_tracks": TRACKS,
        "reconciliation_clean": True,
        "unresolved_unknown_orders": 0,
        "kill_switch_engaged": False,
        "health_allows_new_exposure": True,
        "prior_canary_submissions": 0,
    }
    values.update(overrides)
    return PaperCanaryContext(**values)


def enabled_policy(**overrides) -> PaperCanaryPolicy:
    values = {
        "enabled": True,
        "max_notional": Decimal("10"),
        "max_account_fraction": Decimal("0.001"),
        "max_attestation_age_seconds": 30,
        "approval_ttl_seconds": 5,
    }
    values.update(overrides)
    return PaperCanaryPolicy(**values)


def test_canary_is_disabled_by_default(tmp_path) -> None:
    with pytest.raises(PaperCanaryRejected, match="disabled by default"):
        PaperCanaryGate().approve(context(tmp_path))


def test_exact_notional_boundary_is_approved_and_one_cent_above_is_rejected(tmp_path) -> None:
    approval = PaperCanaryGate(enabled_policy()).approve(context(tmp_path))
    assert approval.notional == Decimal("10")
    assert approval.effective_notional_cap == Decimal("10")
    assert len(approval.approval_hash) == 64
    assert approval.is_valid_at(NOW)
    assert not approval.is_valid_at(NOW + timedelta(seconds=5))

    above = order(limit_price="10.01")
    with pytest.raises(PaperCanaryRejected, match="exceeds strict effective cap"):
        PaperCanaryGate(enabled_policy()).approve(context(tmp_path / "above", order=above))


def test_account_fraction_cap_can_be_stricter_than_absolute_cap(tmp_path) -> None:
    small = attestation(portfolio_value="5000")
    # 5000 * 0.001 = 5, stricter than max_notional 10.
    with pytest.raises(PaperCanaryRejected, match="exceeds strict effective cap"):
        PaperCanaryGate(enabled_policy()).approve(
            context(tmp_path, account_attestation=small)
        )


def test_canary_requires_exact_r0_through_r5_certification(tmp_path) -> None:
    for tracks in (
        ("R0", "R1", "R2", "R3", "R4"),
        ("R0", "R1", "R2", "R3", "R4", "R5", "R6"),
        ("R1", "R0", "R2", "R3", "R4", "R5"),
    ):
        with pytest.raises(PaperCanaryRejected, match="certified-track"):
            PaperCanaryGate(enabled_policy()).approve(
                context(tmp_path / h(str(tracks))[:8], certified_tracks=tracks)
            )


@pytest.mark.parametrize(
    "current_order,reason",
    [
        (order(status=OrderStatus.CREATED), "VALIDATED"),
        (order(status=OrderStatus.SUBMITTING), "VALIDATED"),
        (order(side=Side.SELL), "BUY-only"),
        (order(order_type=OrderType.MARKET, limit_price=None), "LIMIT"),
        (order(stop_price="9"), "stop field"),
        (order(broker_order_id="broker-already"), "already bound"),
    ],
)
def test_canary_rejects_nonminimal_order_surface(tmp_path, current_order, reason) -> None:
    # Non-VALIDATED orders cannot create a submission binding; emulate a frozen
    # VALIDATED binding/state and then present a changed operational order to gate.
    if current_order.status is not OrderStatus.VALIDATED:
        base_order, att, bind, state, _ = prepared_components(tmp_path)
        ctx = PaperCanaryContext(
            order=current_order,
            binding=bind,
            submission_state=state,
            account_attestation=att,
            now=NOW,
            certified_tracks=TRACKS,
            reconciliation_clean=True,
            unresolved_unknown_orders=0,
            kill_switch_engaged=False,
            health_allows_new_exposure=True,
            prior_canary_submissions=0,
        )
    else:
        ctx = context(tmp_path, order=current_order)
    with pytest.raises(PaperCanaryRejected, match=reason):
        PaperCanaryGate(enabled_policy()).approve(ctx)


def test_canary_rejects_stale_or_future_account_attestation(tmp_path) -> None:
    stale = attestation(at=NOW - timedelta(seconds=31))
    with pytest.raises(PaperCanaryRejected, match="stale"):
        PaperCanaryGate(enabled_policy()).approve(
            context(tmp_path / "stale", account_attestation=stale)
        )

    future = attestation(at=NOW + timedelta(seconds=1))
    with pytest.raises(PaperCanaryRejected, match="future"):
        PaperCanaryGate(enabled_policy()).approve(
            context(tmp_path / "future", account_attestation=future)
        )


def test_binding_must_match_current_order_attestation_and_submission_state(tmp_path) -> None:
    ctx = context(tmp_path)
    wrong_binding = replace(ctx.binding, order_payload_hash=h("changed-payload"))
    with pytest.raises(PaperCanaryRejected, match="immutable submission binding"):
        PaperCanaryGate(enabled_policy()).approve(replace(ctx, binding=wrong_binding))

    wrong_attestation = replace(ctx.account_attestation, request_id="different-request")
    with pytest.raises(PaperCanaryRejected, match="current PAPER attestation"):
        PaperCanaryGate(enabled_policy()).approve(
            replace(ctx, account_attestation=wrong_attestation)
        )


def test_unknown_submission_state_blocks_canary(tmp_path) -> None:
    order_value, att, bind, _, registry = prepared_components(tmp_path)
    unknown = registry.mark_submit_attempt_unknown(
        order_id=bind.order_id,
        attempt_id="attempt-before-canary",
        now=NOW,
    )
    ctx = PaperCanaryContext(
        order=order_value,
        binding=bind,
        submission_state=unknown,
        account_attestation=att,
        now=NOW,
        certified_tracks=TRACKS,
        reconciliation_clean=True,
        unresolved_unknown_orders=1,
        kill_switch_engaged=False,
        health_allows_new_exposure=True,
        prior_canary_submissions=0,
    )
    with pytest.raises(PaperCanaryRejected) as exc:
        PaperCanaryGate(enabled_policy()).approve(ctx)
    assert "PREPARED" in str(exc.value)
    assert "UNKNOWN" in str(exc.value)


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"reconciliation_clean": False}, "reconciliation"),
        ({"unresolved_unknown_orders": 1}, "UNKNOWN"),
        ({"kill_switch_engaged": True}, "kill switch"),
        ({"health_allows_new_exposure": False}, "Health"),
        ({"prior_canary_submissions": 1}, "budget is exhausted"),
    ],
)
def test_global_safety_predicates_block_canary(tmp_path, overrides, reason) -> None:
    with pytest.raises(PaperCanaryRejected, match=reason):
        PaperCanaryGate(enabled_policy()).approve(context(tmp_path, **overrides))


@pytest.mark.parametrize(
    "policy_overrides",
    [
        {"max_notional": Decimal("0")},
        {"max_notional": Decimal("NaN")},
        {"max_account_fraction": Decimal("0")},
        {"max_account_fraction": Decimal("0.011")},
        {"max_attestation_age_seconds": 0},
        {"max_attestation_age_seconds": 121},
        {"approval_ttl_seconds": 0},
        {"approval_ttl_seconds": 16},
        {"max_prior_canary_submissions": 1},
    ],
)
def test_policy_bounds_fail_closed(policy_overrides) -> None:
    with pytest.raises(ValueError):
        enabled_policy(**policy_overrides)


def test_context_counters_and_time_must_be_valid(tmp_path) -> None:
    base = context(tmp_path)
    with pytest.raises(ValueError):
        replace(base, unresolved_unknown_orders=-1)
    with pytest.raises(ValueError):
        replace(base, prior_canary_submissions=-1)
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(base, now=datetime(2026, 8, 11, 7, 30))


def test_canary_gate_has_no_submit_or_network_surface(tmp_path) -> None:
    gate = PaperCanaryGate(enabled_policy())
    forbidden = {
        "submit",
        "submit_order",
        "post",
        "send",
        "place_order",
        "create_order",
    }
    assert not (forbidden & set(dir(gate)))
    approval = gate.approve(context(tmp_path))
    assert approval.order_id == "canary-order-001"
