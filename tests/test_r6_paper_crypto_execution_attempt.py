from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json

import pytest

from autotrade.persistence import SQLiteRuntime
from autotrade.brokers.alpaca_paper_crypto_execution_attempt import (
    CryptoExecutionAttemptConflict,
    CryptoExecutionAttemptIntegrityError,
    SQLiteCryptoExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_final_guard import (
    CryptoFinalWritePhase,
)
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_final_guard import _authorize_pre, _setup


def _registry(tmp_path):
    runtime = SQLiteRuntime(tmp_path / "crypto-attempt.sqlite3")
    return runtime, SQLiteCryptoExecutionAttemptRegistry(runtime)


def test_preconsume_checkpoint_roundtrips_exact_attestation(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)
    _, registry = _registry(tmp_path)

    checkpoint = registry.record_pre_consume(pre)
    loaded = registry.get(pre.attempt_id)
    by_package = registry.get_for_package(pre.package_hash)

    assert checkpoint == loaded == by_package
    assert loaded.pre_consume == pre
    assert loaded.pre_consume.phase is CryptoFinalWritePhase.PRE_CONSUME
    assert loaded.attempt_id == ctx.operator_decision.context.attempt_id
    assert loaded.package_hash == ctx.package.package_hash
    assert loaded.operator_decision_hash == ctx.operator_decision.decision_hash


def test_identical_checkpoint_replay_is_idempotent(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)
    _, registry = _registry(tmp_path)

    first = registry.record_pre_consume(pre)
    second = registry.record_pre_consume(pre)

    assert second == first


def test_checkpoint_rejects_non_preconsume_attestation(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)
    _, registry = _registry(tmp_path)
    invalid = replace(
        pre,
        phase=CryptoFinalWritePhase.PRE_IO,
        previous_attestation_hash=pre.attestation_hash,
    )
    with pytest.raises((CryptoExecutionAttemptConflict, ValueError)):
        registry.record_pre_consume(invalid)


def test_same_package_cannot_be_rebound_after_checkpoint(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)
    _, registry = _registry(tmp_path)
    registry.record_pre_consume(pre)

    # Any changed evidence for the same package must fail closed. We deliberately
    # alter a stable identity while retaining the package hash; the registry must
    # reject before a second attempt can replace the durable checkpoint.
    changed = replace(pre, order_id="different-order")
    with pytest.raises((CryptoExecutionAttemptConflict, ValueError)):
        registry.record_pre_consume(changed)


def test_checkpoint_detects_record_hash_tampering(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)
    runtime, registry = _registry(tmp_path)
    registry.record_pre_consume(pre)

    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_execution_attempts SET record_hash = ? WHERE attempt_id = ?",
            ("f" * 64, pre.attempt_id),
        )
    finally:
        conn.close()

    with pytest.raises(CryptoExecutionAttemptIntegrityError):
        registry.get(pre.attempt_id)


def test_checkpoint_detects_persisted_preconsume_tampering_even_if_row_hash_is_rewritten(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)
    runtime, registry = _registry(tmp_path)
    registry.record_pre_consume(pre)

    conn = runtime.connect()
    try:
        row = conn.execute(
            "SELECT record_json FROM alpaca_crypto_execution_attempts WHERE attempt_id = ?",
            (pre.attempt_id,),
        ).fetchone()
        payload = json.loads(str(row["record_json"]))
        payload["pre_consume"]["fresh_account_fingerprint"] = "a" * 64
        tampered = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        # A malicious rewrite of both JSON and outer row hash still cannot forge
        # the inner final-guard attestation_hash.
        conn.execute(
            "UPDATE alpaca_crypto_execution_attempts SET record_json = ?, record_hash = ? WHERE attempt_id = ?",
            (tampered, "b" * 64, pre.attempt_id),
        )
    finally:
        conn.close()

    with pytest.raises(CryptoExecutionAttemptIntegrityError):
        registry.get(pre.attempt_id)


def test_restart_after_operator_consumption_recovers_same_preconsume_for_preio(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)
    runtime, registry = _registry(tmp_path)
    checkpoint = registry.record_pre_consume(pre)

    # Simulate the crash-sensitive point: human authority is consumed after the
    # PRE_CONSUME checkpoint, but no lifecycle UNKNOWN transition and no network
    # I/O have happened yet.
    ctx.operator_registry.consume(
        decision=ctx.operator_decision,
        attempt_id=checkpoint.attempt_id,
        now=NOW + timedelta(seconds=4, milliseconds=250),
    )

    restarted = SQLiteCryptoExecutionAttemptRegistry(runtime)
    recovered = restarted.get(checkpoint.attempt_id)
    assert recovered == checkpoint

    # The existing OMS stage is idempotent for the exact handoff, so the same
    # attempt can continue after restart without creating a second authority.
    ctx.oms.stage_external_submission(
        order_id=ctx.package.order_id,
        handoff_id="c" * 64,
        decision=ctx.decision,
        market=ctx.prepared_market.market,
        now=NOW + timedelta(seconds=4, milliseconds=300),
    )
    ctx.lifecycle.mark_entry_submission_unknown(
        ctx.package.lifecycle_id,
        at=NOW + timedelta(seconds=4, milliseconds=350),
    )

    final = ctx.guard.authorize(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        broker_order=ctx.broker_order,
        lifecycle=ctx.lifecycle,
        prepared_account=ctx.prepared_account,
        prepared_asset=ctx.prepared_asset,
        prepared_product_profile=ctx.prepared_profile,
        fresh_account=ctx.fresh_account,
        fresh_asset=ctx.fresh_asset,
        fresh_product_profile=ctx.fresh_profile,
        fresh_market=ctx.fresh_market,
        fresh_flat_account=ctx.fresh_flat,
        now=NOW + timedelta(seconds=4, milliseconds=400),
        phase=CryptoFinalWritePhase.PRE_IO,
        expected_attempt_id=checkpoint.attempt_id,
        previous_attestation=recovered.pre_consume,
    )

    assert final.phase is CryptoFinalWritePhase.PRE_IO
    assert final.previous_attestation_hash == pre.attestation_hash
    assert final.attempt_id == checkpoint.attempt_id


def test_wrong_attempt_cannot_fetch_or_replace_checkpoint(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    pre = _authorize_pre(ctx)
    _, registry = _registry(tmp_path)
    registry.record_pre_consume(pre)

    with pytest.raises(KeyError):
        registry.get("crypto-final-attempt-999")
    assert registry.get_for_package(pre.package_hash).attempt_id == pre.attempt_id
