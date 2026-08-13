from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleBinding,
    CryptoLifecycleBlocked,
    CryptoLifecycleIntegrityError,
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from autotrade.brokers.alpaca_paper_crypto_order import (
    CryptoOrderRole,
    build_crypto_entry_order,
    build_crypto_long_protection_order,
    deterministic_crypto_client_order_id,
)
from autotrade.persistence import SQLiteRuntime
from autotrade.product_profile import BrokerOrderType, ProductCapabilities, TimeInForce


NOW = datetime(2026, 8, 13, 3, 30, tzinfo=timezone.utc)


def asset() -> AlpacaPaperCryptoAssetAttestation:
    return AlpacaPaperCryptoAssetAttestation(
        symbol="BTC/USD",
        asset_id="276e2673-764b-4ab6-a611-caf665ca6340",
        asset_class="crypto",
        exchange="CRYPTO",
        status="active",
        tradable=True,
        fractionable=True,
        marginable=False,
        shortable=False,
        min_order_size=Decimal("0.0001"),
        min_trade_increment=Decimal("0.0001"),
        price_increment=Decimal("1"),
        account_attestation_fingerprint="a" * 64,
        credential_reference="b" * 64,
        observed_at=NOW,
        request_id="req-crypto",
        response_sha256="c" * 64,
        source_path="/v2/assets/BTC%2FUSD",
    )


def profile(a: AlpacaPaperCryptoAssetAttestation) -> ProductCapabilities:
    return ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=a.fingerprint,
        observed_at=a.observed_at,
        fractionable=a.fractionable,
        marginable=a.marginable,
        shortable=a.shortable,
    )


def setup_lifecycle(tmp_path):
    a = asset()
    p = profile(a)
    lifecycle_id = "crypto-life-001"
    entry = build_crypto_entry_order(
        symbol="BTC/USD",
        quantity=Decimal("0.0010"),
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=lifecycle_id, role=CryptoOrderRole.ENTRY
        ),
        product_profile=p,
        asset_attestation=a,
        limit_price=Decimal("100000"),
    )
    binding = CryptoLifecycleBinding(
        lifecycle_id=lifecycle_id,
        account_attestation_fingerprint="d" * 64,
        asset_attestation_fingerprint=a.fingerprint,
        product_profile_fingerprint=p.fingerprint,
        symbol="BTC/USD",
        entry_order_fingerprint=entry.fingerprint,
        entry_client_order_id=entry.client_order_id,
        entry_quantity=entry.quantity,
        created_at=NOW,
    )
    runtime = SQLiteRuntime(tmp_path / "crypto.sqlite3")
    lifecycle = SQLiteCryptoPaperLifecycle(runtime)
    return lifecycle, runtime, a, p, binding


