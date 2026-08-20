from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleBinding,
    CryptoLifecycleIntegrityError,
    CryptoLifecycleStatus,
)
from autotrade.first_canary_fee_aware_recovery import (
    FirstCanaryFeeAwareRecoveryLifecycle,
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


def test_operator_scale_fee_adjusted_position_reconciles_and_keeps_net_exposure(tmp_path) -> None:
    lifecycle, binding = _lifecycle(tmp_path, "fee-ok")
    net = Decimal("0.0001439592")  # 25 bps below the gross fill.
    state = lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-fee-ok",
        broker_status="filled",
        filled_quantity=GROSS,
        confirmed_net_long_quantity=net,
        at=NOW + timedelta(seconds=2),
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
        lifecycle.reconcile_entry(
            binding.lifecycle_id,
            broker_order_id="broker-fee-reject",
            broker_status="filled",
            filled_quantity=GROSS,
            confirmed_net_long_quantity=net,
            at=NOW + timedelta(seconds=2),
        )


def test_generic_lifecycle_remains_strict_and_unmodified(tmp_path) -> None:
    from autotrade.brokers.alpaca_paper_crypto_lifecycle import SQLiteCryptoPaperLifecycle

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
