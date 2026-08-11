from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_bracket import AlpacaEquityBracketBuilder, PaperEquityVenueRules
from autotrade.brokers.alpaca_paper_canary import PaperCanaryContext, PaperCanaryGate, PaperCanaryPolicy
from autotrade.brokers.alpaca_paper_final_guard import (
    PaperFinalWriteBlocked,
    PaperFinalWriteGuard,
    PaperFinalWritePhase,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation
from autotrade.brokers.alpaca_paper_submission import PaperSubmissionBinding, SQLitePaperSubmissionRegistry
from autotrade.domain import OrderIntent, OrderRecord, OrderStatus, OrderType, PortfolioSnapshot, Side
from autotrade.health_bridge import EffectiveHealthControl, HealthRiskMode
from autotrade.persistence import SQLiteRuntime
from autotrade.state import InMemoryOrderStore, InMemoryPortfolioStore, InMemorySafetyStateStore


UTC = timezone.utc
NOW = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)
TRACKS = ("R0", "R1", "R2", "R3", "R4", "R5")


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class HealthBridge:
    def __init__(self, mode=HealthRiskMode.NORMAL):
        self.mode = mode

    def effective_control(self, *, strategy_id, portfolio_entity_id, now):
        del strategy_id, portfolio_entity_id, now
        multiplier = Decimal("1") if self.mode is HealthRiskMode.NORMAL else Decimal("0.5")
        return EffectiveHealthControl(
            mode=self.mode,
            order_multiplier=multiplier,
            strategy_multiplier=multiplier,
            portfolio_multiplier=multiplier,
            reason="FINAL_WRITE_TEST",
            strategy_state_fingerprint=h("strategy-health"),
            portfolio_state_fingerprint=h("portfolio-health"),
        )


def base_order(order_id="guard-order-001", idempotency="guard-idem-001"):
    return OrderRecord(
        order_id=order_id,
        intent=OrderIntent(
            intent_id=f"intent-{order_id}",
            idempotency_key=idempotency,
            strategy_id="guard-strategy",
            symbol="AAPL",
            side=Side.BUY,
            quantity=Decimal("1"),
            order_type=OrderType.LIMIT,
            created_at=NOW - timedelta(seconds=2),
            limit_price=Decimal("10"),
        ),
        risk_decision_id=f"risk-{order_id}",
        status=OrderStatus.VALIDATED,
        created_at=NOW - timedelta(seconds=1),
    )


def account():
    return AlpacaPaperAccountAttestation(
        account_id="guard-account-001",
        account_reference=h("account"),
        credential_reference=h("credentials"),
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=True,
        attested_at=NOW,
        request_id="guard-account-request-001",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )


def bracket(current_order):
    return AlpacaEquityBracketBuilder().build(
        order=current_order,
        venue_rules=PaperEquityVenueRules(
            symbol="AAPL",
            asset_class="us_equity",
            price_tick=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            instrument_master_fingerprint=h("instrument"),
        ),
        take_profit_price=Decimal("10.50"),
        stop_loss_price=Decimal("9.50"),
    )


def setup(tmp_path, *, health_mode=HealthRiskMode.NORMAL):
    current_order = base_order()
    current_account = account()
    expected = bracket(current_order)
    registry = SQLitePaperSubmissionRegistry(SQLiteRuntime(tmp_path / "submissions.sqlite"))
    binding = PaperSubmissionBinding.from_order(
        order=current_order,
        account_attestation_fingerprint=current_account.fingerprint,
        order_payload_hash=expected.payload_hash,
        created_at=NOW,
    )
    state = registry.prepare(binding)
    approval = PaperCanaryGate(PaperCanaryPolicy(enabled=True)).approve(
        PaperCanaryContext(
            order=current_order,
            binding=binding,
            submission_state=state,
            account_attestation=current_account,
            now=NOW,
            certified_tracks=TRACKS,
            reconciliation_clean=True,
            unresolved_unknown_orders=0,
            kill_switch_engaged=False,
            health_allows_new_exposure=True,
            prior_canary_submissions=0,
        )
    )
    orders = InMemoryOrderStore()
    orders.create_if_absent(current_order)
    orders.update(replace(current_order, status=OrderStatus.SUBMITTING, submitted_at=NOW))
    safety = InMemorySafetyStateStore()
    portfolio = InMemoryPortfolioStore()
    portfolio.initialize(
        PortfolioSnapshot(
            snapshot_id="guard-portfolio-001",
            equity=Decimal("100000"),
            gross_exposure=Decimal("0"),
            net_exposure=Decimal("0"),
            daily_pnl=Decimal("0"),
            drawdown=Decimal("0"),
            open_orders=0,
            signed_position_notional_by_symbol={},
            strategy_gross_exposure={},
            strategy_signed_position_notional_by_symbol={},
            reconciliation_ok=True,
            broker_state_known=True,
        ),
        now=NOW,
    )
    guard = PaperFinalWriteGuard(
        order_store=orders,
        safety_state_store=safety,
        portfolio_store=portfolio,
        health_bridge=HealthBridge(health_mode),
        portfolio_health_entity_id="portfolio-r6-canary",
    )
    return current_order, expected, registry, binding, approval, orders, safety, portfolio, guard