def test_full_crypto_lifecycle_is_unknown_before_each_external_attempt_and_finishes_flat(tmp_path) -> None:
    lifecycle, _runtime, a, p, binding = setup_lifecycle(tmp_path)
    state = lifecycle.prepare(binding)
    assert state.status is CryptoLifecycleStatus.ENTRY_PREPARED
    assert state.restart_action == "CONTINUE_CERTIFIED_LIFECYCLE"

    state = lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=1))
    assert state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    assert state.entry_attempt_count == 1
    assert state.restart_action == "RECONCILE_ONLY"

    state = lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-entry-1",
        broker_status="filled",
        filled_quantity=Decimal("0.0010"),
        confirmed_net_long_quantity=Decimal("0.0010"),
        at=NOW + timedelta(seconds=2),
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED
    assert state.entry_terminal is True
    assert state.restart_action == "REDUCE_RISK_OR_PROTECT"

    protection = build_crypto_long_protection_order(
        symbol="BTC/USD",
        confirmed_entry_filled_quantity=state.entry_filled_quantity,
        confirmed_net_long_quantity=state.confirmed_net_long_quantity,
        requested_protection_quantity=state.confirmed_net_long_quantity,
        stop_price=Decimal("95000"),
        limit_price=Decimal("94500"),
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=binding.lifecycle_id, role=CryptoOrderRole.PROTECTION
        ),
        product_profile=p,
        asset_attestation=a,
    )
    state = lifecycle.prepare_protection(
        binding.lifecycle_id, order=protection, at=NOW + timedelta(seconds=3)
    )
    assert state.status is CryptoLifecycleStatus.PROTECTION_PREPARED

    state = lifecycle.mark_protection_submission_unknown(
        binding.lifecycle_id, at=NOW + timedelta(seconds=4)
    )
    assert state.status is CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN
    assert state.protection_attempt_count == 1
    assert state.restart_action == "RECONCILE_ONLY"

    state = lifecycle.reconcile_protection(
        binding.lifecycle_id,
        broker_order_id="broker-protect-1",
        broker_status="new",
        filled_quantity=Decimal("0"),
        confirmed_net_long_quantity=Decimal("0.0010"),
        at=NOW + timedelta(seconds=5),
    )
    assert state.status is CryptoLifecycleStatus.PROTECTED_OPEN
    assert state.restart_action == "MONITOR_AND_RECONCILE"

    state = lifecycle.reconcile_protection(
        binding.lifecycle_id,
        broker_order_id="broker-protect-1",
        broker_status="partially_filled",
        filled_quantity=Decimal("0.0004"),
        confirmed_net_long_quantity=Decimal("0.0006"),
        at=NOW + timedelta(seconds=6),
    )
    assert state.status is CryptoLifecycleStatus.PROTECTION_PARTIALLY_FILLED

    state = lifecycle.reconcile_protection(
        binding.lifecycle_id,
        broker_order_id="broker-protect-1",
        broker_status="filled",
        filled_quantity=Decimal("0.0010"),
        confirmed_net_long_quantity=Decimal("0"),
        at=NOW + timedelta(seconds=7),
    )
    assert state.status is CryptoLifecycleStatus.FLAT_RECONCILED
    assert state.restart_action == "IDLE"
    snapshot = lifecycle.snapshot(binding.lifecycle_id)
    assert snapshot.state.status is CryptoLifecycleStatus.FLAT_RECONCILED
    assert len(snapshot.events) == 8


def test_unknown_entry_never_allows_second_submission_attempt(tmp_path) -> None:
    lifecycle, _runtime, _a, _p, binding = setup_lifecycle(tmp_path)
    lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=1))
    with pytest.raises(CryptoLifecycleBlocked, match="exactly once"):
        lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=2))
    assert lifecycle.snapshot(binding.lifecycle_id).state.restart_action == "RECONCILE_ONLY"


def test_partial_entry_must_be_terminal_before_opposing_sell_protection(tmp_path) -> None:
    lifecycle, _runtime, a, p, binding = setup_lifecycle(tmp_path)
    lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=1))
    state = lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-entry-1",
        broker_status="partially_filled",
        filled_quantity=Decimal("0.0004"),
        confirmed_net_long_quantity=Decimal("0.0004"),
        at=NOW + timedelta(seconds=2),
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_PARTIALLY_FILLED
    assert state.entry_terminal is False

    protection = build_crypto_long_protection_order(
        symbol="BTC/USD",
        confirmed_entry_filled_quantity=state.entry_filled_quantity,
        confirmed_net_long_quantity=state.confirmed_net_long_quantity,
        requested_protection_quantity=state.confirmed_net_long_quantity,
        stop_price=Decimal("95000"),
        limit_price=Decimal("94500"),
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=binding.lifecycle_id, role=CryptoOrderRole.PROTECTION
        ),
        product_profile=p,
        asset_attestation=a,
    )
    with pytest.raises(CryptoLifecycleBlocked, match="terminal reconciled entry"):
        lifecycle.prepare_protection(
            binding.lifecycle_id, order=protection, at=NOW + timedelta(seconds=3)
        )

    state = lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-entry-1",
        broker_status="canceled",
        filled_quantity=Decimal("0.0004"),
        confirmed_net_long_quantity=Decimal("0.0004"),
        at=NOW + timedelta(seconds=4),
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED
    assert state.entry_terminal is True
    state = lifecycle.prepare_protection(
        binding.lifecycle_id, order=protection, at=NOW + timedelta(seconds=5)
    )
    assert state.status is CryptoLifecycleStatus.PROTECTION_PREPARED


