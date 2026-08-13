from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from autotrade.persistence import SQLiteRuntime
from autotrade.brokers.alpaca_paper_crypto_execution_attempt import SQLiteCryptoExecutionAttemptRegistry
from autotrade.brokers.alpaca_paper_crypto_execution_bridge import (
    CryptoPaperExecutionBridge,
    CryptoPaperExecutionStageResult,
)
from autotrade.brokers.alpaca_paper_crypto_execution_simulation import (
    CryptoExecutionSimulationBlocked,
    CryptoExecutionSimulationResult,
    CryptoPaperExecutionSimulationCoordinator,
)
from autotrade.brokers.alpaca_paper_crypto_order import CryptoOrderRole
from test_r6_paper_crypto_execution_bridge import _checkpoint, _stage
from test_r6_paper_crypto_execution_simulation import _execute
from test_r6_paper_crypto_final_guard import _setup


def _attempts(tmp_path):
    return SQLiteCryptoExecutionAttemptRegistry(SQLiteRuntime(tmp_path / "attempt.sqlite3"))


def test_crypto_execution_bridge_constructor_rejects_non_authoritative_oms() -> None:
    with pytest.raises(TypeError, match="authoritative OrderManagementSystem"):
        CryptoPaperExecutionBridge(oms=object())  # type: ignore[arg-type]


def test_simulation_coordinator_constructor_requires_each_authority_type(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    attempts = _attempts(tmp_path)
    bridge = CryptoPaperExecutionBridge(oms=ctx.oms)

    with pytest.raises(TypeError, match="execution bridge"):
        CryptoPaperExecutionSimulationCoordinator(
            execution_bridge=object(),  # type: ignore[arg-type]
            final_guard=ctx.guard,
            attempt_registry=attempts,
        )
    with pytest.raises(TypeError, match="Final Freshness"):
        CryptoPaperExecutionSimulationCoordinator(
            execution_bridge=bridge,
            final_guard=object(),  # type: ignore[arg-type]
            attempt_registry=attempts,
        )
    with pytest.raises(TypeError, match="execution-attempt registry"):
        CryptoPaperExecutionSimulationCoordinator(
            execution_bridge=bridge,
            final_guard=ctx.guard,
            attempt_registry=object(),  # type: ignore[arg-type]
        )


def test_simulation_result_rejects_wrong_phase_call_count_and_restart_policy(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    good = _execute(ctx, _attempts(tmp_path))

    with pytest.raises(ValueError, match="PRE_IO"):
        CryptoExecutionSimulationResult(
            checkpoint=good.checkpoint,
            pre_io_attestation=good.checkpoint.pre_consume,
            write_receipt=good.write_receipt,
            handoff_id=good.handoff_id,
            simulated_transport_calls=1,
            restart_action="RECONCILE_ONLY",
        )
    with pytest.raises(ValueError, match="exactly one"):
        replace(good, simulated_transport_calls=2)
    with pytest.raises(ValueError, match="RECONCILE_ONLY"):
        replace(good, restart_action="CONTINUE_CERTIFIED_LIFECYCLE")


def test_execution_stage_result_rejects_order_handoff_identity_drift(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    checkpoint = _checkpoint(ctx, tmp_path)
    good = _stage(ctx, checkpoint)
    # The upstream ExternalSubmissionHandoff is itself hash-bound. Use the
    # minimal interface consumed by the bridge result so this test reaches the
    # bridge's own identity check rather than failing in the upstream fixture.
    mismatched_handoff = SimpleNamespace(order_id="other-order")

    with pytest.raises(ValueError, match="order/handoff mismatch"):
        CryptoPaperExecutionStageResult(
            package_hash=good.package_hash,
            operator_decision_hash=good.operator_decision_hash,
            attempt_id=good.attempt_id,
            checkpoint_hash=good.checkpoint_hash,
            order=good.order,
            handoff=mismatched_handoff,  # type: ignore[arg-type]
        )


def test_simulation_binding_validator_rejects_role_and_each_operator_binding_drift(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    validator = CryptoPaperExecutionSimulationCoordinator._validate_bindings

    wrong_role = SimpleNamespace(
        role=CryptoOrderRole.PROTECTION,
        client_order_id=ctx.broker_order.client_order_id,
        fingerprint=ctx.broker_order.fingerprint,
    )
    with pytest.raises(CryptoExecutionSimulationBlocked, match="ENTRY only"):
        validator(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            broker_order=wrong_role,  # type: ignore[arg-type]
        )

    context = ctx.operator_decision.context
    broker = SimpleNamespace(
        role=CryptoOrderRole.ENTRY,
        client_order_id=ctx.broker_order.client_order_id,
        fingerprint=ctx.broker_order.fingerprint,
    )
    base_context = {
        "prepared_package_hash": context.prepared_package_hash,
        "lifecycle_id": context.lifecycle_id,
        "order_id": context.order_id,
        "client_order_id": context.client_order_id,
    }
    context_cases = (
        ({**base_context, "prepared_package_hash": "f" * 64}, "package hash mismatch"),
        ({**base_context, "lifecycle_id": "different-life"}, "package identity mismatch"),
        ({**base_context, "order_id": "different-order"}, "package identity mismatch"),
        ({**base_context, "client_order_id": "different-client-order"}, "client_order_id mismatch"),
    )
    for values, message in context_cases:
        fake_decision = SimpleNamespace(context=SimpleNamespace(**values))
        with pytest.raises(CryptoExecutionSimulationBlocked, match=message):
            validator(
                package=ctx.package,
                operator_decision=fake_decision,  # type: ignore[arg-type]
                broker_order=broker,  # type: ignore[arg-type]
            )

    wrong_fingerprint = SimpleNamespace(
        role=CryptoOrderRole.ENTRY,
        client_order_id=ctx.broker_order.client_order_id,
        fingerprint="f" * 64,
    )
    with pytest.raises(CryptoExecutionSimulationBlocked, match="broker order differs"):
        validator(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            broker_order=wrong_fingerprint,  # type: ignore[arg-type]
        )


def test_simulation_checkpoint_validator_rejects_every_identity_rebinding(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    checkpoint = _checkpoint(ctx, tmp_path)
    validator = CryptoPaperExecutionSimulationCoordinator._validate_checkpoint

    base = {
        "attempt_id": checkpoint.attempt_id,
        "package_hash": checkpoint.package_hash,
        "preparation_hash": checkpoint.preparation_hash,
        "operator_decision_hash": checkpoint.operator_decision_hash,
        "order_id": checkpoint.order_id,
        "client_order_id": checkpoint.client_order_id,
    }
    cases = (
        ({**base, "attempt_id": "different-attempt"}, "checkpoint attempt mismatch"),
        ({**base, "package_hash": "f" * 64}, "checkpoint package mismatch"),
        ({**base, "preparation_hash": "e" * 64}, "checkpoint preparation mismatch"),
        ({**base, "operator_decision_hash": "d" * 64}, "checkpoint operator-decision mismatch"),
        ({**base, "order_id": "different-order"}, "checkpoint order mismatch"),
        ({**base, "client_order_id": "different-client-order"}, "checkpoint client_order_id mismatch"),
    )
    for values, message in cases:
        fake_checkpoint = SimpleNamespace(**values)
        with pytest.raises(CryptoExecutionSimulationBlocked, match=message):
            validator(
                checkpoint=fake_checkpoint,  # type: ignore[arg-type]
                package=ctx.package,
                operator_decision=ctx.operator_decision,
                broker_order=ctx.broker_order,
            )