def authorize(values, *, phase=PaperFinalWritePhase.PRE_CONSUME, attempt_id=None):
    current_order, expected, registry, binding, approval, orders, safety, portfolio, guard = values
    return guard.authorize(
        approval=approval,
        expected_bracket=expected,
        submission_registry=registry,
        now=NOW + timedelta(seconds=1),
        phase=phase,
        expected_attempt_id=attempt_id,
    )


def test_preconsume_reads_authoritative_sources_and_hashes_attestation(tmp_path):
    values = setup(tmp_path)
    attested = authorize(values)
    assert attested.phase is PaperFinalWritePhase.PRE_CONSUME
    assert attested.submission_status.value == "PREPARED"
    assert attested.health_mode is HealthRiskMode.NORMAL
    assert len(attested.attestation_hash) == 64


def test_authoritative_oms_must_be_submitting_not_merely_caller_approved(tmp_path):
    values = setup(tmp_path)
    current_order, expected, registry, binding, approval, orders, safety, portfolio, guard = values
    orders.update(current_order)
    with pytest.raises(PaperFinalWriteBlocked, match="SUBMITTING"):
        authorize(values)


def test_kill_switch_and_circuit_are_authoritative_blockers(tmp_path):
    values = setup(tmp_path)
    *_, safety, portfolio, guard = values[0:7] + values[7:]
    values[6].activate(reason="kill", now=NOW)
    with pytest.raises(PaperFinalWriteBlocked, match="kill switch"):
        authorize(values)

    values = setup(tmp_path / "circuit")
    values[6].activate_circuit(reason="circuit", now=NOW)
    with pytest.raises(PaperFinalWriteBlocked, match="circuit"):
        authorize(values)


def test_portfolio_reconciliation_and_known_broker_state_are_required(tmp_path):
    values = setup(tmp_path)
    portfolio = values[7]
    current = portfolio.get()
    portfolio.compare_and_set(
        expected_version=current.version,
        snapshot=replace(current.snapshot, reconciliation_ok=False),
        now=NOW,
    )
    with pytest.raises(PaperFinalWriteBlocked, match="reconciliation"):
        authorize(values)

    values = setup(tmp_path / "unknown-broker")
    portfolio = values[7]
    current = portfolio.get()
    portfolio.compare_and_set(
        expected_version=current.version,
        snapshot=replace(current.snapshot, broker_state_known=False),
        now=NOW,
    )
    with pytest.raises(PaperFinalWriteBlocked, match="broker state"):
        authorize(values)


def test_degraded_health_blocks_even_when_not_no_new_risk(tmp_path):
    values = setup(tmp_path, health_mode=HealthRiskMode.REDUCED)
    with pytest.raises(PaperFinalWriteBlocked, match="Health"):
        authorize(values)


def test_other_attempted_or_unknown_submission_exhausts_first_canary_budget(tmp_path):
    values = setup(tmp_path)
    registry = values[2]
    other = base_order("guard-order-002", "guard-idem-002")
    other_expected = bracket(other)
    other_binding = PaperSubmissionBinding.from_order(
        order=other,
        account_attestation_fingerprint=account().fingerprint,
        order_payload_hash=other_expected.payload_hash,
        created_at=NOW,
    )
    registry.prepare(other_binding)
    registry.mark_submit_attempt_unknown(
        order_id=other.order_id,
        attempt_id="other-attempt",
        now=NOW + timedelta(milliseconds=100),
    )
    with pytest.raises(PaperFinalWriteBlocked, match="another"):
        authorize(values)


def test_pre_io_requires_same_durable_attempt_identity(tmp_path):
    values = setup(tmp_path)
    registry = values[2]
    registry.mark_submit_attempt_unknown(
        order_id=values[0].order_id,
        attempt_id="expected-attempt",
        now=NOW + timedelta(milliseconds=100),
    )
    attested = authorize(
        values,
        phase=PaperFinalWritePhase.PRE_IO,
        attempt_id="expected-attempt",
    )
    assert attested.submission_status.value == "UNKNOWN"
    with pytest.raises(PaperFinalWriteBlocked, match="attempt_id"):
        authorize(
            values,
            phase=PaperFinalWritePhase.PRE_IO,
            attempt_id="different-attempt",
        )


def test_global_snapshot_detects_other_control_tamper(tmp_path):
    values = setup(tmp_path)
    registry = values[2]
    other = base_order("guard-order-002", "guard-idem-002")
    other_binding = PaperSubmissionBinding.from_order(
        order=other,
        account_attestation_fingerprint=account().fingerprint,
        order_payload_hash=bracket(other).payload_hash,
        created_at=NOW,
    )
    registry.prepare(other_binding)
    with sqlite3.connect(registry._runtime.path) as conn:  # adversarial direct corruption
        conn.execute(
            "UPDATE alpaca_paper_submission_control SET status='UNKNOWN' WHERE order_id=?",
            (other.order_id,),
        )
        conn.commit()
    with pytest.raises(PaperFinalWriteBlocked, match="corrupt"):
        authorize(values)
