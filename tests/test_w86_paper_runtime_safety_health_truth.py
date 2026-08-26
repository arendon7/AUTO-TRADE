from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

import autotrade.paper_runtime_candidate_identity as candidate_module
import autotrade.paper_runtime_safety_health_truth as truth_module
from autotrade.paper_runtime_safety_health_truth import (
    PORTFOLIO_HEALTH_ENTITY_ID,
    PaperRuntimeSafetyHealthTruthIntegrityError,
    PaperRuntimeSafetyHealthTruthPolicy,
    PaperRuntimeSafetyHealthTruthProof,
    PaperRuntimeSafetyHealthTruthReader,
)


AT = datetime(2026, 8, 25, 21, 0, tzinfo=timezone.utc)
STRATEGY_ID = "strategy-w86-safety-health"


def _hash(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _ledger_hash(
    *, prev_hash: str, event_id: str, event_type: str, occurred_at: str, payload_json: str
) -> str:
    return sha256(
        "\x1f".join(
            (prev_hash, event_id, event_type, occurred_at, payload_json)
        ).encode("utf-8")
    ).hexdigest()


def _candidate(*, strategy_id: str = STRATEGY_ID):
    values = {
        "proof_id": "w86-candidate-safety-health-test",
        "contract_version": candidate_module.PAPER_RUNTIME_CANDIDATE_IDENTITY_VERSION,
        "w85_source_snapshot_hash": "1" * 64,
        "authority_key": "2" * 64,
        "admission_id": "admission-safety-health-test",
        "admission_hash": "3" * 64,
        "final_admission_verification_hash": "4" * 64,
        "w83_resolution_id": "w83-resolution-safety-health-test",
        "w83_resolution_hash": "5" * 64,
        "w83_binding_hash": "6" * 64,
        "selected_trial_fingerprint": "7" * 64,
        "selected_strategy_id": strategy_id,
        "selected_strategy_version": "v1",
        "strategy_spec_hash": "8" * 64,
        "loaded_runtime_code_hash": "9" * 64,
        "fee_product_economics_hash": "a" * 64,
        "intent_fingerprint": "b" * 64,
        "product_id": "product-safety-health-test",
        "asset_class": "crypto",
        "venue": "alpaca-paper-model",
        "symbol": "TEST-USD",
        "side": "BUY",
        "base_currency": "TEST",
        "quote_currency": "USD",
        "product_identity_verified": True,
        "strategy_runtime_identity_verified": True,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return candidate_module.PaperRuntimeCandidateIdentityProof(
        **values,
        proof_hash=candidate_module._hash(values),
    )


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE safety_state (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
            kill_switch_active INTEGER NOT NULL,
            kill_switch_reason TEXT NOT NULL,
            circuit_active INTEGER NOT NULL,
            circuit_reason TEXT NOT NULL,
            version INTEGER NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE ledger_events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE
        );
        CREATE TABLE health_state_v2 (
            entity_kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            state TEXT NOT NULL,
            version INTEGER NOT NULL,
            distinct_quarantine_count INTEGER NOT NULL,
            baseline_fingerprint TEXT NOT NULL,
            policy_fingerprint TEXT NOT NULL,
            last_assessment_fingerprint TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            recovery_ack_head TEXT NOT NULL,
            state_hash TEXT NOT NULL,
            PRIMARY KEY(entity_kind,entity_id)
        );
        CREATE TABLE health_recovery_acks_v3 (
            entity_kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            ack_seq INTEGER NOT NULL,
            recovery_id TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            confirmed_by TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            previous_ack_hash TEXT NOT NULL,
            ack_hash TEXT NOT NULL,
            PRIMARY KEY(entity_kind,entity_id,recovery_id),
            UNIQUE(entity_kind,entity_id,ack_seq)
        );
        CREATE TABLE health_bridge_state (
            entity_kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            risk_multiplier TEXT NOT NULL,
            health_state_version INTEGER NOT NULL,
            health_state_fingerprint TEXT NOT NULL,
            baseline_fingerprint TEXT NOT NULL,
            policy_fingerprint TEXT NOT NULL,
            bridge_version INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            state_hash TEXT NOT NULL,
            PRIMARY KEY(entity_kind,entity_id)
        );
        """
    )


def _append_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    payload: dict[str, object],
) -> str:
    row = conn.execute(
        "SELECT event_hash FROM ledger_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    previous = str(row[0]) if row is not None else "GENESIS"
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    occurred_raw = occurred_at.isoformat()
    event_hash = _ledger_hash(
        prev_hash=previous,
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_raw,
        payload_json=payload_json,
    )
    conn.execute(
        "INSERT INTO ledger_events(event_id,event_type,occurred_at,payload_json,prev_hash,event_hash) "
        "VALUES(?,?,?,?,?,?)",
        (event_id, event_type, occurred_raw, payload_json, previous, event_hash),
    )
    return event_hash


def _insert_health(
    conn: sqlite3.Connection,
    *,
    kind: str,
    entity_id: str,
    updated_at: datetime,
    with_ack: bool,
) -> dict[str, object]:
    baseline = _hash({"baseline": f"{kind}:{entity_id}"})
    policy = _hash({"policy": f"{kind}:{entity_id}"})
    assessment = _hash({"assessment": f"{kind}:{entity_id}"})
    recovery_head = "GENESIS"
    ack_count = 0
    if with_ack:
        request_fp = _hash({"request": f"{kind}:{entity_id}"})
        ack_payload = {
            "entity_kind": kind,
            "entity_id": entity_id,
            "ack_seq": 1,
            "recovery_id": f"recovery-{kind.lower()}",
            "request_fingerprint": request_fp,
            "confirmed_by": "human-operator",
            "applied_at": updated_at.isoformat(),
            "previous_ack_hash": "GENESIS",
        }
        recovery_head = _hash(ack_payload)
        conn.execute(
            "INSERT INTO health_recovery_acks_v3(entity_kind,entity_id,ack_seq,recovery_id,"
            "request_fingerprint,confirmed_by,applied_at,previous_ack_hash,ack_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                kind,
                entity_id,
                1,
                ack_payload["recovery_id"],
                request_fp,
                ack_payload["confirmed_by"],
                ack_payload["applied_at"],
                "GENESIS",
                recovery_head,
            ),
        )
        ack_count = 1
    payload = {
        "entity_id": entity_id,
        "entity_kind": kind,
        "state": "HEALTHY",
        "version": 1,
        "distinct_quarantine_count": 0,
        "baseline_fingerprint": baseline,
        "policy_fingerprint": policy,
        "last_assessment_fingerprint": assessment,
        "updated_at": updated_at.isoformat(),
        "recovery_ack_head": recovery_head,
    }
    fingerprint = _hash(payload)
    conn.execute(
        "INSERT INTO health_state_v2(entity_kind,entity_id,state,version,distinct_quarantine_count,"
        "baseline_fingerprint,policy_fingerprint,last_assessment_fingerprint,updated_at,"
        "recovery_ack_head,state_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            kind,
            entity_id,
            "HEALTHY",
            1,
            0,
            baseline,
            policy,
            assessment,
            updated_at.isoformat(),
            recovery_head,
            fingerprint,
        ),
    )
    return {
        "kind": kind,
        "entity_id": entity_id,
        "version": 1,
        "baseline": baseline,
        "policy": policy,
        "fingerprint": fingerprint,
        "recovery_head": recovery_head,
        "ack_count": ack_count,
    }


def _insert_bridge(
    conn: sqlite3.Connection,
    *,
    health: dict[str, object],
    updated_at: datetime,
) -> str:
    payload = {
        "entity_id": health["entity_id"],
        "entity_kind": health["kind"],
        "mode": "NORMAL",
        "risk_multiplier": "1",
        "health_state_version": health["version"],
        "health_state_fingerprint": health["fingerprint"],
        "baseline_fingerprint": health["baseline"],
        "policy_fingerprint": health["policy"],
        "bridge_version": 1,
        "updated_at": updated_at.isoformat(),
    }
    fingerprint = _hash(payload)
    conn.execute(
        "INSERT INTO health_bridge_state(entity_kind,entity_id,mode,risk_multiplier,"
        "health_state_version,health_state_fingerprint,baseline_fingerprint,policy_fingerprint,"
        "bridge_version,updated_at,state_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            health["kind"],
            health["entity_id"],
            "NORMAL",
            "1",
            health["version"],
            health["fingerprint"],
            health["baseline"],
            health["policy"],
            1,
            updated_at.isoformat(),
            fingerprint,
        ),
    )
    return fingerprint


def _build_core(tmp_path: Path) -> Path:
    path = tmp_path / "core.sqlite3"
    conn = _connect(path)
    try:
        _schema(conn)
        commission_at = AT - timedelta(seconds=12)
        reset_at = AT - timedelta(seconds=11)
        strategy_at = AT - timedelta(seconds=10)
        strategy_bridge_at = AT - timedelta(seconds=9)
        portfolio_at = AT - timedelta(seconds=8)
        portfolio_bridge_at = AT - timedelta(seconds=7)

        _append_event(
            conn,
            event_id="commission",
            event_type=truth_module.COMMISSIONING_EVENT,
            occurred_at=commission_at,
            payload={"kill_switch_reason": "R6_HEALTH_R4_EVIDENCE_REQUIRED"},
        )
        _append_event(
            conn,
            event_id="kill-reset",
            event_type="KILL_SWITCH_RESET",
            occurred_at=reset_at,
            payload={"confirmed_by": "human-operator", "safety_state_version": "2"},
        )

        strategy = _insert_health(
            conn,
            kind="STRATEGY",
            entity_id=STRATEGY_ID,
            updated_at=strategy_at,
            with_ack=True,
        )
        _insert_bridge(conn, health=strategy, updated_at=strategy_bridge_at)
        _append_event(
            conn,
            event_id="strategy-health-bridge",
            event_type="HEALTH_BRIDGE_APPLIED",
            occurred_at=strategy_bridge_at,
            payload={
                "entity_kind": "STRATEGY",
                "entity_id": STRATEGY_ID,
                "mode": "NORMAL",
                "health_state_version": "1",
                "health_state_fingerprint": strategy["fingerprint"],
                "bridge_version": "1",
                "safety_state_version": "3",
                "action": "AUTOMATIC_HEALTH_SYNC",
            },
        )

        portfolio = _insert_health(
            conn,
            kind="PORTFOLIO",
            entity_id=PORTFOLIO_HEALTH_ENTITY_ID,
            updated_at=portfolio_at,
            with_ack=False,
        )
        _insert_bridge(conn, health=portfolio, updated_at=portfolio_bridge_at)
        _append_event(
            conn,
            event_id="portfolio-health-bridge",
            event_type="HEALTH_BRIDGE_APPLIED",
            occurred_at=portfolio_bridge_at,
            payload={
                "entity_kind": "PORTFOLIO",
                "entity_id": PORTFOLIO_HEALTH_ENTITY_ID,
                "mode": "NORMAL",
                "health_state_version": "1",
                "health_state_fingerprint": portfolio["fingerprint"],
                "bridge_version": "1",
                "safety_state_version": "4",
                "action": "AUTOMATIC_HEALTH_SYNC",
            },
        )
        conn.execute(
            "INSERT INTO safety_state(singleton_id,kill_switch_active,kill_switch_reason,"
            "circuit_active,circuit_reason,version,updated_at) VALUES(1,0,'',0,'',4,?)",
            (portfolio_bridge_at.isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _rehash_health_row(conn: sqlite3.Connection, kind: str, entity_id: str) -> None:
    row = conn.execute(
        "SELECT * FROM health_state_v2 WHERE entity_kind=? AND entity_id=?",
        (kind, entity_id),
    ).fetchone()
    assert row is not None
    payload = {
        "entity_id": str(row["entity_id"]),
        "entity_kind": str(row["entity_kind"]),
        "state": str(row["state"]),
        "version": int(row["version"]),
        "distinct_quarantine_count": int(row["distinct_quarantine_count"]),
        "baseline_fingerprint": str(row["baseline_fingerprint"]),
        "policy_fingerprint": str(row["policy_fingerprint"]),
        "last_assessment_fingerprint": str(row["last_assessment_fingerprint"]),
        "updated_at": str(row["updated_at"]),
        "recovery_ack_head": str(row["recovery_ack_head"]),
    }
    conn.execute(
        "UPDATE health_state_v2 SET state_hash=? WHERE entity_kind=? AND entity_id=?",
        (_hash(payload), kind, entity_id),
    )


def _rehash_bridge_row(conn: sqlite3.Connection, kind: str, entity_id: str) -> None:
    row = conn.execute(
        "SELECT * FROM health_bridge_state WHERE entity_kind=? AND entity_id=?",
        (kind, entity_id),
    ).fetchone()
    assert row is not None
    payload = {
        "entity_id": str(row["entity_id"]),
        "entity_kind": str(row["entity_kind"]),
        "mode": str(row["mode"]),
        "risk_multiplier": str(row["risk_multiplier"]),
        "health_state_version": int(row["health_state_version"]),
        "health_state_fingerprint": str(row["health_state_fingerprint"]),
        "baseline_fingerprint": str(row["baseline_fingerprint"]),
        "policy_fingerprint": str(row["policy_fingerprint"]),
        "bridge_version": int(row["bridge_version"]),
        "updated_at": str(row["updated_at"]),
    }
    conn.execute(
        "UPDATE health_bridge_state SET state_hash=? WHERE entity_kind=? AND entity_id=?",
        (_hash(payload), kind, entity_id),
    )


def _verify(path: Path, *, candidate=None, observed_at: datetime = AT, policy=None):
    return PaperRuntimeSafetyHealthTruthReader(path).verify_current(
        proof_id="w86-safety-health-proof-test",
        candidate_identity=candidate or _candidate(),
        observed_at=observed_at,
        policy=policy,
    )


def _rehash_proof(value: PaperRuntimeSafetyHealthTruthProof, **changes):
    values = {
        name: getattr(value, name)
        for name in PaperRuntimeSafetyHealthTruthProof.__dataclass_fields__
        if name != "proof_hash"
    }
    values.update(changes)
    return PaperRuntimeSafetyHealthTruthProof(
        **values,
        proof_hash=truth_module._hash(truth_module._proof_payload_from_values(values)),
    )


def test_clean_atomic_safety_health_truth_is_verified_but_never_authorizes(tmp_path):
    proof = _verify(_build_core(tmp_path))

    assert proof.selected_strategy_id == STRATEGY_ID
    assert proof.portfolio_health_entity_id == PORTFOLIO_HEALTH_ENTITY_ID
    assert proof.kill_switch_active is False
    assert proof.circuit_active is False
    assert proof.safety_version == 4
    assert proof.ledger_event_count == 4
    assert proof.strategy_recovery_ack_count == 1
    assert proof.portfolio_recovery_ack_count == 0
    assert proof.ledger_integrity_verified is True
    assert proof.safety_projection_verified is True
    assert proof.strategy_health_verified is True
    assert proof.portfolio_health_verified is True
    assert proof.read_only_core_truth is True
    assert proof.sqlite_snapshot_consistent is True
    assert proof.concurrent_durable_change_detected is False
    assert proof.paper_runtime_ready is False
    assert proof.paper_execution_authorized is False
    assert proof.external_execution_authorized is False
    assert proof.runtime_execution_authorized is False
    assert proof.capital_authority == "NONE"
    assert proof.live_trading == "BLOCKED"
    assert proof.to_dict()["proof_hash"] == proof.proof_hash


def test_engaged_kill_switch_blocks_new_risk_even_with_healthy_health(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        when = AT - timedelta(seconds=1)
        _append_event(
            conn,
            event_id="emergency-kill",
            event_type="KILL_SWITCH_ACTIVATED",
            occurred_at=when,
            payload={"reason": "manual emergency", "safety_state_version": "5"},
        )
        conn.execute(
            "UPDATE safety_state SET kill_switch_active=1,kill_switch_reason='manual emergency',"
            "version=5,updated_at=? WHERE singleton_id=1",
            (when.isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="kill switch is engaged"):
        _verify(path)


def test_engaged_circuit_breaker_blocks_new_risk_even_with_healthy_health(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        when = AT - timedelta(seconds=1)
        _append_event(
            conn,
            event_id="loss-circuit",
            event_type="CIRCUIT_ACTIVATED",
            occurred_at=when,
            payload={"reason": "MAX_DAILY_LOSS:-1000", "safety_state_version": "5"},
        )
        conn.execute(
            "UPDATE safety_state SET circuit_active=1,circuit_reason='MAX_DAILY_LOSS:-1000',"
            "version=5,updated_at=? WHERE singleton_id=1",
            (when.isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="circuit breaker is engaged"):
        _verify(path)


def test_direct_safety_row_tamper_cannot_bypass_ledger_projection(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE safety_state SET updated_at=? WHERE singleton_id=1",
            ((AT - timedelta(seconds=2)).isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="ledger-projected"):
        _verify(path)


def test_core_ledger_hash_chain_tamper_is_rejected(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute("UPDATE ledger_events SET event_hash=? WHERE seq=1", ("f" * 64,))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="event hash mismatch"):
        _verify(path)


def test_duplicate_commissioning_safety_anchor_is_rejected(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        _append_event(
            conn,
            event_id="commission-again",
            event_type=truth_module.COMMISSIONING_EVENT,
            occurred_at=AT - timedelta(seconds=1),
            payload={"kill_switch_reason": "R6_HEALTH_R4_EVIDENCE_REQUIRED"},
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="not initial Safety anchor"):
        _verify(path)


def test_strategy_identity_is_derived_from_candidate_not_caller(tmp_path):
    path = _build_core(tmp_path)
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="required authoritative Health is missing"):
        _verify(path, candidate=_candidate(strategy_id="different-strategy"))


def test_portfolio_health_requires_exact_canonical_identity(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute(
            "DELETE FROM health_bridge_state WHERE entity_kind='PORTFOLIO' AND entity_id=?",
            (PORTFOLIO_HEALTH_ENTITY_ID,),
        )
        conn.execute(
            "DELETE FROM health_state_v2 WHERE entity_kind='PORTFOLIO' AND entity_id=?",
            (PORTFOLIO_HEALTH_ENTITY_ID,),
        )
        other = _insert_health(
            conn,
            kind="PORTFOLIO",
            entity_id="OTHER_PORTFOLIO",
            updated_at=AT - timedelta(seconds=4),
            with_ack=False,
        )
        _insert_bridge(conn, health=other, updated_at=AT - timedelta(seconds=3))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="R6_CRYPTO_PAPER_PORTFOLIO"):
        _verify(path)


def test_authoritative_health_state_hash_tamper_is_rejected(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE health_state_v2 SET state_hash=? WHERE entity_kind='STRATEGY' AND entity_id=?",
            ("0" * 64, STRATEGY_ID),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="Health state hash mismatch"):
        _verify(path)


def test_health_recovery_ack_chain_hash_tamper_is_rejected(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE health_recovery_acks_v3 SET ack_hash=? WHERE entity_kind='STRATEGY' AND entity_id=?",
            ("0" * 64, STRATEGY_ID),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="ACK hash mismatch"):
        _verify(path)


@pytest.mark.parametrize(
    "delta, expected",
    [
        (timedelta(seconds=-3601), "stale"),
        (timedelta(seconds=1), "future"),
    ],
)
def test_strategy_health_stale_or_future_fails_closed(tmp_path, delta, expected):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE health_state_v2 SET updated_at=? WHERE entity_kind='STRATEGY' AND entity_id=?",
            ((AT + delta).isoformat(), STRATEGY_ID),
        )
        _rehash_health_row(conn, "STRATEGY", STRATEGY_ID)
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match=expected):
        _verify(path)


@pytest.mark.parametrize(
    "delta, expected",
    [
        (timedelta(seconds=-3601), "stale"),
        (timedelta(seconds=1), "future"),
    ],
)
def test_portfolio_bridge_stale_or_future_fails_closed(tmp_path, delta, expected):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE health_bridge_state SET updated_at=? WHERE entity_kind='PORTFOLIO' AND entity_id=?",
            ((AT + delta).isoformat(), PORTFOLIO_HEALTH_ENTITY_ID),
        )
        _rehash_bridge_row(conn, "PORTFOLIO", PORTFOLIO_HEALTH_ENTITY_ID)
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match=expected):
        _verify(path)


def test_degraded_bridge_cannot_be_treated_as_runtime_ready(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE health_bridge_state SET mode='REDUCED',risk_multiplier='0.5' "
            "WHERE entity_kind='STRATEGY' AND entity_id=?",
            (STRATEGY_ID,),
        )
        _rehash_bridge_row(conn, "STRATEGY", STRATEGY_ID)
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="not NORMAL"):
        _verify(path)


def test_bridge_must_bind_exact_authoritative_health_fingerprint(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE health_bridge_state SET health_state_fingerprint=? "
            "WHERE entity_kind='STRATEGY' AND entity_id=?",
            ("9" * 64, STRATEGY_ID),
        )
        _rehash_bridge_row(conn, "STRATEGY", STRATEGY_ID)
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="fingerprint binding mismatch"):
        _verify(path)


def test_bridge_must_bind_exact_health_version_and_policy(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE health_bridge_state SET health_state_version=2 "
            "WHERE entity_kind='PORTFOLIO' AND entity_id=?",
            (PORTFOLIO_HEALTH_ENTITY_ID,),
        )
        _rehash_bridge_row(conn, "PORTFOLIO", PORTFOLIO_HEALTH_ENTITY_ID)
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="not synchronized"):
        _verify(path)

    path2 = _build_core(tmp_path / "second")
    conn = _connect(path2)
    try:
        conn.execute(
            "UPDATE health_bridge_state SET policy_fingerprint=? "
            "WHERE entity_kind='PORTFOLIO' AND entity_id=?",
            ("e" * 64, PORTFOLIO_HEALTH_ENTITY_ID),
        )
        _rehash_bridge_row(conn, "PORTFOLIO", PORTFOLIO_HEALTH_ENTITY_ID)
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="baseline/policy"):
        _verify(path2)


def test_candidate_proof_tamper_is_rejected_before_sqlite_truth_is_used(tmp_path):
    path = _build_core(tmp_path)
    candidate = _candidate()
    object.__setattr__(candidate, "proof_hash", "0" * 64)
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="candidate identity proof hash"):
        _verify(path, candidate=candidate)


def test_missing_schema_and_symlinked_core_fail_closed(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute("DROP TABLE health_recovery_acks_v3")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="schema is incomplete"):
        _verify(path)

    good = _build_core(tmp_path / "good")
    link = tmp_path / "linked.sqlite3"
    link.symlink_to(good)
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="symlinked"):
        PaperRuntimeSafetyHealthTruthReader(link)


def test_atomic_reader_rejects_concurrent_data_version_change(tmp_path, monkeypatch):
    path = _build_core(tmp_path)
    real = truth_module._data_version
    calls = 0

    def moving_version(conn):
        nonlocal calls
        calls += 1
        value = real(conn)
        return value if calls == 1 else value + 1

    monkeypatch.setattr(truth_module, "_data_version", moving_version)
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="changed during W86 atomic snapshot"):
        _verify(path)


def test_policy_can_only_tighten_not_weaken_r6_health_freshness():
    assert PaperRuntimeSafetyHealthTruthPolicy(1).max_health_state_age_seconds == 1
    assert PaperRuntimeSafetyHealthTruthPolicy(3600).fingerprint
    with pytest.raises(ValueError, match="\[1, 3600\]"):
        PaperRuntimeSafetyHealthTruthPolicy(3601)
    with pytest.raises(ValueError):
        PaperRuntimeSafetyHealthTruthPolicy(True)


def test_proof_constructor_rejects_authority_escalation_and_self_tamper(tmp_path):
    proof = _verify(_build_core(tmp_path))
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="may not grant"):
        _rehash_proof(proof, paper_runtime_ready=True)
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="proof hash mismatch"):
        replace(proof, proof_hash="0" * 64)


def test_inactive_safety_controls_cannot_retain_hidden_reasons(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE safety_state SET circuit_reason='hidden' WHERE singleton_id=1"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="must not retain reason"):
        _verify(path)


def test_safety_version_gap_in_ledger_fails_closed(tmp_path):
    path = _build_core(tmp_path)
    conn = _connect(path)
    try:
        when = AT - timedelta(seconds=1)
        _append_event(
            conn,
            event_id="bad-version",
            event_type="KILL_SWITCH_ACTIVATED",
            occurred_at=when,
            payload={"reason": "bad history", "safety_state_version": "7"},
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperRuntimeSafetyHealthTruthIntegrityError, match="sequence is not contiguous"):
        _verify(path)
