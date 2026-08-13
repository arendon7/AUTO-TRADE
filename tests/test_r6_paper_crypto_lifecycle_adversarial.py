from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleBinding,
    CryptoLifecycleBlocked,
    CryptoLifecycleConflict,
    CryptoLifecycleIntegrityError,
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from autotrade.brokers.alpaca_paper_crypto_order import (
    AlpacaPaperCryptoOrderRequest,
    CryptoOrderRole,
    build_crypto_entry_order,
    build_crypto_long_protection_order,
    deterministic_crypto_client_order_id,
)
from autotrade.persistence import SQLiteRuntime
from autotrade.product_profile import BrokerOrderType, ProductCapabilities, TimeInForce


NOW = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)


def _asset(
    *,
    symbol: str = "BTC/USD",
    response_hash: str = "c" * 64,
) -> AlpacaPaperCryptoAssetAttestation:
    return AlpacaPaperCryptoAssetAttestation(
        symbol=symbol,
        asset_id="asset-" + symbol.replace("/", "-"),
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
        request_id="req-" + symbol.replace("/", "-"),
        response_sha256=response_hash,
        source_path="/v2/assets/" + symbol.replace("/", "%2F"),
    )


def _profile(asset: AlpacaPaperCryptoAssetAttestation) -> ProductCapabilities:
    return ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )


def _setup(tmp_path, *, lifecycle_id: str = "crypto-adv-001"):
    asset = _asset()
    profile = _profile(asset)
    entry = build_crypto_entry_order(
        symbol="BTC/USD",
        quantity=Decimal("0.0010"),
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=lifecycle_id,
            role=CryptoOrderRole.ENTRY,
        ),
        product_profile=profile,
        asset_attestation=asset,
        limit_price=Decimal("100000"),
    )
    binding = CryptoLifecycleBinding(
        lifecycle_id=lifecycle_id,
        account_attestation_fingerprint="d" * 64,
        asset_attestation_fingerprint=asset.fingerprint,
        product_profile_fingerprint=profile.fingerprint,
        symbol="BTC/USD",
        entry_order_fingerprint=entry.fingerprint,
        entry_client_order_id=entry.client_order_id,
        entry_quantity=entry.quantity,
        created_at=NOW,
    )
    runtime = SQLiteRuntime(tmp_path / f"{lifecycle_id}.sqlite3")
    lifecycle = SQLiteCryptoPaperLifecycle(runtime)
    return lifecycle, runtime, asset, profile, entry, binding


def _entry_unknown(lifecycle: SQLiteCryptoPaperLifecycle, binding: CryptoLifecycleBinding) -> None:
    lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=1))


def _entry_filled(lifecycle: SQLiteCryptoPaperLifecycle, binding: CryptoLifecycleBinding):
    _entry_unknown(lifecycle, binding)
    return lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-entry-1",
        broker_status="filled",
        filled_quantity=binding.entry_quantity,
        confirmed_net_long_quantity=binding.entry_quantity,
        at=NOW + timedelta(seconds=2),
    )


def _protection(
    *,
    asset: AlpacaPaperCryptoAssetAttestation,
    profile: ProductCapabilities,
    binding: CryptoLifecycleBinding,
    quantity: Decimal,
) -> AlpacaPaperCryptoOrderRequest:
    return build_crypto_long_protection_order(
        symbol="BTC/USD",
        confirmed_entry_filled_quantity=quantity,
        confirmed_net_long_quantity=quantity,
        requested_protection_quantity=quantity,
        stop_price=Decimal("95000"),
        limit_price=Decimal("94500"),
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=binding.lifecycle_id,
            role=CryptoOrderRole.PROTECTION,
        ),
        product_profile=profile,
        asset_attestation=asset,
    )


def _protection_unknown(lifecycle, asset, profile, binding):
    state = _entry_filled(lifecycle, binding)
    order = _protection(
        asset=asset,
        profile=profile,
        binding=binding,
        quantity=state.confirmed_net_long_quantity,
    )
    lifecycle.prepare_protection(binding.lifecycle_id, order=order, at=NOW + timedelta(seconds=3))
    lifecycle.mark_protection_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=4))
    return order


