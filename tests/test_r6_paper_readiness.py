from __future__ import annotations

from datetime import timedelta

import pytest

from autotrade.brokers.alpaca_paper_operator_decision import (
    PaperOperatorDecisionContext,
    SQLitePaperOperatorDecisionRegistry,
)
from autotrade.brokers.alpaca_paper_readiness import (
    PaperOperationalReadinessInspector,
    PaperReadinessIntegrityError,
    PaperReadinessPhase,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_operational_prepare import NOW, build, run_prepare
from test_r6_paper_canary_coordinator import attestation


def test_readiness_reports_account_preflight_required_without_network(tmp_path) -> None:
    _, workspace, broker, _, _ = build(tmp_path)
    report = PaperOperationalReadinessInspector(workspace.root).inspect(now=NOW)

    assert report.phase is PaperReadinessPhase.ACCOUNT_PREFLIGHT_REQUIRED
    assert report.next_action == "RUN_SEPARATE_GET_ONLY_PAPER_ACCOUNT_PREFLIGHT"
    assert report.account_attested is False
    assert report.network_used is False
    assert report.broker_write_performed is False
    assert report.execution_authorized is False
    assert report.capital_authority == "NONE"
    assert report.profitability_claim is False
    assert report.production_status == "BLOCKED"
    assert broker.calls == 0


def test_readiness_reports_offline_preparation_required_after_account_artifact(tmp_path) -> None:
    _, workspace, broker, _, _ = build(tmp_path)
    workspace.write_account_attestation(attestation())

    report = PaperOperationalReadinessInspector(workspace.root).inspect(now=NOW)

    assert report.phase is PaperReadinessPhase.PREPARATION_REQUIRED
    assert report.next_action == "RUN_SEPARATE_OFFLINE_CANARY_PREPARATION"
    assert report.account_attested is True
    assert report.execution_authorized is False
    assert broker.calls == 0


def test_readiness_reports_human_decision_required_after_valid_preparation(tmp_path) -> None:
    preparer, workspace, broker, submission, permit = build(tmp_path)
    prepared = run_prepare(preparer, submission, permit)

    report = PaperOperationalReadinessInspector(workspace.root).inspect(
        now=NOW + timedelta(seconds=1)
    )

    assert report.phase is PaperReadinessPhase.HUMAN_DECISION_REQUIRED
    assert report.next_action == "RUN_SEPARATE_INTERACTIVE_HUMAN_OPERATOR_DECISION"
    assert report.order_id == prepared.result.package.order_id
    assert report.client_order_id == prepared.result.package.client_order_id
    assert report.package_hash == prepared.result.package.package_hash
    assert report.submission_status == "PREPARED"
    assert report.submission_attempt_count == 0
    assert report.oms_status == "VALIDATED"
    assert report.operator_status is None
    assert report.permit_status == "ISSUED"
    assert report.core_provenance_verified is True
    assert report.execution_authorized is False
    assert broker.calls == 0


def test_readiness_never_authorizes_even_with_fresh_human_decision(tmp_path) -> None:
    preparer, workspace, broker, submission, permit = build(tmp_path)
    prepared = run_prepare(preparer, submission, permit)
    context = PaperOperatorDecisionContext.from_prepared_package(prepared.result.package)
    registry = SQLitePaperOperatorDecisionRegistry(SQLiteRuntime(workspace.operator_db_path))
    registry.record_operator_approval(
        context=context,
        operator_id="operator:readiness-test",
        issued_at=NOW + timedelta(milliseconds=100),
        expires_at=NOW + timedelta(seconds=4),
    )

    report = PaperOperationalReadinessInspector(workspace.root).inspect(
        now=NOW + timedelta(seconds=1)
    )

    assert report.phase is PaperReadinessPhase.EXPLICIT_EXECUTION_DECISION_REQUIRED
    assert (
        report.next_action
        == "SEPARATE_EXPLICIT_OPERATOR_DECISION_REQUIRED_BEFORE_REAL_PAPER_EXECUTION"
    )
    assert report.operator_status == "ISSUED"
    assert report.operator_decision_valid is True
    assert report.permit_status == "ISSUED"
    assert report.core_provenance_verified is True
    assert report.execution_authorized is False
    assert report.network_used is False
    assert report.broker_write_performed is False
    assert broker.calls == 0


def test_readiness_blocks_expired_authority_instead_of_recommending_execution(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    prepared = run_prepare(preparer, submission, permit)
    context = PaperOperatorDecisionContext.from_prepared_package(prepared.result.package)
    registry = SQLitePaperOperatorDecisionRegistry(SQLiteRuntime(workspace.operator_db_path))
    registry.record_operator_approval(
        context=context,
        operator_id="operator:readiness-expiry",
        issued_at=NOW + timedelta(milliseconds=100),
        expires_at=NOW + timedelta(seconds=2),
    )

    report = PaperOperationalReadinessInspector(workspace.root).inspect(
        now=NOW + timedelta(seconds=3)
    )

    assert report.phase is PaperReadinessPhase.BLOCKED_INCONSISTENT_STATE
    assert report.operator_status == "ISSUED"
    assert report.operator_decision_valid is False
    assert report.execution_authorized is False


def test_readiness_detects_account_artifact_live_or_secret_policy_tamper(tmp_path) -> None:
    _, workspace, _, _, _ = build(tmp_path)
    workspace.write_account_attestation(attestation())
    raw = workspace.account_attestation_path.read_text(encoding="utf-8")
    workspace.account_attestation_path.write_text(
        raw.replace('"credentials_persisted": false', '"credentials_persisted": true'),
        encoding="utf-8",
    )

    with pytest.raises(PaperReadinessIntegrityError, match="persist credentials"):
        PaperOperationalReadinessInspector(workspace.root).inspect(now=NOW)


def test_readiness_missing_workspace_is_fail_closed(tmp_path) -> None:
    with pytest.raises(PaperReadinessIntegrityError, match="does not exist"):
        PaperOperationalReadinessInspector(tmp_path / "missing").inspect(now=NOW)


def test_readiness_report_serialization_keeps_non_authorizing_truth(tmp_path) -> None:
    _, workspace, _, _, _ = build(tmp_path)
    payload = PaperOperationalReadinessInspector(workspace.root).inspect(now=NOW).to_dict()
    assert payload["network_used"] is False
    assert payload["broker_write_performed"] is False
    assert payload["execution_authorized"] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["profitability_claim"] is False
    assert payload["live_trading"] == "BLOCKED"
