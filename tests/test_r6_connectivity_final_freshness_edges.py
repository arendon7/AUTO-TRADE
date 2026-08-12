from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import sqlite3

import pytest

import autotrade.connectivity_final_freshness as cff
from autotrade.connectivity_final_freshness import (
    ConnectivityFinalFreshnessConflict,
    ConnectivityFinalFreshnessGuard,
    ConnectivityFinalFreshnessIntegrityError,
    ConnectivityFinalFreshnessRejected,
    SQLiteConnectivityFinalFreshnessRegistry,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_connectivity_candidate import CREDS, NOW, h
from test_r6_connectivity_final_freshness import (
    Clock,
    FreshAccountGateway,
    FreshAssetGateway,
    FreshFlatGateway,
    FreshMarketGateway,
    guard,
    ready_workspace,
)


def successful(tmp_path, *, market_ask="5.01"):
    ws, _, _, _ = ready_workspace(tmp_path)
    result = guard(ws, market_gateway=FreshMarketGateway(ask=market_ask)).acquire(
        credentials=CREDS
    )
    return ws, result


def test_guard_requires_operational_workspace() -> None:
    with pytest.raises(TypeError, match="PaperOperationalWorkspace"):
        ConnectivityFinalFreshnessGuard(object())  # type: ignore[arg-type]


def test_guard_requires_credential_type(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    with pytest.raises(TypeError, match="AlpacaPaperCredentials"):
        guard(ws).acquire(credentials=object())  # type: ignore[arg-type]


def test_guard_rejects_existing_registry_even_without_artifact(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    SQLiteConnectivityFinalFreshnessRegistry(
        SQLiteRuntime(ws.root / "connectivity_final_freshness.sqlite3")
    )
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="never refresh in-place"):
        guard(ws).acquire(credentials=CREDS)


def test_guard_rejects_naive_clock(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        guard(ws, clock=Clock(NOW.replace(tzinfo=None))).acquire(credentials=CREDS)


def test_guard_rejects_missing_operator_registry(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    (ws.root / "connectivity_operator.sqlite3").unlink()
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="human decision registry is missing"):
        guard(ws).acquire(credentials=CREDS)


def test_guard_rejects_missing_operator_artifact(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    (ws.root / "connectivity_operator_decision.json").unlink()
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="operator decision"):
        guard(ws).acquire(credentials=CREDS)


def test_guard_rejects_unsafe_operator_artifact_marker(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    path = ws.root / "connectivity_operator_decision.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["external_post_authorized"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="external_post_authorized"):
        guard(ws).acquire(credentials=CREDS)


def test_guard_rejects_operator_artifact_decision_tamper(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    path = ws.root / "connectivity_operator_decision.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["decision"]["operator_id"] = "operator:tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="artifact/registry decision mismatch"):
        guard(ws).acquire(credentials=CREDS)


def test_guard_rejects_operator_artifact_event_tamper(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    path = ws.root / "connectivity_operator_decision.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["event_hash"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="event mismatch"):
        guard(ws).acquire(credentials=CREDS)


def test_guard_rejects_preparation_operator_hash_drift(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    path = ws.root / "connectivity_preparation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["preparation_hash"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="preparation/operator hash mismatch"):
        guard(ws).acquire(credentials=CREDS)


def test_guard_rejects_preparation_action_drift_with_valid_hash(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    path = ws.root / "connectivity_preparation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["next_action"] = "WRONG"
    body = dict(payload)
    body.pop("preparation_hash")
    payload["preparation_hash"] = cff._hash(body)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="action drifted"):
        ConnectivityFinalFreshnessGuard(ws)._load_preparation(payload["preparation_hash"])


def test_guard_rejects_preparation_authority_drift_with_valid_hash(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    path = ws.root / "connectivity_preparation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["external_post_authorized"] = True
    body = dict(payload)
    body.pop("preparation_hash")
    payload["preparation_hash"] = cff._hash(body)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="authority drifted"):
        ConnectivityFinalFreshnessGuard(ws)._load_preparation(payload["preparation_hash"])


def test_guard_rejects_wrong_current_credential_reference(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    other = replace(CREDS, key_id="different-key")
    gateway = FreshAccountGateway()
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="current credentials do not match"):
        guard(ws, account_gateway=gateway).acquire(credentials=other)
    assert gateway.calls == 0


def test_fresh_account_validator_rejects_wrong_type_and_nonpositive_cap(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    g = guard(ws)
    initial = g._read_initial_account()
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="invalid type"):
        g._validate_fresh_account(initial=initial, fresh=object())  # type: ignore[arg-type]
    fresh = FreshAccountGateway().attest_account(
        credentials=CREDS, expected_account_id=initial.account_id, now=NOW
    )
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="positive"):
        g._validate_fresh_account(
            initial=initial,
            fresh=replace(fresh, buying_power=Decimal("0")),
        )


def test_fresh_asset_validator_rejects_wrong_type_and_status(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    g = guard(ws)
    initial = cff.PaperAssetEvidenceStore(ws).read()
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="invalid type"):
        g._validate_fresh_asset(initial=initial, fresh=object())  # type: ignore[arg-type]
    fresh = replace(initial, observed_at=NOW, request_id="fresh", response_sha256=h("fresh"))
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="no longer active/tradable"):
        g._validate_fresh_asset(initial=initial, fresh=replace(fresh, tradable=False))


def test_fresh_flat_validator_rejects_wrong_type_binding_and_credentials(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    g = guard(ws)
    account = FreshAccountGateway().attest_account(
        credentials=CREDS,
        expected_account_id=g._read_initial_account().account_id,
        now=NOW,
    )
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="invalid type"):
        g._validate_fresh_flat(fresh=object(), account=account)  # type: ignore[arg-type]
    fresh = FreshFlatGateway().attest_flatness(
        credentials=CREDS,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=account.credential_reference,
        now=NOW,
    )
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="flat/account"):
        g._validate_fresh_flat(
            fresh=replace(fresh, account_attestation_fingerprint="f" * 64),
            account=account,
        )
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="credential reference"):
        g._validate_fresh_flat(
            fresh=replace(fresh, credential_reference="f" * 64),
            account=account,
        )


def test_fresh_market_validator_rejects_wrong_type_symbol_and_feed(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    g = guard(ws)
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="invalid type"):
        g._validate_fresh_market(fresh=object(), symbol="FIVE")  # type: ignore[arg-type]
    fresh = FreshMarketGateway().attest_snapshot(credentials=CREDS, symbol="FIVE", now=NOW)
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="symbol drifted"):
        g._validate_fresh_market(
            fresh=replace(fresh, market=replace(fresh.market, symbol="OTHER")),
            symbol="FIVE",
        )
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="IEX/USD"):
        g._validate_fresh_market(fresh=replace(fresh, feed="sip"), symbol="FIVE")