def test_prepare_is_idempotent_but_immutable_binding_and_client_id_are_single_owner(tmp_path) -> None:
    lifecycle, _runtime, _asset_value, _profile_value, _entry, binding = _setup(tmp_path)
    first = lifecycle.prepare(binding)
    second = lifecycle.prepare(binding)
    assert second == first

    with pytest.raises(CryptoLifecycleConflict, match="different immutable data"):
        lifecycle.prepare(replace(binding, entry_quantity=Decimal("0.0020")))

    other = replace(binding, lifecycle_id="crypto-adv-002")
    with pytest.raises(CryptoLifecycleConflict, match="client_order_id"):
        lifecycle.prepare(other)


@pytest.mark.parametrize("broker_status", ["accepted", "pending_new", "new"])
def test_entry_open_ack_states_require_zero_fill(tmp_path, broker_status: str) -> None:
    lifecycle, _runtime, _asset_value, _profile_value, _entry, binding = _setup(
        tmp_path, lifecycle_id=f"entry-open-{broker_status}"
    )
    _entry_unknown(lifecycle, binding)
    state = lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-entry-open",
        broker_status=broker_status,
        filled_quantity=Decimal("0"),
        confirmed_net_long_quantity=Decimal("0"),
        at=NOW + timedelta(seconds=2),
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_ACKNOWLEDGED
    assert state.entry_terminal is False


@pytest.mark.parametrize(
    "broker_status,filled,net,match",
    [
        ("mystery", Decimal("0"), Decimal("0"), "unsupported entry broker status"),
        ("filled", Decimal("0.0009"), Decimal("0.0009"), "exact intended quantity"),
        ("new", Decimal("0.0001"), Decimal("0.0001"), "unfilled entry status"),
        ("partially_filled", Decimal("0"), Decimal("0"), "strict partial quantity"),
        ("partially_filled", Decimal("0.0010"), Decimal("0.0010"), "strict partial quantity"),
        ("partially_filled", Decimal("0.0011"), Decimal("0.0011"), "exceeds intended quantity"),
    ],
)
def test_entry_reconciliation_rejects_impossible_broker_states(
    tmp_path,
    broker_status: str,
    filled: Decimal,
    net: Decimal,
    match: str,
) -> None:
    lifecycle, _runtime, _asset_value, _profile_value, _entry, binding = _setup(
        tmp_path, lifecycle_id="entry-impossible-" + broker_status.replace("_", "-") + "-" + str(filled).replace(".", "-")
    )
    _entry_unknown(lifecycle, binding)
    with pytest.raises(CryptoLifecycleIntegrityError, match=match):
        lifecycle.reconcile_entry(
            binding.lifecycle_id,
            broker_order_id="broker-entry-impossible",
            broker_status=broker_status,
            filled_quantity=filled,
            confirmed_net_long_quantity=net,
            at=NOW + timedelta(seconds=2),
        )


def test_entry_reconciliation_rejects_broker_id_change_and_fill_regression(tmp_path) -> None:
    lifecycle, _runtime, _asset_value, _profile_value, _entry, binding = _setup(tmp_path)
    _entry_unknown(lifecycle, binding)
    lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-entry-1",
        broker_status="partially_filled",
        filled_quantity=Decimal("0.0005"),
        confirmed_net_long_quantity=Decimal("0.0005"),
        at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(CryptoLifecycleIntegrityError, match="broker order id changed"):
        lifecycle.reconcile_entry(
            binding.lifecycle_id,
            broker_order_id="broker-entry-2",
            broker_status="partially_filled",
            filled_quantity=Decimal("0.0006"),
            confirmed_net_long_quantity=Decimal("0.0006"),
            at=NOW + timedelta(seconds=3),
        )
    with pytest.raises(CryptoLifecycleIntegrityError, match="fill.*regressed"):
        lifecycle.reconcile_entry(
            binding.lifecycle_id,
            broker_order_id="broker-entry-1",
            broker_status="partially_filled",
            filled_quantity=Decimal("0.0004"),
            confirmed_net_long_quantity=Decimal("0.0004"),
            at=NOW + timedelta(seconds=4),
        )


