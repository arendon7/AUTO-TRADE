from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from autotrade.brokers.alpaca_paper_core_provenance import (
    PaperCoreProvenanceConflict,
    PaperCoreProvenanceMissing,
    PaperOperationalCoreProvenanceReader,
    _provenance_hash,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import _order_to_json
from test_r6_core_provenance import setup_provenance
from test_r6_paper_canary_coordinator import NOW, decision


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self._row = row

    def execute(self, *_args, **_kwargs):
        return _Cursor(self._row)


def _hash(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _valid_health_row(*, strategy_id: str) -> dict[str, object]:
    baseline = _hash("baseline")
    policy = _hash("policy")
    assessment = _hash("assessment")
    recovery = _hash("recovery")
    updated_at = NOW.isoformat()
    payload = {
        "baseline_fingerprint": baseline,
        "distinct_quarantine_count": 0,
        "entity_id": strategy_id,
        "entity_kind": "STRATEGY",
        "last_assessment_fingerprint": assessment,
        "policy_fingerprint": policy,
        "recovery_ack_head": recovery,
        "state": "HEALTHY",
        "updated_at": updated_at,
        "version": 1,
    }
    return {
        "entity_kind": "STRATEGY",
        "entity_id": strategy_id,
        "state": "HEALTHY",
        "version": 1,
        "distinct_quarantine_count": 0,
        "baseline_fingerprint": baseline,
        "policy_fingerprint": policy,
        "last_assessment_fingerprint": assessment,
        "updated_at": updated_at,
        "recovery_ack_head": recovery,
        "state_hash": _provenance_hash(payload),
    }


def test_provenance_value_object_rejects_invalid_hash_status_versions_and_time(tmp_path) -> None:
    workspace, _, _, _, _ = setup_provenance(tmp_path)
    proof = PaperOperationalCoreProvenanceReader(workspace).verify(now=NOW)

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        replace(proof, core_db_sha256="INVALID")
    with pytest.raises(ValueError, match="requires VALIDATED"):
        replace(proof, order_status=OrderStatus.SUBMITTING.value)
    with pytest.raises(ValueError, match="non-negative integer"):
        replace(proof, safety_version=True)
    with pytest.raises(ValueError, match="non-negative integer"):
        replace(proof, safety_version=-1)
    with pytest.raises(ValueError, match="must be > 0"):
        replace(proof, portfolio_version=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(proof, verified_at=datetime(2026, 8, 11, 12, 0, 0))
    with pytest.raises(ValueError, match="hash mismatch"):
        replace(proof, provenance_hash="0" * 64)


def test_read_order_rejects_missing_nontext_noncanonical_and_row_identity(tmp_path) -> None:
    workspace, result, _, _, _ = setup_provenance(tmp_path)
    reader = PaperOperationalCoreProvenanceReader
    package = result.package
    risk = decision()
    raw = _order_to_json(result.order)

    with pytest.raises(PaperCoreProvenanceMissing, match="missing from durable OMS"):
        reader._read_order(_Conn(None), package, risk)
    with pytest.raises(PaperCoreProvenanceConflict, match="not text"):
        reader._read_order(
            _Conn({"record_json": b"bad", "order_id": result.order.order_id, "idempotency_key": result.order.intent.idempotency_key}),
            package,
            risk,
        )
    with pytest.raises(PaperCoreProvenanceConflict, match="not canonical"):
        reader._read_order(
            _Conn({"record_json": raw + "\n", "order_id": result.order.order_id, "idempotency_key": result.order.intent.idempotency_key}),
            package,
            risk,
        )
    with pytest.raises(PaperCoreProvenanceConflict, match="row identity mismatch"):
        reader._read_order(
            _Conn({"record_json": raw, "order_id": result.order.order_id, "idempotency_key": "wrong"}),
            package,
            risk,
        )


def test_read_order_rejects_status_risk_and_intent_mismatches(tmp_path) -> None:
    _, result, _, _, _ = setup_provenance(tmp_path)
    reader = PaperOperationalCoreProvenanceReader
    package = result.package
    risk = decision()

    submitting = replace(result.order, status=OrderStatus.SUBMITTING)
    with pytest.raises(PaperCoreProvenanceConflict, match="remain VALIDATED"):
        reader._read_order(
            _Conn({"record_json": _order_to_json(submitting), "order_id": submitting.order_id, "idempotency_key": submitting.intent.idempotency_key}),
            package,
            risk,
        )

    wrong_risk = replace(result.order, risk_decision_id="different-risk-decision")
    with pytest.raises(PaperCoreProvenanceConflict, match="RiskDecision id mismatch"):
        reader._read_order(
            _Conn({"record_json": _order_to_json(wrong_risk), "order_id": wrong_risk.order_id, "idempotency_key": wrong_risk.intent.idempotency_key}),
            package,
            risk,
        )

    with pytest.raises(PaperCoreProvenanceConflict, match="intent id mismatch"):
        reader._read_order(
            _Conn({"record_json": _order_to_json(result.order), "order_id": result.order.order_id, "idempotency_key": result.order.intent.idempotency_key}),
            package,
            replace(risk, intent_id="different-intent"),
        )


def test_read_safety_rejects_missing_invalid_fields_kill_and_version(tmp_path) -> None:
    _, result, _, _, _ = setup_provenance(tmp_path)
    package = result.package
    reader = PaperOperationalCoreProvenanceReader
    base = {
        "kill_switch_active": 0,
        "kill_switch_reason": "",
        "version": package.risk_decision_safety_state_version,
        "updated_at": NOW.isoformat(),
    }

    with pytest.raises(PaperCoreProvenanceMissing, match="Safety state is missing"):
        reader._read_safety(_Conn(None), package)

    cases = (
        ({**base, "kill_switch_active": 2}, "kill-switch flag is invalid"),
        ({**base, "kill_switch_reason": None}, "reason is invalid"),
        ({**base, "updated_at": 123}, "timestamp is invalid"),
        ({**base, "kill_switch_active": 1, "kill_switch_reason": "test"}, "kill switch is engaged"),
        ({**base, "version": package.risk_decision_safety_state_version + 1}, "Safety version differs"),
    )
    for row, message in cases:
        with pytest.raises(PaperCoreProvenanceConflict, match=message):
            reader._read_safety(_Conn(row), package)

    state, observed = reader._read_safety(_Conn(base), package)
    assert state.version == package.risk_decision_safety_state_version
    assert len(observed) == 64


def test_read_health_rejects_missing_identity_state_versions_hashes_and_time(tmp_path) -> None:
    _, result, _, _, _ = setup_provenance(tmp_path)
    strategy_id = result.order.intent.strategy_id
    reader = PaperOperationalCoreProvenanceReader
    valid = _valid_health_row(strategy_id=strategy_id)

    with pytest.raises(PaperCoreProvenanceMissing, match="Health state is missing"):
        reader._read_strategy_health(_Conn(None), strategy_id=strategy_id)

    cases = (
        ({**valid, "entity_id": "other"}, "row identity mismatch"),
        ({**valid, "state": "DEGRADED"}, "not HEALTHY"),
        ({**valid, "version": True}, "version is invalid"),
        ({**valid, "distinct_quarantine_count": -1}, "quarantine count is invalid"),
        ({**valid, "baseline_fingerprint": "bad"}, "baseline hash is invalid"),
        ({**valid, "updated_at": 123}, "timestamp/recovery evidence is invalid"),
        ({**valid, "recovery_ack_head": ""}, "timestamp/recovery evidence is invalid"),
        ({**valid, "updated_at": "2026-08-11T12:00:00"}, "timestamp must be timezone-aware"),
        ({**valid, "state_hash": "f" * 64}, "state integrity failed"),
    )
    for row, message in cases:
        with pytest.raises(PaperCoreProvenanceConflict, match=message):
            reader._read_strategy_health(_Conn(row), strategy_id=strategy_id)

    health = reader._read_strategy_health(_Conn(valid), strategy_id=strategy_id)
    assert health.entity_id == strategy_id
    assert health.version == 1
    assert health.fingerprint == valid["state_hash"]


def test_provenance_hash_is_stable_for_canonical_mapping_order() -> None:
    left = _provenance_hash({"b": 2, "a": 1})
    right = _provenance_hash({"a": 1, "b": 2})
    assert left == right
    assert len(left) == 64


def test_now_fixture_is_timezone_aware() -> None:
    assert NOW.tzinfo is not None
    assert NOW.utcoffset() is not None
    assert timezone.utc.utcoffset(NOW) is not None
