from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from autotrade.first_canary_execution_gate import (
    FirstCanaryExecutionBlocked,
    FirstCanaryExecutionInputs,
    FirstCanaryFinalEvidence,
    execute_first_canary_once,
)
from autotrade.persistence import SQLiteRuntime
import autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io as cold_pre_io
from test_r6_first_canary_execution_gate import (
    NOW,
    _AmbiguousDelegate,
    _final,
    _prepare_session,
)


class _UnavailableReconciler:
    def __init__(self) -> None:
        self.calls = 0

    def reconcile(self, **_kwargs):
        self.calls += 1
        raise TimeoutError("synthetic GET-only reconciliation outage")


def test_first_canary_reconciliation_outage_persists_halt_and_never_retries_post(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    execute_at = NOW + timedelta(seconds=4, milliseconds=300)
    monkeypatch.setattr(
        cold_pre_io,
        "_utc_now",
        lambda: execute_at + timedelta(milliseconds=35),
    )
    delegate = _AmbiguousDelegate()
    reconciler = _UnavailableReconciler()

    outcome = execute_first_canary_once(
        inputs=inputs,
        final_evidence=_final(inputs, at=execute_at),
        delegate=delegate,
        reconciler=reconciler,
        now=execute_at,
    )

    assert delegate.calls == 1
    assert reconciler.calls == 1
    assert outcome.status == "UNKNOWN_HALTED_NO_RETRY"
    assert outcome.retry_forbidden is True
    evidence = session.attempt.read(path=session.attempt.reconciliation_path)
    assert evidence["status"] == "CRYPTO_PAPER_FIRST_CANARY_RECONCILIATION_UNAVAILABLE_HALT_NO_RETRY"
    assert evidence["error_type"] == "TimeoutError"
    assert evidence["retry_post"] is False
    assert evidence["reconciliation_retry_get_only"] is True
    assert evidence["live_trading"] == "BLOCKED"


def test_first_canary_inputs_reject_attempt_runtime_from_other_database(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    wrong_runtime = SQLiteRuntime(tmp_path / "different-attempt.sqlite3")

    with pytest.raises(FirstCanaryExecutionBlocked, match="exact attempt database"):
        FirstCanaryExecutionInputs(
            attempt=inputs.attempt,
            core_runtime=inputs.core_runtime,
            attempt_runtime=wrong_runtime,
            credentials=inputs.credentials,
            package=inputs.package,
            broker_order=inputs.broker_order,
            prepared_account=inputs.prepared_account,
            prepared_asset=inputs.prepared_asset,
            prepared_product_profile=inputs.prepared_product_profile,
            prepared_market=inputs.prepared_market,
            risk_decision=inputs.risk_decision,
            preparation_authority_state_fingerprint=inputs.preparation_authority_state_fingerprint,
        )


def test_first_canary_final_evidence_rejects_non_active_account(tmp_path, monkeypatch) -> None:
    _, _, inputs = _prepare_session(tmp_path, monkeypatch)
    at = NOW + timedelta(seconds=4, milliseconds=300)
    final = _final(inputs, at=at)

    with pytest.raises(FirstCanaryExecutionBlocked, match="ACTIVE USD"):
        FirstCanaryFinalEvidence(
            account=replace(final.account, status="SUSPENDED"),
            asset=final.asset,
            product_profile=final.product_profile,
            market=final.market,
            flat_account=final.flat_account,
        )


def test_first_canary_gate_requires_delegate_and_reconciler_before_any_latch(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    execute_at = NOW + timedelta(seconds=4, milliseconds=300)

    with pytest.raises(TypeError, match="delegate and reconciler are required"):
        execute_first_canary_once(
            inputs=inputs,
            final_evidence=_final(inputs, at=execute_at),
            delegate=None,  # type: ignore[arg-type]
            reconciler=None,  # type: ignore[arg-type]
            now=execute_at,
        )
    assert session.attempt.execution_started_path.exists() is False