def test_protection_requires_protection_role_and_exact_lifecycle_identity(tmp_path) -> None:
    lifecycle, _runtime, asset, profile, entry, binding = _setup(tmp_path)
    state = _entry_filled(lifecycle, binding)
    with pytest.raises(CryptoLifecycleBlocked, match="PROTECTION order"):
        lifecycle.prepare_protection(binding.lifecycle_id, order=entry, at=NOW + timedelta(seconds=3))

    valid = _protection(asset=asset, profile=profile, binding=binding, quantity=state.confirmed_net_long_quantity)
    with pytest.raises(CryptoLifecycleIntegrityError, match="symbol differs"):
        lifecycle.prepare_protection(
            binding.lifecycle_id,
            order=replace(valid, symbol="ETH/USD"),
            at=NOW + timedelta(seconds=3),
        )
    with pytest.raises(CryptoLifecycleIntegrityError, match="asset evidence differs"):
        lifecycle.prepare_protection(
            binding.lifecycle_id,
            order=replace(valid, asset_attestation_fingerprint="e" * 64),
            at=NOW + timedelta(seconds=4),
        )
    with pytest.raises(CryptoLifecycleIntegrityError, match="product profile differs"):
        lifecycle.prepare_protection(
            binding.lifecycle_id,
            order=replace(valid, product_profile_fingerprint="f" * 64),
            at=NOW + timedelta(seconds=5),
        )


def test_protection_submission_unknown_is_one_shot_and_restart_is_reconcile_only(tmp_path) -> None:
    lifecycle, _runtime, asset, profile, _entry, binding = _setup(tmp_path)
    _protection_unknown(lifecycle, asset, profile, binding)
    with pytest.raises(CryptoLifecycleBlocked, match="exactly once"):
        lifecycle.mark_protection_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=5))
    assert lifecycle.snapshot(binding.lifecycle_id).state.restart_action == "RECONCILE_ONLY"


@pytest.mark.parametrize("broker_status", ["canceled", "expired", "rejected"])
def test_failed_or_removed_protection_with_remaining_position_is_at_risk(tmp_path, broker_status: str) -> None:
    lifecycle, _runtime, asset, profile, _entry, binding = _setup(
        tmp_path, lifecycle_id=f"protect-risk-{broker_status}"
    )
    _protection_unknown(lifecycle, asset, profile, binding)
    state = lifecycle.reconcile_protection(
        binding.lifecycle_id,
        broker_order_id="broker-protect-risk",
        broker_status=broker_status,
        filled_quantity=Decimal("0"),
        confirmed_net_long_quantity=Decimal("0.0010"),
        at=NOW + timedelta(seconds=5),
    )
    assert state.status is CryptoLifecycleStatus.PROTECTION_AT_RISK
    assert state.restart_action == "REDUCE_RISK_OR_PROTECT"


def test_terminal_protection_with_full_fill_can_reconcile_flat_even_if_status_is_canceled(tmp_path) -> None:
    lifecycle, _runtime, asset, profile, _entry, binding = _setup(tmp_path)
    _protection_unknown(lifecycle, asset, profile, binding)
    state = lifecycle.reconcile_protection(
        binding.lifecycle_id,
        broker_order_id="broker-protect-canceled-after-fill",
        broker_status="canceled",
        filled_quantity=Decimal("0.0010"),
        confirmed_net_long_quantity=Decimal("0"),
        at=NOW + timedelta(seconds=5),
    )
    assert state.status is CryptoLifecycleStatus.FLAT_RECONCILED