def test_permit_validates_hash_window_cap_and_safety_version(tmp_path) -> None:
    _, result = successful(tmp_path)
    permit = result.permit
    with pytest.raises(ValueError, match="permit hash mismatch"):
        replace(permit, permit_hash="f" * 64)
    with pytest.raises(ValueError, match="<=5 seconds"):
        replace(permit, expires_at=permit.issued_at + timedelta(seconds=6))
    with pytest.raises(ValueError, match="effective_notional_cap"):
        replace(permit, effective_notional_cap=Decimal("0"))
    with pytest.raises(ValueError, match="safety_state_version"):
        replace(permit, safety_state_version=-1)
    assert permit.is_valid_at(permit.issued_at - timedelta(microseconds=1)) is False
    assert permit.is_valid_at(permit.expires_at) is False


def test_registry_requires_permit_type_get_missing_and_lists_empty(tmp_path) -> None:
    registry = SQLiteConnectivityFinalFreshnessRegistry(SQLiteRuntime(tmp_path / "fresh.sqlite3"))
    with pytest.raises(TypeError, match="ConnectivityFinalFreshnessPermit"):
        registry.issue(object())  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        registry.get("f" * 64)
    assert registry.list_states() == ()


def test_registry_issue_is_idempotent_and_rejects_valid_different_permit(tmp_path) -> None:
    ws, result = successful(tmp_path)
    registry = SQLiteConnectivityFinalFreshnessRegistry(
        SQLiteRuntime(ws.root / "connectivity_final_freshness.sqlite3")
    )
    assert registry.issue(result.permit) == result.state
    _, different = successful(tmp_path / "different", market_ask="5.02")
    assert different.permit.permit_hash != result.permit.permit_hash
    with pytest.raises(ConnectivityFinalFreshnessConflict, match="already issued"):
        registry.issue(different.permit)


