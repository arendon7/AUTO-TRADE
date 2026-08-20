from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import autotrade.first_canary_recovery as canonical_recovery
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleBinding,
    CryptoLifecycleBlocked,
    CryptoLifecycleIntegrityError,
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from autotrade.first_canary_fee_aware_recovery import (
    FirstCanaryFeeAwareRecoveryError,
    FirstCanaryFeeAwareRecoveryLifecycle,
    _validate_fee_adjusted_net_position,
    recover_first_canary_fee_aware,
)
from autotrade.persistence import SQLiteRuntime


NOW = datetime(2026, 8, 20, 17, 47, tzinfo=timezone.utc)
GROSS = Decimal("0.000144320")


def _lifecycle(tmp_path, lifecycle_id: str):
    binding = CryptoLifecycleBinding(
        lifecycle_id=lifecycle_id,
        account_attestation_fingerprint="a" * 64,
        asset_attestation_fingerprint="b" * 64,
        product_profile_fingerprint="c" * 64,
        symbol="BTC/USD",
        entry_order_fingerprint="d" * 64,
        entry_client_order_id="atr6c-entry-" + lifecycle_id,
        entry_quantity=GROSS,
        created_at=NOW,
    )
    lifecycle = FirstCanaryFeeAwareRecoveryLifecycle(
        SQLiteRuntime(tmp_path / f"{lifecycle_id}.sqlite3")
    )
    lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(
        lifecycle_id, at=NOW + timedelta(seconds=1)
    )
    return lifecycle, binding


def _reconcile(
    lifecycle,
    binding,
    *,
    status: str,
    filled: Decimal,
    net: Decimal,
    broker_order_id: str = "broker-order",
    seconds: int = 2,
):
    return lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id=broker_order_id,
        broker_status=status,
        filled_quantity=filled,
        confirmed_net_long_quantity=net,
        at=NOW + timedelta(seconds=seconds),
    )