@pytest.mark.parametrize(
    "broker_status,filled,net,match",
    [
        ("mystery", Decimal("0"), Decimal("0.0010"), "unsupported protection broker status"),
        ("filled", Decimal("0.0009"), Decimal("0.0001"), "filled protection must reconcile account flat"),
        ("partially_filled", Decimal("0"), Decimal("0.0010"), "strict partial quantity"),
        ("partially_filled", Decimal("0.0010"), Decimal("0"), "strict partial quantity"),
        ("new", Decimal("0.0001"), Decimal("0.0009"), "open protection status with fills"),
        ("partially_filled", Decimal("0.0011"), Decimal("0"), "exceeds protected quantity"),
        ("partially_filled", Decimal("0.0004"), Decimal("0.0007"), "position disagree"),
    ],
)
def test_protection_reconciliation_rejects_impossible_broker_states(
    tmp_path,
    broker_status: str,
    filled: Decimal,
    net: Decimal,
    match: str,
) -> None:
    lifecycle, _runtime, asset, profile, _entry, binding = _setup(
        tmp_path, lifecycle_id="protect-impossible-" + broker_status.replace("_", "-") + "-" + str(filled).replace(".", "-")
    )
    _protection_unknown(lifecycle, asset, profile, binding)
    with pytest.raises(CryptoLifecycleIntegrityError, match=match):
        lifecycle.reconcile_protection(
            binding.lifecycle_id,
            broker_order_id="broker-protect-impossible",
            broker_status=broker_status,
            filled_quantity=filled,
            confirmed_net_long_quantity=net,
            at=NOW + timedelta(seconds=5),
        )


def test_protection_reconciliation_rejects_id_change_and_fill_regression(tmp_path) -> None:
    lifecycle, _runtime, asset, profile, _entry, binding = _setup(tmp_path)
    _protection_unknown(lifecycle, asset, profile, binding)
    lifecycle.reconcile_protection(
        binding.lifecycle_id,
        broker_order_id="broker-protect-1",
        broker_status="partially_filled",
        filled_quantity=Decimal("0.0005"),
        confirmed_net_long_quantity=Decimal("0.0005"),
        at=NOW + timedelta(seconds=5),
    )
    with pytest.raises(CryptoLifecycleIntegrityError, match="broker order id changed"):
        lifecycle.reconcile_protection(
            binding.lifecycle_id,
            broker_order_id="broker-protect-2",
            broker_status="partially_filled",
            filled_quantity=Decimal("0.0006"),
            confirmed_net_long_quantity=Decimal("0.0004"),
            at=NOW + timedelta(seconds=6),
        )
    with pytest.raises(CryptoLifecycleIntegrityError, match="fill regressed"):
        lifecycle.reconcile_protection(
            binding.lifecycle_id,
            broker_order_id="broker-protect-1",
            broker_status="partially_filled",
            filled_quantity=Decimal("0.0004"),
            confirmed_net_long_quantity=Decimal("0.0006"),
            at=NOW + timedelta(seconds=7),
        )


def test_triggered_unfilled_marker_is_not_valid_before_protection_is_open(tmp_path) -> None:
    lifecycle, _runtime, asset, profile, _entry, binding = _setup(tmp_path)
    _protection_unknown(lifecycle, asset, profile, binding)
    with pytest.raises(CryptoLifecycleBlocked, match="only from PROTECTED_OPEN"):
        lifecycle.mark_protection_triggered_unfilled(binding.lifecycle_id, at=NOW + timedelta(seconds=5))