def test_first_canary_protection_must_cover_exact_confirmed_position(tmp_path) -> None:
    lifecycle, _runtime, a, p, binding = setup_lifecycle(tmp_path)
    lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=1))
    state = lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-entry-1",
        broker_status="filled",
        filled_quantity=Decimal("0.0010"),
        confirmed_net_long_quantity=Decimal("0.0010"),
        at=NOW + timedelta(seconds=2),
    )
    protection = build_crypto_long_protection_order(
        symbol="BTC/USD",
        confirmed_entry_filled_quantity=state.entry_filled_quantity,
        confirmed_net_long_quantity=state.confirmed_net_long_quantity,
        requested_protection_quantity=Decimal("0.0005"),
        stop_price=Decimal("95000"),
        limit_price=Decimal("94500"),
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=binding.lifecycle_id, role=CryptoOrderRole.PROTECTION
        ),
        product_profile=p,
        asset_attestation=a,
    )
    with pytest.raises(CryptoLifecycleBlocked, match="exactly the confirmed"):
        lifecycle.prepare_protection(
            binding.lifecycle_id, order=protection, at=NOW + timedelta(seconds=3)
        )


def test_stop_limit_trigger_with_remaining_position_is_explicit_risk_state(tmp_path) -> None:
    lifecycle, _runtime, a, p, binding = setup_lifecycle(tmp_path)
    lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=1))
    state = lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-entry-1",
        broker_status="filled",
        filled_quantity=Decimal("0.0010"),
        confirmed_net_long_quantity=Decimal("0.0010"),
        at=NOW + timedelta(seconds=2),
    )
    protection = build_crypto_long_protection_order(
        symbol="BTC/USD",
        confirmed_entry_filled_quantity=state.entry_filled_quantity,
        confirmed_net_long_quantity=state.confirmed_net_long_quantity,
        requested_protection_quantity=state.confirmed_net_long_quantity,
        stop_price=Decimal("95000"),
        limit_price=Decimal("94500"),
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=binding.lifecycle_id, role=CryptoOrderRole.PROTECTION
        ),
        product_profile=p,
        asset_attestation=a,
    )
    lifecycle.prepare_protection(binding.lifecycle_id, order=protection, at=NOW + timedelta(seconds=3))
    lifecycle.mark_protection_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=4))
    lifecycle.reconcile_protection(
        binding.lifecycle_id,
        broker_order_id="broker-protect-1",
        broker_status="new",
        filled_quantity=Decimal("0"),
        confirmed_net_long_quantity=Decimal("0.0010"),
        at=NOW + timedelta(seconds=5),
    )
    state = lifecycle.mark_protection_triggered_unfilled(
        binding.lifecycle_id, at=NOW + timedelta(seconds=6)
    )
    assert state.status is CryptoLifecycleStatus.PROTECTION_AT_RISK
    assert state.restart_action == "REDUCE_RISK_OR_PROTECT"


def test_reconciliation_detects_position_or_fill_inconsistency(tmp_path) -> None:
    lifecycle, _runtime, _a, _p, binding = setup_lifecycle(tmp_path)
    lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=1))
    with pytest.raises(CryptoLifecycleIntegrityError, match="net long position"):
        lifecycle.reconcile_entry(
            binding.lifecycle_id,
            broker_order_id="broker-entry-1",
            broker_status="partially_filled",
            filled_quantity=Decimal("0.0004"),
            confirmed_net_long_quantity=Decimal("0.0003"),
            at=NOW + timedelta(seconds=2),
        )


def test_restart_integrity_detects_control_or_event_tampering(tmp_path) -> None:
    lifecycle, runtime, _a, _p, binding = setup_lifecycle(tmp_path)
    lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=1))

    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_lifecycle_control SET control_hash = ? WHERE lifecycle_id = ?",
            ("0" * 64, binding.lifecycle_id),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoLifecycleIntegrityError, match="control hash"):
        SQLiteCryptoPaperLifecycle(runtime).snapshot(binding.lifecycle_id)


def test_terminal_entry_without_fill_is_safe_idle_exposure(tmp_path) -> None:
    lifecycle, _runtime, _a, _p, binding = setup_lifecycle(tmp_path)
    lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=1))
    state = lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-entry-1",
        broker_status="canceled",
        filled_quantity=Decimal("0"),
        confirmed_net_long_quantity=Decimal("0"),
        at=NOW + timedelta(seconds=2),
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_TERMINAL_NO_FILL
    state = lifecycle.reconcile_flat(
        binding.lifecycle_id, open_order_count=0, at=NOW + timedelta(seconds=3)
    )
    assert state.status is CryptoLifecycleStatus.FLAT_RECONCILED
