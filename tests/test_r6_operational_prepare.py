from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.brokers.alpaca_paper_canary import PaperCanaryRejected
from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalWorkspace,
    read_expected_bracket,
    read_prepared_package,
)
from autotrade.brokers.alpaca_paper_operational_prepare import PaperOperationalCanaryPreparer
from autotrade.brokers.alpaca_paper_preparation_snapshot import read_preparation_snapshot
from test_r6_paper_canary_coordinator import (
    NOW,
    TRACKS,
    attestation,
    decision,
    intent,
    market,
    stack,
    venue_rules,
)


def build(tmp_path):
    coordinator, broker, _, submission, permit = stack(tmp_path / "coordinator")
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    preparer = PaperOperationalCanaryPreparer(
        workspace=workspace,
        coordinator=coordinator,
    )
    return preparer, workspace, broker, submission, permit


def run_prepare(preparer, submission, permit, **overrides):
    values = {
        "intent": intent(),
        "decision": decision(),
        "market": market(),
        "account_attestation": attestation(),
        "venue_rules": venue_rules(),
        "take_profit_price": Decimal("11"),
        "stop_loss_price": Decimal("9"),
        "submission_registry": submission,
        "permit_registry": permit,
        "now": NOW,
        "certified_tracks": TRACKS,
        "reconciliation_clean": True,
        "unresolved_unknown_orders": 0,
        "kill_switch_engaged": False,
        "health_allows_new_exposure": True,
        "prior_canary_submissions": 0,
    }
    values.update(overrides)
    return preparer.prepare(**values)


def test_operational_preparer_persists_exact_restart_evidence_and_stops_before_execution(tmp_path) -> None:
    preparer, workspace, broker, submission, permit = build(tmp_path)
    prepared = run_prepare(preparer, submission, permit)

    assert prepared.result.package.network_write_authorized is False
    assert prepared.result.package.next_action == "OPERATOR_DECISION_REQUIRED"
    assert read_prepared_package(prepared.prepared_package_path) == prepared.result.package
    assert read_expected_bracket(prepared.expected_bracket_path) == prepared.result.bracket
    snapshot_decision, snapshot_market, snapshot_approval = read_preparation_snapshot(
        workspace,
        package=prepared.result.package,
    )
    assert snapshot_decision == decision()
    assert snapshot_market == market()
    assert snapshot_approval == prepared.result.approval
    assert prepared.account_attestation_path == workspace.account_attestation_path
    assert prepared.expected_bracket_path == workspace.expected_bracket_path
    assert prepared.preparation_snapshot_path == workspace.root / "preparation_snapshot.json"
    assert prepared.operator_context_path == workspace.operator_context_path
    assert prepared.manifest_path == workspace.manifest_path
    assert broker.calls == 0

    forbidden = {"submit", "submit_once", "stage_external_submission", "post", "write", "send"}
    assert not (forbidden & set(dir(preparer)))


def test_identical_operational_preparation_is_idempotent(tmp_path) -> None:
    preparer, _, broker, submission, permit = build(tmp_path)
    first = run_prepare(preparer, submission, permit)
    second = run_prepare(preparer, submission, permit)
    assert second.result.package == first.result.package
    assert second.result.bracket == first.result.bracket
    assert second.prepared_package_path.read_bytes() == first.prepared_package_path.read_bytes()
    assert second.expected_bracket_path.read_bytes() == first.expected_bracket_path.read_bytes()
    assert second.preparation_snapshot_path.read_bytes() == first.preparation_snapshot_path.read_bytes()
    assert broker.calls == 0


def test_operational_preparation_propagates_canary_fail_closed_without_partial_package(tmp_path) -> None:
    preparer, workspace, broker, submission, permit = build(tmp_path)
    with pytest.raises(PaperCanaryRejected, match="reconciliation"):
        run_prepare(
            preparer,
            submission,
            permit,
            reconciliation_clean=False,
        )
    assert workspace.account_attestation_path.exists()
    assert not workspace.prepared_package_path.exists()
    assert not workspace.expected_bracket_path.exists()
    assert not (workspace.root / "preparation_snapshot.json").exists()
    assert not workspace.operator_context_path.exists()
    assert not workspace.manifest_path.exists()
    assert broker.calls == 0


def test_operational_preparation_rejects_stale_account_before_package_persistence(tmp_path) -> None:
    preparer, workspace, broker, submission, permit = build(tmp_path)
    stale = attestation(at=NOW - timedelta(seconds=31))
    with pytest.raises(PaperCanaryRejected, match="stale"):
        run_prepare(
            preparer,
            submission,
            permit,
            account_attestation=stale,
        )
    assert workspace.account_attestation_path.exists()
    assert not workspace.prepared_package_path.exists()
    assert not workspace.expected_bracket_path.exists()
    assert not (workspace.root / "preparation_snapshot.json").exists()
    assert broker.calls == 0


def test_operational_preparer_requires_authoritative_types(tmp_path) -> None:
    coordinator, _, _, _, _ = stack(tmp_path / "coordinator")
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    with pytest.raises(TypeError, match="workspace"):
        PaperOperationalCanaryPreparer(workspace=object(), coordinator=coordinator)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Coordinator"):
        PaperOperationalCanaryPreparer(workspace=workspace, coordinator=object())  # type: ignore[arg-type]