def test_flat_reconciliation_and_halt_are_fail_closed(tmp_path) -> None:
    lifecycle, _runtime, _asset_value, _profile_value, _entry, binding = _setup(tmp_path)
    state = _entry_filled(lifecycle, binding)
    assert state.confirmed_net_long_quantity > 0
    with pytest.raises(CryptoLifecycleBlocked, match="position is non-zero"):
        lifecycle.reconcile_flat(binding.lifecycle_id, open_order_count=0, at=NOW + timedelta(seconds=3))

    lifecycle2, _runtime2, _a2, _p2, _entry2, binding2 = _setup(tmp_path, lifecycle_id="flat-open-order")
    _entry_unknown(lifecycle2, binding2)
    lifecycle2.reconcile_entry(
        binding2.lifecycle_id,
        broker_order_id="broker-entry-none",
        broker_status="canceled",
        filled_quantity=Decimal("0"),
        confirmed_net_long_quantity=Decimal("0"),
        at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(CryptoLifecycleBlocked, match="open orders remain"):
        lifecycle2.reconcile_flat(binding2.lifecycle_id, open_order_count=1, at=NOW + timedelta(seconds=3))
    for invalid in (-1, True, "1"):
        with pytest.raises(ValueError, match="open_order_count"):
            lifecycle2.reconcile_flat(binding2.lifecycle_id, open_order_count=invalid, at=NOW + timedelta(seconds=4))

    lifecycle3, _runtime3, _a3, _p3, _entry3, binding3 = _setup(tmp_path, lifecycle_id="halt-state")
    lifecycle3.prepare(binding3)
    for reason in ("", "x" * 513):
        with pytest.raises(ValueError, match="halt reason"):
            lifecycle3.halt(binding3.lifecycle_id, reason=reason, at=NOW + timedelta(seconds=1))
    halted = lifecycle3.halt(
        binding3.lifecycle_id,
        reason="broker reconciliation unavailable",
        at=NOW + timedelta(seconds=2),
    )
    assert halted.status is CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED
    assert halted.restart_action == "RECONCILE_ONLY"


def test_halt_after_flat_is_rejected(tmp_path) -> None:
    lifecycle, _runtime, _asset_value, _profile_value, _entry, binding = _setup(tmp_path)
    _entry_unknown(lifecycle, binding)
    lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-entry-none",
        broker_status="canceled",
        filled_quantity=Decimal("0"),
        confirmed_net_long_quantity=Decimal("0"),
        at=NOW + timedelta(seconds=2),
    )
    lifecycle.reconcile_flat(binding.lifecycle_id, open_order_count=0, at=NOW + timedelta(seconds=3))
    with pytest.raises(CryptoLifecycleBlocked, match="does not require halt"):
        lifecycle.halt(binding.lifecycle_id, reason="late halt", at=NOW + timedelta(seconds=4))


def test_snapshot_missing_lifecycle_fails_closed(tmp_path) -> None:
    lifecycle, _runtime, _asset_value, _profile_value, _entry, _binding = _setup(tmp_path)
    with pytest.raises(CryptoLifecycleIntegrityError, match="missing durable"):
        lifecycle.snapshot("missing-life")


def test_binding_hash_tamper_is_detected(tmp_path) -> None:
    lifecycle, runtime, _asset_value, _profile_value, _entry, binding = _setup(tmp_path)
    lifecycle.prepare(binding)
    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_lifecycle_bindings SET binding_hash = ? WHERE lifecycle_id = ?",
            ("0" * 64, binding.lifecycle_id),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoLifecycleIntegrityError, match="binding hash mismatch"):
        SQLiteCryptoPaperLifecycle(runtime).snapshot(binding.lifecycle_id)


def test_state_binding_mismatch_is_detected_before_reuse(tmp_path) -> None:
    lifecycle, runtime, _asset_value, _profile_value, _entry, binding = _setup(tmp_path)
    lifecycle.prepare(binding)
    conn = runtime.connect()
    try:
        row = conn.execute(
            "SELECT state_json FROM alpaca_crypto_lifecycle_control WHERE lifecycle_id = ?",
            (binding.lifecycle_id,),
        ).fetchone()
        state = json.loads(str(row["state_json"]))
        state["binding_hash"] = "e" * 64
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":"))
        conn.execute(
            "UPDATE alpaca_crypto_lifecycle_control SET state_json = ? WHERE lifecycle_id = ?",
            (encoded, binding.lifecycle_id),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoLifecycleIntegrityError, match="wrong lifecycle"):
        SQLiteCryptoPaperLifecycle(runtime).snapshot(binding.lifecycle_id)


