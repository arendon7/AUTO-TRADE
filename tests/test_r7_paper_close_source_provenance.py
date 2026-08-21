from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import (
    PreparedCryptoPaperCanaryPackage,
    _hash_json,
    _package_payload_from_values,
)
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import FirstCanaryAttemptWorkspace
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleBinding, CryptoLifecycleStatus
from autotrade.domain import OrderIntent, OrderRecord, OrderStatus, OrderType, Side, intent_fingerprint
from autotrade.first_canary_fee_aware_recovery import FirstCanaryFeeAwareRecoveryLifecycle
from autotrade.paper_close_source_provenance import (
    FirstCanaryCloseSourceReader,
    PaperCloseSourceProvenance,
    PaperCloseSourceProvenanceConflict,
    PaperCloseSourceProvenanceMissing,
)
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime, _order_to_json
from autotrade.product_profile import BrokerOrderType, TimeInForce

NOW = datetime(2026, 8, 21, 16, 35, tzinfo=timezone.utc)
ATTEMPT_ID = "first-canary-11111111111111111111111111111111"
GROSS = Decimal("0.00014432")
NET = Decimal("0.000143959")
PRICE = Decimal("72760")
BROKER_ORDER_ID = "broker-entry-source"
STRATEGY_ID = "R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION"


@dataclass(slots=True)
class _Case:
    workspace: Path
    attempt: FirstCanaryAttemptWorkspace
    package: PreparedCryptoPaperCanaryPackage
    order: OrderRecord
    lifecycle_id: str


def _package(*, order: OrderRecord, binding, prepared_state) -> PreparedCryptoPaperCanaryPackage:
    values: dict[str, object] = {
        "lifecycle_id": binding.lifecycle_id,
        "order_id": order.order_id,
        "client_order_id": binding.entry_client_order_id,
        "symbol": order.intent.symbol,
        "intent_fingerprint": intent_fingerprint(order.intent),
        "risk_decision_id": order.risk_decision_id,
        "risk_decision_fingerprint": "7" * 64,
        "risk_decision_safety_state_version": 0,
        "risk_decision_valid_until": NOW + timedelta(minutes=2),
        "market_fingerprint": "8" * 64,
        "market_attestation_fingerprint": "9" * 64,
        "account_attestation_fingerprint": "1" * 64,
        "asset_attestation_fingerprint": "2" * 64,
        "product_profile_fingerprint": "3" * 64,
        "crypto_order_fingerprint": binding.entry_order_fingerprint,
        "crypto_order_payload_hash": "a" * 64,
        "lifecycle_binding_hash": binding.fingerprint,
        "lifecycle_control_hash": prepared_state.control_hash,
        "lifecycle_event_head_hash": prepared_state.event_head_hash,
        "quantity": GROSS,
        "limit_price": PRICE,
        "notional": GROSS * PRICE,
        "effective_notional_cap": Decimal("12"),
        "prepared_at": NOW,
        "execution_deadline": NOW + timedelta(seconds=30),
        "order_status": OrderStatus.VALIDATED.value,
        "broker_order_type": BrokerOrderType.LIMIT.value,
        "time_in_force": TimeInForce.IOC.value,
        "opening_short": False,
        "uses_margin": False,
        "network_write_authorized": False,
        "next_action": "OPERATOR_DECISION_REQUIRED",
    }
    return PreparedCryptoPaperCanaryPackage(
        **values,
        package_hash=_hash_json(_package_payload_from_values(values)),
    )


def _write_hashed(
    attempt: FirstCanaryAttemptWorkspace,
    path: Path,
    document: dict[str, object],
    hash_key: str,
) -> None:
    payload = dict(document)
    payload[hash_key] = attempt.document_hash(payload, hash_key=hash_key)
    attempt.write_once(path=path, document=payload)


