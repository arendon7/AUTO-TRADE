from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from autotrade.persistence import SQLiteRuntime
from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecision,
    CryptoOperatorDecisionConflict,
    CryptoOperatorDecisionContext,
    CryptoOperatorDecisionExpired,
    CryptoOperatorDecisionIntegrityError,
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
    crypto_operator_confirmation_challenge,
)
from test_r6_paper_crypto_canary_coordinator import NOW, _prepare


def _setup(tmp_path, *, attempt_id: str = "crypto-attempt-001"):
    prepared, _lifecycle = _prepare(tmp_path / "prepare")
    context = CryptoOperatorDecisionContext.from_prepared_package(
        prepared.package,
        attempt_id=attempt_id,
    )
    runtime = SQLiteRuntime(tmp_path / "operator.sqlite3")
    registry = SQLiteCryptoOperatorDecisionRegistry(runtime)
    return prepared, context, registry, runtime


def _issue(registry, context, *, operator_id="operator-001", issued_offset=3, expires_offset=10):
    return registry.record_operator_approval(
        context=context,
        operator_id=operator_id,
        issued_at=NOW + timedelta(seconds=issued_offset),
        expires_at=NOW + timedelta(seconds=expires_offset),
    )


def test_crypto_operator_context_binds_exact_prepared_package(tmp_path) -> None:
    prepared, context, _registry, _runtime = _setup(tmp_path)
    package = prepared.package
    assert context.environment == "PAPER"
    assert context.prepared_package_hash == package.package_hash
    assert context.lifecycle_id == package.lifecycle_id
    assert context.order_id == package.order_id
    assert context.client_order_id == package.client_order_id
    assert context.symbol == "BTC/USD"
    assert context.account_attestation_fingerprint == package.account_attestation_fingerprint
    assert context.asset_attestation_fingerprint == package.asset_attestation_fingerprint
    assert context.product_profile_fingerprint == package.product_profile_fingerprint
    assert context.risk_decision_fingerprint == package.risk_decision_fingerprint
    assert context.crypto_order_fingerprint == package.crypto_order_fingerprint
    assert context.crypto_order_payload_hash == package.crypto_order_payload_hash
    assert context.lifecycle_binding_hash == package.lifecycle_binding_hash
    assert context.lifecycle_control_hash == package.lifecycle_control_hash
    assert context.lifecycle_event_head_hash == package.lifecycle_event_head_hash
    assert context.quantity == package.quantity
    assert context.limit_price == package.limit_price
    assert context.notional == package.notional
    assert context.execution_deadline == package.execution_deadline
    assert len(context.preparation_hash) == 64
    assert CryptoOperatorDecisionContext.from_dict(context.to_dict()) == context
    challenge = crypto_operator_confirmation_challenge(context)
    assert challenge == f"APPROVE CRYPTO PAPER BTC/USD {context.preparation_hash[:12]}"


def test_crypto_operator_approval_is_human_exact_short_lived_and_non_network(tmp_path) -> None:
    _prepared, context, registry, _runtime = _setup(tmp_path)
    state = _issue(registry, context)
    decision = state.decision
    assert state.status is CryptoOperatorDecisionStatus.ISSUED
    assert state.consumed_at is None
    assert decision.context == context
    assert decision.operator_id == "operator-001"
    assert decision.source == "HUMAN_OPERATOR"
    assert decision.action == "APPROVE_SINGLE_CRYPTO_PAPER_CANARY_ENTRY"
    assert decision.issued_at == NOW + timedelta(seconds=3)
    assert decision.expires_at == NOW + timedelta(seconds=10)
    assert decision.expires_at <= context.execution_deadline
    assert len(decision.decision_hash) == 64
    assert decision.is_valid_at(NOW + timedelta(seconds=4)) is True
    assert decision.is_valid_at(NOW + timedelta(seconds=10)) is False