def test_registry_detects_event_count_tamper(tmp_path) -> None:
    ws, result = successful(tmp_path)
    path = ws.root / "connectivity_final_freshness.sqlite3"
    registry = SQLiteConnectivityFinalFreshnessRegistry(SQLiteRuntime(path))
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM connectivity_final_freshness_events WHERE sequence=1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityFinalFreshnessIntegrityError, match="count mismatch"):
        registry.list_states()


def test_registry_detects_payload_tamper(tmp_path) -> None:
    ws, result = successful(tmp_path)
    path = ws.root / "connectivity_final_freshness.sqlite3"
    registry = SQLiteConnectivityFinalFreshnessRegistry(SQLiteRuntime(path))
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE connectivity_final_freshness_events SET payload_json=? WHERE sequence=1",
            (json.dumps({"tampered": True}),),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityFinalFreshnessIntegrityError):
        registry.get(result.permit.permit_hash)


def test_registry_detects_event_hash_tamper(tmp_path) -> None:
    ws, result = successful(tmp_path)
    path = ws.root / "connectivity_final_freshness.sqlite3"
    registry = SQLiteConnectivityFinalFreshnessRegistry(SQLiteRuntime(path))
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE connectivity_final_freshness_events SET event_hash=? WHERE sequence=1",
            ("f" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityFinalFreshnessIntegrityError, match="event hash mismatch"):
        registry.get(result.permit.permit_hash)


def test_empty_registry_non_genesis_head_is_detected(tmp_path) -> None:
    path = tmp_path / "fresh.sqlite3"
    registry = SQLiteConnectivityFinalFreshnessRegistry(SQLiteRuntime(path))
    fake = "f" * 64
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE connectivity_final_freshness_control SET event_head_hash=?,control_hash=? WHERE singleton=1",
            (fake, cff._control_hash(0, fake)),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityFinalFreshnessIntegrityError, match="non-genesis"):
        registry.list_states()


def test_permit_payload_parser_rejects_noncanonical_and_unsafe_markers(tmp_path) -> None:
    _, result = successful(tmp_path)
    payload = result.permit.payload()
    payload["unexpected"] = True
    with pytest.raises(ConnectivityFinalFreshnessIntegrityError, match="non-canonical"):
        cff._permit_from_payload(payload)
    payload = result.permit.payload()
    payload["external_post_authorized"] = True
    with pytest.raises(ConnectivityFinalFreshnessIntegrityError, match="external_post_authorized"):
        cff._permit_from_payload(payload)


def test_json_helpers_reject_invalid_shapes() -> None:
    with pytest.raises(ConnectivityFinalFreshnessIntegrityError, match="JSON is invalid"):
        cff._json_object("{", "x")
    with pytest.raises(ConnectivityFinalFreshnessIntegrityError, match="must be object"):
        cff._json_object("[]", "x")
    with pytest.raises(ValueError, match="datetime value"):
        cff._iso("not-a-datetime")