def test_event_tail_sequence_tamper_is_detected(tmp_path) -> None:
    lifecycle, runtime, _asset_value, _profile_value, _entry, binding = _setup(tmp_path)
    _entry_unknown(lifecycle, binding)
    conn = runtime.connect()
    try:
        conn.execute(
            "DELETE FROM alpaca_crypto_lifecycle_events WHERE lifecycle_id = ? AND sequence = 2",
            (binding.lifecycle_id,),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoLifecycleIntegrityError, match="sequence/tail mismatch"):
        SQLiteCryptoPaperLifecycle(runtime).snapshot(binding.lifecycle_id)


def test_event_previous_hash_tamper_is_detected(tmp_path) -> None:
    lifecycle, runtime, _asset_value, _profile_value, _entry, binding = _setup(tmp_path)
    _entry_unknown(lifecycle, binding)
    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_lifecycle_events SET previous_event_hash = ? WHERE lifecycle_id = ? AND sequence = 2",
            ("f" * 64, binding.lifecycle_id),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoLifecycleIntegrityError, match="chain sequence mismatch"):
        SQLiteCryptoPaperLifecycle(runtime).snapshot(binding.lifecycle_id)


def test_event_hash_tamper_is_detected(tmp_path) -> None:
    lifecycle, runtime, _asset_value, _profile_value, _entry, binding = _setup(tmp_path)
    _entry_unknown(lifecycle, binding)
    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_lifecycle_events SET event_hash = ? WHERE lifecycle_id = ? AND sequence = 2",
            ("0" * 64, binding.lifecycle_id),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoLifecycleIntegrityError, match="event hash mismatch"):
        SQLiteCryptoPaperLifecycle(runtime).snapshot(binding.lifecycle_id)


def test_state_event_head_tamper_with_rehashed_control_is_detected(tmp_path) -> None:
    lifecycle, runtime, _asset_value, _profile_value, _entry, binding = _setup(tmp_path)
    _entry_unknown(lifecycle, binding)
    conn = runtime.connect()
    try:
        row = conn.execute(
            "SELECT state_json FROM alpaca_crypto_lifecycle_control WHERE lifecycle_id = ?",
            (binding.lifecycle_id,),
        ).fetchone()
        state = json.loads(str(row["state_json"]))
        state["event_head_hash"] = "e" * 64
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":"))
        conn.execute(
            "UPDATE alpaca_crypto_lifecycle_control SET state_json = ?, control_hash = ? WHERE lifecycle_id = ?",
            (encoded, sha256(encoded.encode()).hexdigest(), binding.lifecycle_id),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoLifecycleIntegrityError, match="control head differs"):
        SQLiteCryptoPaperLifecycle(runtime).snapshot(binding.lifecycle_id)


@pytest.mark.parametrize("target", ["binding", "state", "event"])
def test_corrupt_serialized_lifecycle_rows_fail_closed(tmp_path, target: str) -> None:
    lifecycle, runtime, _asset_value, _profile_value, _entry, binding = _setup(
        tmp_path, lifecycle_id="corrupt-" + target
    )
    lifecycle.prepare(binding)
    conn = runtime.connect()
    try:
        if target == "binding":
            conn.execute(
                "UPDATE alpaca_crypto_lifecycle_bindings SET binding_json = ? WHERE lifecycle_id = ?",
                ("{bad", binding.lifecycle_id),
            )
            match = "binding JSON"
        elif target == "state":
            conn.execute(
                "UPDATE alpaca_crypto_lifecycle_control SET state_json = ? WHERE lifecycle_id = ?",
                ("{bad", binding.lifecycle_id),
            )
            match = "state JSON"
        else:
            conn.execute(
                "UPDATE alpaca_crypto_lifecycle_events SET payload_json = ? WHERE lifecycle_id = ? AND sequence = 1",
                ("[]", binding.lifecycle_id),
            )
            match = "event row"
    finally:
        conn.close()
    with pytest.raises(CryptoLifecycleIntegrityError, match=match):
        SQLiteCryptoPaperLifecycle(runtime).snapshot(binding.lifecycle_id)