def _rewrite_hashed(
    attempt: FirstCanaryAttemptWorkspace,
    path: Path,
    hash_key: str,
    mutate,
) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    document.pop(hash_key, None)
    document[hash_key] = attempt.document_hash(document, hash_key=hash_key)
    path.write_text(
        json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _checkpoint_delete_mode(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()
    finally:
        conn.close()
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


def _build_case(
    tmp_path: Path,
    *,
    net: Decimal = NET,
    resolution: str = "recovery",
) -> _Case:
    workspace = tmp_path / "paper-workspace"
    workspace.mkdir(parents=True)
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=ATTEMPT_ID,
    )
    runtime = SQLiteRuntime(attempt.database_path)

    intent = OrderIntent(
        intent_id="original-first-canary-intent",
        idempotency_key="original-first-canary-idem",
        strategy_id=STRATEGY_ID,
        symbol="BTC/USD",
        side=Side.BUY,
        quantity=GROSS,
        order_type=OrderType.LIMIT,
        created_at=NOW,
        limit_price=PRICE,
    )
    order = OrderRecord(
        order_id="original-oms-entry-order",
        intent=intent,
        risk_decision_id="original-risk-decision",
        status=OrderStatus.SUBMITTING,
        created_at=NOW,
        submitted_at=NOW + timedelta(milliseconds=20),
    )
    SQLiteOrderStore(runtime).create_if_absent(order)

    account_fp = "1" * 64
    asset_fp = "2" * 64
    product_fp = "3" * 64
    raw = ":".join((order.order_id, account_fp, asset_fp, product_fp))
    lifecycle_id = "r6c-entry-" + sha256(raw.encode("utf-8")).hexdigest()[:40]
    binding = CryptoLifecycleBinding(
        lifecycle_id=lifecycle_id,
        account_attestation_fingerprint=account_fp,
        asset_attestation_fingerprint=asset_fp,
        product_profile_fingerprint=product_fp,
        symbol="BTC/USD",
        entry_order_fingerprint="4" * 64,
        entry_client_order_id="atr6c-entry-source",
        entry_quantity=GROSS,
        created_at=NOW,
    )
    lifecycle = FirstCanaryFeeAwareRecoveryLifecycle(runtime)
    prepared_state = lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(
        lifecycle_id,
        at=NOW + timedelta(milliseconds=30),
    )
    final_state = lifecycle.reconcile_entry(
        lifecycle_id,
        broker_order_id=BROKER_ORDER_ID,
        broker_status="filled",
        filled_quantity=GROSS,
        confirmed_net_long_quantity=net,
        at=NOW + timedelta(milliseconds=40),
    )
    assert final_state.status is CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED
    package = _package(order=order, binding=binding, prepared_state=prepared_state)

    _write_hashed(
        attempt,
        attempt.preparation_path,
        {
            "schema_version": 1,
            "attempt_id": ATTEMPT_ID,
            "environment": "PAPER",
            "prepared_package": package.canonical_payload(),
            "external_post_authorized": False,
            "broker_write_performed": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        },
        "preparation_hash",
    )
    _write_hashed(
        attempt,
        attempt.execution_started_path,
        {
            "schema_version": 1,
            "attempt_id": ATTEMPT_ID,
            "client_order_id": package.client_order_id,
            "package_hash": package.package_hash,
            "retry_forbidden": True,
            "writer_invocation_permitted_once": True,
            "live_trading": "BLOCKED",
        },
        "execution_started_hash",
    )
    started_hash = json.loads(
        attempt.execution_started_path.read_text(encoding="utf-8")
    )["execution_started_hash"]
    _write_hashed(
        attempt,
        attempt.execution_result_path,
        {
            "schema_version": 1,
            "attempt_id": ATTEMPT_ID,
            "client_order_id": package.client_order_id,
            "package_hash": package.package_hash,
            "execution_started_hash": started_hash,
            "entry_attempt_count": 1,
            "broker_post_outcome": "BROKER_RESPONSE_RECEIVED",
            "retry_forbidden": True,
            "live_trading": "BLOCKED",
        },
        "execution_result_hash",
    )

    if resolution == "recovery":
        _write_hashed(
            attempt,
            attempt.recovery_resolution_path,
            {
                "schema_version": 1,
                "status": "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECONCILIATION_FINAL_NO_RETRY",
                "attempt_id": ATTEMPT_ID,
                "client_order_id": package.client_order_id,
                "reconciliation_type": "ORDER_PLUS_POSITION",
                "broker_order_id": BROKER_ORDER_ID,
                "broker_order_status": "filled",
                "broker_filled_quantity": str(GROSS),
                "position_quantity": str(net),
                "resulting_lifecycle_status": CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED.value,
                "entry_attempt_count": 1,
                "retry_post": False,
                "recovery_get_only": True,
                "credentials_persisted": False,
                "live_trading": "BLOCKED",
            },
            "recovery_resolution_hash",
        )
    elif resolution == "initial":
        _write_hashed(
            attempt,
            attempt.reconciliation_path,
            {
                "schema_version": 1,
                "status": "CRYPTO_PAPER_FIRST_CANARY_RECONCILED_FINAL_NO_RETRY",
                "attempt_id": ATTEMPT_ID,
                "client_order_id": package.client_order_id,
                "evidence_type": "ORDER_PLUS_POSITION",
                "broker_order_id": BROKER_ORDER_ID,
                "broker_order_status": "filled",
                "broker_filled_quantity": str(GROSS),
                "position_quantity": str(net),
                "lifecycle_status": CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED.value,
                "retry_post": False,
                "persisted_final_resolution": True,
                "live_trading": "BLOCKED",
            },
            "reconciliation_hash",
        )
    else:
        raise AssertionError(resolution)

    _checkpoint_delete_mode(attempt.database_path)
    return _Case(
        workspace=workspace,
        attempt=attempt,
        package=package,
        order=order,
        lifecycle_id=lifecycle_id,
    )


def _reader(case: _Case) -> FirstCanaryCloseSourceReader:
    return FirstCanaryCloseSourceReader(
        workspace_path=case.workspace,
        attempt_id=ATTEMPT_ID,
    )


def test_read_only_source_provenance_happy_path_fee_adjusted(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    before = case.attempt.database_path.read_bytes()
    provenance = _reader(case).verify(now=NOW + timedelta(seconds=1))
    assert isinstance(provenance, PaperCloseSourceProvenance)
    assert provenance.strategy_id == STRATEGY_ID
    assert provenance.lifecycle_id == case.lifecycle_id
    assert provenance.source_order.order_id == case.order.order_id
    assert provenance.gross_filled_quantity == GROSS
    assert provenance.confirmed_net_long_quantity == NET
    assert provenance.broker_order_id == BROKER_ORDER_ID
    assert provenance.broker_order_status == "filled"
    assert provenance.resolution_kind == "GET_ONLY_RECOVERY_RESOLUTION"
    assert len(provenance.provenance_hash) == 64
    assert case.attempt.database_path.read_bytes() == before
    assert not Path(str(case.attempt.database_path) + "-wal").exists()
    assert not Path(str(case.attempt.database_path) + "-shm").exists()


def test_initial_terminal_reconciliation_is_supported(tmp_path: Path) -> None:
    case = _build_case(tmp_path, net=GROSS, resolution="initial")
    provenance = _reader(case).verify(now=NOW + timedelta(seconds=1))
    assert provenance.resolution_kind == "INITIAL_RECONCILIATION"
    assert provenance.confirmed_net_long_quantity == GROSS


def test_reader_constructor_fails_closed_on_bad_workspace_and_attempt(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="pathlib.Path"):
        FirstCanaryCloseSourceReader(
            workspace_path="not-a-path",  # type: ignore[arg-type]
            attempt_id=ATTEMPT_ID,
        )
    with pytest.raises(PaperCloseSourceProvenanceMissing, match="workspace"):
        FirstCanaryCloseSourceReader(
            workspace_path=tmp_path / "missing",
            attempt_id=ATTEMPT_ID,
        )
    tmp_path.joinpath("workspace").mkdir()
    with pytest.raises(PaperCloseSourceProvenanceMissing, match="attempt_id"):
        FirstCanaryCloseSourceReader(
            workspace_path=tmp_path / "workspace",
            attempt_id="bad",
        )


def test_verify_requires_aware_time(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        _reader(case).verify(now=NOW.replace(tzinfo=None))


def test_tampered_preparation_hash_and_missing_start_fail_closed(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    raw = json.loads(case.attempt.preparation_path.read_text(encoding="utf-8"))
    raw["environment"] = "LIVE"
    case.attempt.preparation_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperCloseSourceProvenanceMissing, match="preparation"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))

    case = _build_case(tmp_path / "second")
    case.attempt.execution_started_path.unlink()
    with pytest.raises(PaperCloseSourceProvenanceMissing, match="execution-start"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("environment", "LIVE"),
        ("external_post_authorized", True),
        ("broker_write_performed", True),
        ("credentials_persisted", True),
        ("live_trading", "ENABLED"),
    ],
)
def test_hash_valid_but_semantically_invalid_preparation_is_rejected(
    tmp_path: Path,
    key: str,
    value: object,
) -> None:
    case = _build_case(tmp_path)
    _rewrite_hashed(
        case.attempt,
        case.attempt.preparation_path,
        "preparation_hash",
        lambda d: d.__setitem__(key, value),
    )
    with pytest.raises(
        PaperCloseSourceProvenanceConflict,
        match="preparation source binding mismatch",
    ):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


@pytest.mark.parametrize(
    ("target", "key", "value", "match"),
    [
        ("started", "retry_forbidden", False, "execution-start source binding mismatch"),
        ("started", "writer_invocation_permitted_once", False, "execution-start source binding mismatch"),
        ("result", "entry_attempt_count", 2, "one burned POST attempt"),
        ("result", "broker_post_outcome", "NOT_YET_INVOKED", "recognized broker POST outcome"),
        ("result", "live_trading", "ENABLED", "LIVE deny"),
    ],
)
def test_burned_execution_chain_must_remain_one_shot(
    tmp_path: Path,
    target: str,
    key: str,
    value: object,
    match: str,
) -> None:
    case = _build_case(tmp_path)
    path = (
        case.attempt.execution_started_path
        if target == "started"
        else case.attempt.execution_result_path
    )
    hash_key = (
        "execution_started_hash"
        if target == "started"
        else "execution_result_hash"
    )
    _rewrite_hashed(
        case.attempt,
        path,
        hash_key,
        lambda d: d.__setitem__(key, value),
    )
    with pytest.raises(PaperCloseSourceProvenanceConflict, match=match):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def test_execution_result_must_bind_exact_start_latch(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    _rewrite_hashed(
        case.attempt,
        case.attempt.execution_result_path,
        "execution_result_hash",
        lambda d: d.__setitem__("execution_started_hash", "f" * 64),
    )
    with pytest.raises(PaperCloseSourceProvenanceConflict, match="start latch"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def test_terminal_resolution_must_be_get_only_final(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    _rewrite_hashed(
        case.attempt,
        case.attempt.recovery_resolution_path,
        "recovery_resolution_hash",
        lambda d: d.__setitem__("recovery_get_only", False),
    )
    with pytest.raises(
        PaperCloseSourceProvenanceConflict,
        match="GET-only source resolution mismatch",
    ):
        _reader(case).verify(now=NOW + timedelta(seconds=1))

    case = _build_case(tmp_path / "second")
    _rewrite_hashed(
        case.attempt,
        case.attempt.recovery_resolution_path,
        "recovery_resolution_hash",
        lambda d: d.__setitem__("status", "PENDING"),
    )
    with pytest.raises(
        PaperCloseSourceProvenanceConflict,
        match="GET-only source resolution mismatch",
    ):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def test_missing_terminal_resolution_is_rejected(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    case.attempt.recovery_resolution_path.unlink()
    with pytest.raises(
        PaperCloseSourceProvenanceMissing,
        match="terminal broker reconciliation",
    ):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def test_active_wal_or_shm_sidecar_is_fail_closed(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    Path(str(case.attempt.database_path) + "-wal").write_bytes(b"active-wal")
    with pytest.raises(PaperCloseSourceProvenanceConflict, match="WAL/SHM"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def test_missing_or_symlink_attempt_database_is_fail_closed(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    original = case.attempt.database_path
    backup = original.with_name("attempt-backup.sqlite3")
    original.rename(backup)
    with pytest.raises(PaperCloseSourceProvenanceMissing, match="attempt.sqlite3"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))
    original.symlink_to(backup)
    with pytest.raises(PaperCloseSourceProvenanceMissing, match="attempt.sqlite3"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def _update_order(case: _Case, order: OrderRecord) -> None:
    conn = sqlite3.connect(case.attempt.database_path)
    try:
        conn.execute(
            "UPDATE orders SET idempotency_key=?, record_json=? WHERE order_id=?",
            (
                order.intent.idempotency_key,
                _order_to_json(order),
                case.order.order_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_source_oms_order_must_match_package_and_external_submitting(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    _update_order(
        case,
        replace(case.order, status=OrderStatus.VALIDATED, submitted_at=None),
    )
    with pytest.raises(PaperCloseSourceProvenanceConflict, match="SUBMITTING"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))

    case = _build_case(tmp_path / "second")
    _update_order(case, replace(case.order, risk_decision_id="different-risk"))
    with pytest.raises(PaperCloseSourceProvenanceConflict, match="RiskDecision"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def test_missing_and_noncanonical_source_order_rows_are_rejected(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    conn = sqlite3.connect(case.attempt.database_path)
    try:
        conn.execute(
            "DELETE FROM orders WHERE order_id=?",
            (case.order.order_id,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperCloseSourceProvenanceMissing, match="source OMS order"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))

    case = _build_case(tmp_path / "second")
    conn = sqlite3.connect(case.attempt.database_path)
    try:
        raw = _order_to_json(case.order)
        conn.execute(
            "UPDATE orders SET record_json=? WHERE order_id=?",
            (raw + " ", case.order.order_id),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperCloseSourceProvenanceConflict, match="canonical"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def test_lifecycle_control_and_event_chain_tamper_are_rejected(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    conn = sqlite3.connect(case.attempt.database_path)
    try:
        conn.execute(
            "UPDATE alpaca_crypto_lifecycle_control SET control_hash=? WHERE lifecycle_id=?",
            ("f" * 64, case.lifecycle_id),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperCloseSourceProvenanceConflict, match="lifecycle integrity"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))

    case = _build_case(tmp_path / "second")
    conn = sqlite3.connect(case.attempt.database_path)
    try:
        conn.execute(
            "UPDATE alpaca_crypto_lifecycle_events SET event_hash=? WHERE lifecycle_id=? AND sequence=1",
            ("e" * 64, case.lifecycle_id),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperCloseSourceProvenanceConflict, match="lifecycle integrity"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def test_missing_lifecycle_rows_are_rejected(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    conn = sqlite3.connect(case.attempt.database_path)
    try:
        conn.execute(
            "DELETE FROM alpaca_crypto_lifecycle_control WHERE lifecycle_id=?",
            (case.lifecycle_id,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(
        PaperCloseSourceProvenanceMissing,
        match="lifecycle binding/control",
    ):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def test_valid_rehashed_package_cannot_rebind_another_lifecycle(tmp_path: Path) -> None:
    case = _build_case(tmp_path)

    def mutate(document: dict[str, object]) -> None:
        package = document["prepared_package"]
        assert isinstance(package, dict)
        package["lifecycle_binding_hash"] = "f" * 64
        material = {
            key: value
            for key, value in package.items()
            if key != "package_hash"
        }
        package["package_hash"] = _hash_json(material)

    _rewrite_hashed(
        case.attempt,
        case.attempt.preparation_path,
        "preparation_hash",
        mutate,
    )
    with pytest.raises(PaperCloseSourceProvenanceConflict, match="lifecycle binding"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def test_terminal_resolution_must_equal_lifecycle_broker_truth(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    _rewrite_hashed(
        case.attempt,
        case.attempt.recovery_resolution_path,
        "recovery_resolution_hash",
        lambda d: d.__setitem__("broker_order_id", "different-broker-order"),
    )
    with pytest.raises(
        PaperCloseSourceProvenanceConflict,
        match="terminal reconciliation differs",
    ):
        _reader(case).verify(now=NOW + timedelta(seconds=1))

    case = _build_case(tmp_path / "second")
    _rewrite_hashed(
        case.attempt,
        case.attempt.recovery_resolution_path,
        "recovery_resolution_hash",
        lambda d: d.__setitem__("broker_filled_quantity", "0.0001"),
    )
    with pytest.raises(PaperCloseSourceProvenanceConflict, match="gross fill"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))


def test_terminal_net_position_runs_fee_consistency_guard(tmp_path: Path, monkeypatch) -> None:
    case = _build_case(tmp_path)

    def reject_fee(*, filled_quantity, confirmed_net_long_quantity) -> None:
        assert filled_quantity == GROSS
        assert confirmed_net_long_quantity == NET
        raise ValueError("synthetic semantic fee rejection")

    monkeypatch.setattr(
        "autotrade.paper_close_source_provenance._validate_fee_adjusted_net_position",
        reject_fee,
    )
    with pytest.raises(PaperCloseSourceProvenanceConflict, match="fee-consistent"):
        _reader(case).verify(now=NOW + timedelta(seconds=1))