def test_operator_scale_fee_adjusted_position_reconciles_and_keeps_net_exposure(tmp_path) -> None:
    lifecycle, binding = _lifecycle(tmp_path, "fee-ok")
    net = Decimal("0.0001439592")  # 25 bps below the gross fill.
    state = _reconcile(
        lifecycle,
        binding,
        status="filled",
        filled=GROSS,
        net=net,
        broker_order_id="broker-fee-ok",
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED
    assert state.entry_filled_quantity == GROSS
    assert state.confirmed_net_long_quantity == net


@pytest.mark.parametrize(
    ("net", "message"),
    [
        (Decimal("0"), "positive entry fill requires positive"),
        (Decimal("0.000144321"), "may not exceed cumulative entry fills"),
        (Decimal("0.0001430"), "fee allowance"),
    ],
)
def test_fee_adapter_rejects_impossible_or_excessive_position_gap(
    tmp_path, net: Decimal, message: str
) -> None:
    lifecycle, binding = _lifecycle(
        tmp_path, "fee-reject-" + str(net).replace(".", "-")
    )
    with pytest.raises(CryptoLifecycleIntegrityError, match=message):
        _reconcile(
            lifecycle,
            binding,
            status="filled",
            filled=GROSS,
            net=net,
            broker_order_id="broker-fee-reject",
        )


def test_fee_validation_zero_fill_requires_zero_position() -> None:
    _validate_fee_adjusted_net_position(
        filled_quantity=Decimal("0"), confirmed_net_long_quantity=Decimal("0")
    )
    with pytest.raises(CryptoLifecycleIntegrityError, match="zero entry fill requires zero"):
        _validate_fee_adjusted_net_position(
            filled_quantity=Decimal("0"),
            confirmed_net_long_quantity=Decimal("0.000000001"),
        )


def test_terminal_no_fill_and_open_zero_fill_states(tmp_path) -> None:
    lifecycle, binding = _lifecycle(tmp_path, "terminal-no-fill")
    state = _reconcile(
        lifecycle,
        binding,
        status="canceled",
        filled=Decimal("0"),
        net=Decimal("0"),
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_TERMINAL_NO_FILL
    assert state.entry_terminal is True

    lifecycle2, binding2 = _lifecycle(tmp_path, "open-zero-fill")
    state2 = _reconcile(
        lifecycle2,
        binding2,
        status="accepted",
        filled=Decimal("0"),
        net=Decimal("0"),
    )
    assert state2.status is CryptoLifecycleStatus.ENTRY_ACKNOWLEDGED
    assert state2.entry_terminal is False


def test_partial_fill_state_uses_net_exposure(tmp_path) -> None:
    lifecycle, binding = _lifecycle(tmp_path, "partial")
    partial = GROSS / Decimal("2")
    net = partial - Decimal("0.000000001")
    state = _reconcile(
        lifecycle,
        binding,
        status="partially_filled",
        filled=partial,
        net=net,
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_PARTIALLY_FILLED
    assert state.entry_filled_quantity == partial
    assert state.confirmed_net_long_quantity == net


@pytest.mark.parametrize(
    ("status", "filled", "net", "message"),
    [
        ("mystery", Decimal("0"), Decimal("0"), "unsupported entry broker status"),
        ("filled", GROSS + Decimal("0.000000001"), GROSS, "exceeds intended quantity"),
        ("filled", GROSS / Decimal("2"), GROSS / Decimal("2"), "requires exact intended quantity"),
        ("accepted", Decimal("0.000000001"), Decimal("0.000000001"), "may not report cumulative fill"),
        ("partially_filled", Decimal("0"), Decimal("0"), "strict partial quantity"),
        ("partially_filled", GROSS, GROSS, "strict partial quantity"),
    ],
)
def test_reconcile_rejects_invalid_broker_state_combinations(
    tmp_path, status: str, filled: Decimal, net: Decimal, message: str
) -> None:
    lifecycle, binding = _lifecycle(tmp_path, "invalid-" + status + "-" + str(filled))
    with pytest.raises(CryptoLifecycleIntegrityError, match=message):
        _reconcile(
            lifecycle,
            binding,
            status=status,
            filled=filled,
            net=net,
        )


@pytest.mark.parametrize(
    ("filled", "net", "message"),
    [
        (Decimal("-0.000000001"), Decimal("0"), "filled_quantity"),
        (Decimal("0"), Decimal("-0.000000001"), "confirmed_net_long_quantity"),
    ],
)
def test_reconcile_rejects_negative_quantities(
    tmp_path, filled: Decimal, net: Decimal, message: str
) -> None:
    lifecycle, binding = _lifecycle(tmp_path, "negative-" + message)
    with pytest.raises((ValueError, CryptoLifecycleIntegrityError), match=message):
        _reconcile(
            lifecycle,
            binding,
            status="accepted",
            filled=filled,
            net=net,
        )


def test_reconcile_rejects_broker_order_id_change_and_fill_regression(tmp_path) -> None:
    lifecycle, binding = _lifecycle(tmp_path, "broker-id-change")
    _reconcile(
        lifecycle,
        binding,
        status="accepted",
        filled=Decimal("0"),
        net=Decimal("0"),
        broker_order_id="broker-a",
    )
    with pytest.raises(CryptoLifecycleIntegrityError, match="broker order id changed"):
        _reconcile(
            lifecycle,
            binding,
            status="accepted",
            filled=Decimal("0"),
            net=Decimal("0"),
            broker_order_id="broker-b",
            seconds=3,
        )

    lifecycle2, binding2 = _lifecycle(tmp_path, "fill-regression")
    partial = GROSS / Decimal("2")
    _reconcile(
        lifecycle2,
        binding2,
        status="partially_filled",
        filled=partial,
        net=partial,
        broker_order_id="broker-regression",
    )
    with pytest.raises(CryptoLifecycleIntegrityError, match="filled quantity regressed"):
        _reconcile(
            lifecycle2,
            binding2,
            status="partially_filled",
            filled=partial / Decimal("2"),
            net=partial / Decimal("2"),
            broker_order_id="broker-regression",
            seconds=3,
        )


def test_reconcile_rejects_invalid_current_lifecycle_state(tmp_path) -> None:
    lifecycle, binding = _lifecycle(tmp_path, "invalid-current-state")
    _reconcile(
        lifecycle,
        binding,
        status="canceled",
        filled=Decimal("0"),
        net=Decimal("0"),
    )
    with pytest.raises(CryptoLifecycleBlocked, match="not valid from current lifecycle state"):
        _reconcile(
            lifecycle,
            binding,
            status="canceled",
            filled=Decimal("0"),
            net=Decimal("0"),
            seconds=3,
        )


def test_generic_lifecycle_remains_strict_and_unmodified(tmp_path) -> None:
    binding = CryptoLifecycleBinding(
        lifecycle_id="generic-stays-strict",
        account_attestation_fingerprint="a" * 64,
        asset_attestation_fingerprint="b" * 64,
        product_profile_fingerprint="c" * 64,
        symbol="BTC/USD",
        entry_order_fingerprint="d" * 64,
        entry_client_order_id="atr6c-entry-generic-strict",
        entry_quantity=GROSS,
        created_at=NOW,
    )
    lifecycle = SQLiteCryptoPaperLifecycle(SQLiteRuntime(tmp_path / "generic.sqlite3"))
    lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=1))
    with pytest.raises(CryptoLifecycleIntegrityError, match="equal cumulative entry fills"):
        lifecycle.reconcile_entry(
            binding.lifecycle_id,
            broker_order_id="broker-generic",
            broker_status="filled",
            filled_quantity=GROSS,
            confirmed_net_long_quantity=Decimal("0.0001439592"),
            at=NOW + timedelta(seconds=2),
        )


def test_recovery_wrapper_temporarily_swaps_only_the_canonical_lifecycle(monkeypatch) -> None:
    seen = {}

    def fake_recover_first_canary(**kwargs):
        seen["lifecycle"] = canonical_recovery.SQLiteCryptoPaperLifecycle
        seen["kwargs"] = kwargs
        return "recovered"

    monkeypatch.setattr(canonical_recovery, "recover_first_canary", fake_recover_first_canary)
    original = canonical_recovery.SQLiteCryptoPaperLifecycle
    result = recover_first_canary_fee_aware(marker="same-burned-attempt")

    assert result == "recovered"
    assert seen["lifecycle"] is FirstCanaryFeeAwareRecoveryLifecycle
    assert seen["kwargs"] == {"marker": "same-burned-attempt"}
    assert canonical_recovery.SQLiteCryptoPaperLifecycle is original


def test_recovery_wrapper_restores_canonical_lifecycle_when_recovery_raises(monkeypatch) -> None:
    original = canonical_recovery.SQLiteCryptoPaperLifecycle

    def explode(**_kwargs):
        assert canonical_recovery.SQLiteCryptoPaperLifecycle is FirstCanaryFeeAwareRecoveryLifecycle
        raise RuntimeError("broker read failed")

    monkeypatch.setattr(canonical_recovery, "recover_first_canary", explode)
    with pytest.raises(RuntimeError, match="broker read failed"):
        recover_first_canary_fee_aware()
    assert canonical_recovery.SQLiteCryptoPaperLifecycle is original


def test_recovery_wrapper_fails_closed_if_canonical_lifecycle_was_replaced(monkeypatch) -> None:
    class UnexpectedLifecycle:
        pass

    monkeypatch.setattr(canonical_recovery, "SQLiteCryptoPaperLifecycle", UnexpectedLifecycle)
    with pytest.raises(FirstCanaryFeeAwareRecoveryError, match="unexpectedly replaced"):
        recover_first_canary_fee_aware()
