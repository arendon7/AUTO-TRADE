from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json

import pytest

from autotrade.persistence import SQLiteRuntime
from autotrade.brokers import alpaca_paper_crypto_execution_attempt as attempt_module
from autotrade.brokers.alpaca_paper_crypto_execution_attempt import (
    CryptoExecutionAttemptCheckpoint,
    CryptoExecutionAttemptConflict,
    CryptoExecutionAttemptIntegrityError,
    SQLiteCryptoExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_final_guard import CryptoFinalWritePhase
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_final_guard import _advance_to_pre_io, _authorize_pre, _setup


def _registry(tmp_path):
    runtime = SQLiteRuntime(tmp_path / "attempt-adversarial.sqlite3")
    return runtime, SQLiteCryptoExecutionAttemptRegistry(runtime)


def _record(tmp_path):
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)
    runtime, registry = _registry(tmp_path)
    checkpoint = registry.record_pre_consume(pre)
    return ctx, pre, runtime, registry, checkpoint


def _row(runtime, attempt_id):
    conn = runtime.connect()
    try:
        return conn.execute(
            "SELECT * FROM alpaca_crypto_execution_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
    finally:
        conn.close()


def _mutate_record_json(runtime, attempt_id, mutate, *, canonical=True):
    conn = runtime.connect()
    try:
        row = conn.execute(
            "SELECT record_json FROM alpaca_crypto_execution_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        payload = json.loads(str(row["record_json"]))
        mutate(payload)
        if canonical:
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        else:
            raw = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False)
        conn.execute(
            "UPDATE alpaca_crypto_execution_attempts SET record_json = ? WHERE attempt_id = ?",
            (raw, attempt_id),
        )
    finally:
        conn.close()


def _rehash_attestation_payload(payload):
    inner = dict(payload["pre_consume"])
    hash_payload = dict(inner)
    hash_payload.pop("attestation_hash")
    inner["attestation_hash"] = sha256(
        json.dumps(hash_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    payload["pre_consume"] = inner


def test_checkpoint_constructor_rejects_wrong_object_phase_time_and_hash(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)

    with pytest.raises(ValueError, match="CryptoFinalWriteAttestation"):
        CryptoExecutionAttemptCheckpoint(pre_consume=object(), recorded_at=NOW, record_hash="0" * 64)  # type: ignore[arg-type]

    final = _advance_to_pre_io(ctx, pre)
    with pytest.raises(ValueError, match="PRE_CONSUME"):
        CryptoExecutionAttemptCheckpoint(
            pre_consume=final,
            recorded_at=final.observed_at,
            record_hash="0" * 64,
        )

    wrong_time = pre.observed_at + timedelta(milliseconds=1)
    with pytest.raises(ValueError, match="recorded_at"):
        CryptoExecutionAttemptCheckpoint(
            pre_consume=pre,
            recorded_at=wrong_time,
            record_hash=attempt_module._checkpoint_hash(pre, wrong_time),
        )

    with pytest.raises(ValueError, match="checkpoint hash mismatch"):
        CryptoExecutionAttemptCheckpoint(
            pre_consume=pre,
            recorded_at=pre.observed_at,
            record_hash="f" * 64,
        )


def test_record_preconsume_rejects_wrong_type_and_valid_preio(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)
    _, registry = _registry(tmp_path)
    with pytest.raises(TypeError, match="PRE_CONSUME"):
        registry.record_pre_consume(object())  # type: ignore[arg-type]

    final = _advance_to_pre_io(ctx, pre)
    with pytest.raises(CryptoExecutionAttemptConflict, match="only PRE_CONSUME"):
        registry.record_pre_consume(final)


def test_get_validators_and_missing_rows_fail_closed(tmp_path) -> None:
    _, registry = _registry(tmp_path)
    with pytest.raises(ValueError, match="attempt_id"):
        registry.get(" bad attempt ")
    with pytest.raises(KeyError):
        registry.get("missing-attempt")
    with pytest.raises(ValueError, match="package_hash"):
        registry.get_for_package("not-a-hash")
    with pytest.raises(KeyError):
        registry.get_for_package("a" * 64)


def test_record_detects_uniqueness_indexes_disagree_before_parsing_rows(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)
    runtime, registry = _registry(tmp_path)
    conn = runtime.connect()
    try:
        # Two distinct rows each collide with a different immutable identity of
        # the incoming checkpoint. The registry must fail before trusting either.
        conn.execute(
            "INSERT INTO alpaca_crypto_execution_attempts VALUES (?, ?, ?, ?, ?, ?)",
            (pre.attempt_id, "1" * 64, "2" * 64, NOW.isoformat(), "{}", "3" * 64),
        )
        conn.execute(
            "INSERT INTO alpaca_crypto_execution_attempts VALUES (?, ?, ?, ?, ?, ?)",
            ("other-attempt", pre.preparation_hash, "4" * 64, NOW.isoformat(), "{}", "5" * 64),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoExecutionAttemptIntegrityError, match="uniqueness indexes disagree"):
        registry.record_pre_consume(pre)


def test_row_primary_preparation_package_and_timestamp_indexes_are_verified(tmp_path) -> None:
    for field, value, lookup in (
        ("attempt_id", "different-attempt", "different-attempt"),
        ("preparation_hash", "1" * 64, None),
        ("package_hash", "2" * 64, None),
        ("recorded_at", (NOW + timedelta(seconds=1)).isoformat(), None),
    ):
        case = tmp_path / field
        _, pre, runtime, registry, _ = _record(case)
        conn = runtime.connect()
        try:
            conn.execute(
                f"UPDATE alpaca_crypto_execution_attempts SET {field} = ? WHERE attempt_id = ?",
                (value, pre.attempt_id),
            )
        finally:
            conn.close()
        target = lookup or pre.attempt_id
        with pytest.raises(CryptoExecutionAttemptIntegrityError):
            registry.get(target)


def test_row_json_must_be_canonical_object_with_exact_keys_and_nested_object(tmp_path) -> None:
    cases = (
        ("root-list", lambda payload: [payload], True),
        ("extra-key", lambda payload: {**payload, "extra": True}, True),
        ("nested-list", lambda payload: {**payload, "pre_consume": []}, True),
    )
    for name, transform, canonical in cases:
        case = tmp_path / name
        _, pre, runtime, registry, _ = _record(case)
        conn = runtime.connect()
        try:
            row = conn.execute(
                "SELECT record_json FROM alpaca_crypto_execution_attempts WHERE attempt_id = ?",
                (pre.attempt_id,),
            ).fetchone()
            payload = json.loads(str(row["record_json"]))
            changed = transform(payload)
            raw = json.dumps(changed, sort_keys=True, separators=(",", ":"), allow_nan=False)
            conn.execute(
                "UPDATE alpaca_crypto_execution_attempts SET record_json = ? WHERE attempt_id = ?",
                (raw, pre.attempt_id),
            )
        finally:
            conn.close()
        with pytest.raises(CryptoExecutionAttemptIntegrityError):
            registry.get(pre.attempt_id)

    case = tmp_path / "whitespace"
    _, pre, runtime, registry, _ = _record(case)
    _mutate_record_json(runtime, pre.attempt_id, lambda payload: None, canonical=False)
    with pytest.raises(CryptoExecutionAttemptIntegrityError, match="invalid durable"):
        registry.get(pre.attempt_id)


def test_persisted_attestation_requires_exact_schema_enums_and_scalars(tmp_path) -> None:
    mutations = (
        ("missing-key", lambda p: p["pre_consume"].pop("risk_decision_id"), False),
        ("bad-phase", lambda p: p["pre_consume"].__setitem__("phase", "BOGUS"), True),
        ("bad-status", lambda p: p["pre_consume"].__setitem__("operator_status", "BOGUS"), True),
        ("bool-int", lambda p: p["pre_consume"].__setitem__("safety_state_version", True), True),
        ("empty-string", lambda p: p["pre_consume"].__setitem__("order_id", ""), True),
        ("bad-optional", lambda p: p["pre_consume"].__setitem__("previous_attestation_hash", 7), True),
        ("bad-time-type", lambda p: p["pre_consume"].__setitem__("observed_at", 7), True),
        ("noncanonical-time", lambda p: p["pre_consume"].__setitem__("observed_at", "2026-08-13T00:00:04.200000Z"), True),
    )
    for name, mutate, rehash in mutations:
        case = tmp_path / name
        _, pre, runtime, registry, _ = _record(case)
        def apply(payload):
            mutate(payload)
            if rehash and isinstance(payload.get("pre_consume"), dict):
                _rehash_attestation_payload(payload)
        _mutate_record_json(runtime, pre.attempt_id, apply)
        with pytest.raises(CryptoExecutionAttemptIntegrityError):
            registry.get(pre.attempt_id)


def test_checkpoint_rejects_invalid_attempt_id_even_with_valid_inner_attestation_hash(tmp_path) -> None:
    _, pre, _, _, _ = _record(tmp_path)
    payload = attempt_module._attestation_dict(pre)
    payload["attempt_id"] = " bad attempt "
    hash_payload = dict(payload)
    hash_payload.pop("attestation_hash")
    payload["attestation_hash"] = sha256(
        json.dumps(hash_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    changed = attempt_module._attestation_from_dict(payload)
    with pytest.raises(ValueError, match="attempt_id"):
        attempt_module._build_checkpoint(changed)


def test_private_parsers_reject_non_object_invalid_int_optional_and_datetime() -> None:
    with pytest.raises(ValueError, match="root must be object"):
        attempt_module._strict_json_object("[]")
    with pytest.raises(ValueError, match="must be integer"):
        attempt_module._strict_int(True, "value")
    with pytest.raises(ValueError, match="must be integer"):
        attempt_module._strict_int("1", "value")
    assert attempt_module._optional_str(None) is None
    with pytest.raises(ValueError, match="optional hash"):
        attempt_module._optional_str(1)
    with pytest.raises(ValueError, match="ISO datetime"):
        attempt_module._datetime(123, "when")
    with pytest.raises(ValueError, match="canonical UTC"):
        attempt_module._datetime("2026-08-13T01:00:00Z", "when")
    with pytest.raises(ValueError, match="timezone-aware"):
        attempt_module._iso(NOW.replace(tzinfo=None))


def test_corrupt_json_syntax_is_wrapped_as_integrity_error(tmp_path) -> None:
    _, pre, runtime, registry, _ = _record(tmp_path)
    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_execution_attempts SET record_json = ? WHERE attempt_id = ?",
            ("{not-json", pre.attempt_id),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoExecutionAttemptIntegrityError, match="invalid durable"):
        registry.get(pre.attempt_id)
