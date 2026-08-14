from __future__ import annotations

from datetime import timedelta

import pytest

from autotrade.brokers.alpaca_paper_crypto_protection_execution_attempt import (
    CryptoProtectionExecutionAttemptConflict,
    CryptoProtectionExecutionAttemptIntegrityError,
    SQLiteCryptoProtectionExecutionAttemptRegistry,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_protection_final_guard import _advance_to_preio, _preconsume


def test_protection_preconsume_checkpoint_is_durable_idempotent_and_restartable(tmp_path) -> None:
    setup_with_pre = _preconsume(tmp_path / "case")
    setup, pre = setup_with_pre[:-1], setup_with_pre[-1]
    _ctx, _entry_reconciliation, _market, _decision, prepared, operator_registry, operator_decision, _guard = setup

    db_path = tmp_path / "protection-attempt.sqlite3"
    registry = SQLiteCryptoProtectionExecutionAttemptRegistry(SQLiteRuntime(db_path))
    first = registry.record_pre_consume(pre)
    replay = registry.record_pre_consume(pre)

    assert replay == first
    assert first.package_hash == prepared.package.package_hash
    assert first.operator_decision_hash == operator_decision.decision_hash
    assert first.attempt_id == operator_decision.context.attempt_id
    assert first.pre_consume == pre

    operator_registry.consume(
        decision=operator_decision,
        attempt_id=operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=7, milliseconds=100),
    )

    restarted = SQLiteCryptoProtectionExecutionAttemptRegistry(SQLiteRuntime(db_path))
    assert restarted.get(first.attempt_id) == first
    assert restarted.get_for_package(first.package_hash) == first


def test_protection_execution_checkpoint_rejects_preio_as_new_preconsume(tmp_path) -> None:
    setup_with_pre = _preconsume(tmp_path / "preio")
    setup, pre = setup_with_pre[:-1], setup_with_pre[-1]
    final = _advance_to_preio(setup, pre)

    registry = SQLiteCryptoProtectionExecutionAttemptRegistry(
        SQLiteRuntime(tmp_path / "preio-attempt.sqlite3")
    )
    with pytest.raises(CryptoProtectionExecutionAttemptConflict, match="PRE_CONSUME"):
        registry.record_pre_consume(final)


def test_protection_execution_checkpoint_detects_durable_tamper(tmp_path) -> None:
    setup_with_pre = _preconsume(tmp_path / "tamper")
    pre = setup_with_pre[-1]
    runtime = SQLiteRuntime(tmp_path / "tamper-attempt.sqlite3")
    registry = SQLiteCryptoProtectionExecutionAttemptRegistry(runtime)
    checkpoint = registry.record_pre_consume(pre)

    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_protection_execution_attempts SET record_hash = ? WHERE attempt_id = ?",
            ("0" * 64, checkpoint.attempt_id),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(CryptoProtectionExecutionAttemptIntegrityError, match="invalid durable"):
        registry.get(checkpoint.attempt_id)


def test_protection_execution_registry_rejects_invalid_api_inputs(tmp_path) -> None:
    with pytest.raises(TypeError, match="SQLiteRuntime"):
        SQLiteCryptoProtectionExecutionAttemptRegistry(object())  # type: ignore[arg-type]

    registry = SQLiteCryptoProtectionExecutionAttemptRegistry(
        SQLiteRuntime(tmp_path / "invalid-inputs.sqlite3")
    )
    with pytest.raises(TypeError, match="PRE_CONSUME attestation"):
        registry.record_pre_consume(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="attempt_id is invalid"):
        registry.get("bad id with spaces")
    with pytest.raises(ValueError, match="package_hash must be lowercase SHA-256"):
        registry.get_for_package("not-a-sha256")


def test_protection_execution_registry_missing_lookups_fail_closed(tmp_path) -> None:
    registry = SQLiteCryptoProtectionExecutionAttemptRegistry(
        SQLiteRuntime(tmp_path / "missing.sqlite3")
    )
    with pytest.raises(KeyError):
        registry.get("missing-attempt")
    with pytest.raises(KeyError):
        registry.get_for_package("a" * 64)


@pytest.mark.parametrize(
    "corrupt_json",
    [
        "[]",
        "{}",
        "{}\n",
    ],
)
def test_protection_execution_checkpoint_rejects_noncanonical_durable_json(
    tmp_path,
    corrupt_json: str,
) -> None:
    setup_with_pre = _preconsume(tmp_path / "json")
    pre = setup_with_pre[-1]
    runtime = SQLiteRuntime(tmp_path / "json-attempt.sqlite3")
    registry = SQLiteCryptoProtectionExecutionAttemptRegistry(runtime)
    checkpoint = registry.record_pre_consume(pre)

    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_protection_execution_attempts SET record_json = ? WHERE attempt_id = ?",
            (corrupt_json, checkpoint.attempt_id),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(CryptoProtectionExecutionAttemptIntegrityError, match="invalid durable"):
        registry.get(checkpoint.attempt_id)


def test_protection_execution_checkpoint_rejects_sql_index_drift(tmp_path) -> None:
    setup_with_pre = _preconsume(tmp_path / "index")
    pre = setup_with_pre[-1]
    runtime = SQLiteRuntime(tmp_path / "index-attempt.sqlite3")
    registry = SQLiteCryptoProtectionExecutionAttemptRegistry(runtime)
    checkpoint = registry.record_pre_consume(pre)

    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_protection_execution_attempts SET lifecycle_id = ? WHERE attempt_id = ?",
            ("different-lifecycle", checkpoint.attempt_id),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(CryptoProtectionExecutionAttemptIntegrityError, match="invalid durable"):
        registry.get(checkpoint.attempt_id)