def test_exact_duplicate_issuance_is_idempotent_but_changed_evidence_conflicts(tmp_path) -> None:
    _prepared, context, registry, _runtime = _setup(tmp_path)
    first = _issue(registry, context)
    second = _issue(registry, context)
    assert second == first

    with pytest.raises(CryptoOperatorDecisionConflict, match="different operator-decision evidence"):
        _issue(registry, context, operator_id="operator-002")

    with pytest.raises(CryptoOperatorDecisionConflict, match="different operator-decision evidence"):
        registry.record_operator_approval(
            context=context,
            operator_id="operator-001",
            issued_at=NOW + timedelta(seconds=4),
            expires_at=NOW + timedelta(seconds=10),
        )


def test_operator_decision_cannot_predate_or_outlive_prepared_package(tmp_path) -> None:
    _prepared, context, registry, _runtime = _setup(tmp_path)
    with pytest.raises(ValueError, match="may not predate"):
        registry.record_operator_approval(
            context=context,
            operator_id="operator-001",
            issued_at=context.prepared_at - timedelta(seconds=1),
            expires_at=context.prepared_at + timedelta(seconds=1),
        )

    with pytest.raises(ValueError, match="may not outlive prepared package"):
        registry.record_operator_approval(
            context=context,
            operator_id="operator-001",
            issued_at=NOW + timedelta(seconds=3),
            expires_at=context.execution_deadline + timedelta(microseconds=1),
        )


def test_operator_decision_ttl_is_bounded_even_if_package_deadline_is_longer(tmp_path) -> None:
    _prepared, context, _registry, _runtime = _setup(tmp_path)
    # Build a structurally valid direct decision with a >2 minute window to prove the object rejects it.
    with pytest.raises(ValueError, match="<=2 minutes"):
        CryptoOperatorDecision(
            context=replace(
                context,
                execution_deadline=NOW + timedelta(minutes=4),
                risk_decision_valid_until=NOW + timedelta(minutes=4),
                preparation_hash="0" * 64,
            ),
            operator_id="operator-001",
            source="HUMAN_OPERATOR",
            action="APPROVE_SINGLE_CRYPTO_PAPER_CANARY_ENTRY",
            issued_at=NOW + timedelta(seconds=3),
            expires_at=NOW + timedelta(minutes=3),
            decision_hash="0" * 64,
        )


def test_operator_decision_is_consumed_once_for_exact_attempt(tmp_path) -> None:
    _prepared, context, registry, _runtime = _setup(tmp_path)
    issued = _issue(registry, context)
    consumed = registry.consume(
        decision=issued.decision,
        attempt_id=context.attempt_id,
        now=NOW + timedelta(seconds=4),
    )
    assert consumed.status is CryptoOperatorDecisionStatus.CONSUMED
    assert consumed.consumed_attempt_id == context.attempt_id
    assert consumed.consumed_at == NOW + timedelta(seconds=4)

    replay = registry.consume(
        decision=issued.decision,
        attempt_id=context.attempt_id,
        now=NOW + timedelta(seconds=5),
    )
    assert replay == consumed

    with pytest.raises(CryptoOperatorDecisionConflict, match="another attempt"):
        registry.consume(
            decision=issued.decision,
            attempt_id="crypto-attempt-002",
            now=NOW + timedelta(seconds=5),
        )


def test_operator_decision_cannot_be_consumed_before_issue_or_after_expiry(tmp_path) -> None:
    _prepared, context, registry, _runtime = _setup(tmp_path)
    issued = _issue(registry, context)
    with pytest.raises(CryptoOperatorDecisionExpired, match="expired or not yet valid"):
        registry.consume(
            decision=issued.decision,
            attempt_id=context.attempt_id,
            now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(CryptoOperatorDecisionExpired, match="expired or not yet valid"):
        registry.consume(
            decision=issued.decision,
            attempt_id=context.attempt_id,
            now=NOW + timedelta(seconds=10),
        )


def test_operator_context_changes_produce_distinct_authority_identity(tmp_path) -> None:
    _prepared, context, _registry, _runtime = _setup(tmp_path)
    other_attempt = CryptoOperatorDecisionContext.from_dict(
        {
            **context.to_dict(),
            "attempt_id": "crypto-attempt-002",
            # Deliberately stale hash must be rejected rather than silently rebinding.
        }
    )
    assert other_attempt is None  # pragma: no cover
