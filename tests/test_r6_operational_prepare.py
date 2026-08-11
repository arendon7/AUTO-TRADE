from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from autotrade.brokers.alpaca_paper_canary import (
    PaperCanaryGate,
    PaperCanaryPolicy,
    PaperCanaryRejected,
)
from autotrade.brokers.alpaca_paper_canary_coordinator import PaperCanaryCoordinator
from autotrade.brokers.alpaca_paper_canary_permit import SQLitePaperCanaryPermitRegistry
from autotrade.brokers.alpaca_paper_core_provenance import PaperCoreProvenanceMissing
from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalWorkspace,
    read_expected_bracket,
    read_prepared_package,
)
from autotrade.brokers.alpaca_paper_operational_prepare import PaperOperationalCanaryPreparer
from autotrade.brokers.alpaca_paper_preparation_snapshot import read_preparation_snapshot
from autotrade.brokers.alpaca_paper_submission import SQLitePaperSubmissionRegistry
from autotrade.domain import PortfolioSnapshot
from autotrade.health_bridge import SQLiteHealthBridgeStore
from autotrade.oms import OrderManagementSystem
from autotrade.persistence import (
    SQLiteEventLedger,
    SQLiteOrderStore,
    SQLitePortfolioStore,
    SQLiteRuntime,
    SQLiteSafetyStateStore,
)
from autotrade.research.health import (
    HealthAssessment,
    HealthEntityKind,
    HealthPolicy,
    HealthState,
    SQLiteHealthStateStore,
)
from test_r6_paper_canary_coordinator import (
    NOW,
    TRACKS,
    NeverCalledBroker,
    attestation,
    decision,
    intent,
    market,
    stack,
    venue_rules,
)


PORTFOLIO_HEALTH_ID = "portfolio-r6-canary"


def h(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def health_policy() -> HealthPolicy:
    return HealthPolicy(
        min_observations=2,
        degraded_mean_loss_fraction=Decimal("0.20"),
        quarantined_mean_loss_fraction=Decimal("0.50"),
        degraded_volatility_ratio=Decimal("1.50"),
        quarantined_volatility_ratio=Decimal("2.00"),
        retire_after_distinct_quarantines=2,
    )


def seed_healthy_state(
    store: SQLiteHealthStateStore,
    *,
    entity_id: str,
    entity_kind: HealthEntityKind,
    policy: HealthPolicy,
    label: str,
) -> None:
    assessment = HealthAssessment(
        entity_id=entity_id,
        entity_kind=entity_kind,
        baseline_fingerprint=h(f"{label}-baseline"),
        observation_series_fingerprint=h(f"{label}-observations"),
        policy_fingerprint=policy.fingerprint,
        sample_count=2,
        current_mean_return=Decimal("0.01"),
        current_volatility=Decimal("0.01"),
        mean_loss_fraction=Decimal("0"),
        volatility_ratio=Decimal("1"),
        proposed_state=HealthState.HEALTHY,
        evaluated_at=NOW - timedelta(seconds=1),
    )
    state = store.apply_assessment(
        assessment,
        policy,
        now=NOW - timedelta(milliseconds=750),
    )
    assert state.state is HealthState.HEALTHY


def portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id="r6-operational-portfolio-001",
        equity=Decimal("100000"),
        gross_exposure=Decimal("0"),
        net_exposure=Decimal("0"),
        daily_pnl=Decimal("0"),
        drawdown=Decimal("0"),
        open_orders=0,
        signed_position_notional_by_symbol={},
        strategy_gross_exposure={},
        strategy_signed_position_notional_by_symbol={},
        reconciliation_ok=True,
        broker_state_known=True,
    )


def operational_decision():
    # Initial Strategy + Portfolio Health Bridge syncs each increment Safety.
    return replace(decision(), safety_state_version=2)


def build(tmp_path):
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    runtime = SQLiteRuntime(workspace.core_db_path)
    health_store = SQLiteHealthStateStore(workspace.core_db_path)
    policy = health_policy()
    seed_healthy_state(
        health_store,
        entity_id=intent().strategy_id,
        entity_kind=HealthEntityKind.STRATEGY,
        policy=policy,
        label="strategy",
    )
    seed_healthy_state(
        health_store,
        entity_id=PORTFOLIO_HEALTH_ID,
        entity_kind=HealthEntityKind.PORTFOLIO,
        policy=policy,
        label="portfolio",
    )
    bridge = SQLiteHealthBridgeStore(runtime, health_reader=health_store)
    bridge.sync_from_health(
        entity_id=intent().strategy_id,
        entity_kind=HealthEntityKind.STRATEGY,
        now=NOW - timedelta(milliseconds=500),
    )
    bridge.sync_from_health(
        entity_id=PORTFOLIO_HEALTH_ID,
        entity_kind=HealthEntityKind.PORTFOLIO,
        now=NOW - timedelta(milliseconds=250),
    )
    SQLitePortfolioStore(runtime).initialize(portfolio(), now=NOW - timedelta(seconds=1))

    broker = NeverCalledBroker()
    oms = OrderManagementSystem(
        broker=broker,
        ledger=SQLiteEventLedger(runtime),
        order_store=SQLiteOrderStore(runtime),
        safety_state_store=SQLiteSafetyStateStore(runtime),
        health_bridge=bridge,
        portfolio_health_entity_id=PORTFOLIO_HEALTH_ID,
    )
    coordinator = PaperCanaryCoordinator(
        oms=oms,
        canary_gate=PaperCanaryGate(
            PaperCanaryPolicy(
                enabled=True,
                max_notional=Decimal("10"),
                max_account_fraction=Decimal("0.001"),
                max_attestation_age_seconds=30,
                approval_ttl_seconds=5,
            )
        ),
    )
    submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(workspace.submission_db_path))
    permit = SQLitePaperCanaryPermitRegistry(SQLiteRuntime(workspace.permit_db_path))
    preparer = PaperOperationalCanaryPreparer(
        workspace=workspace,
        coordinator=coordinator,
    )
    return preparer, workspace, broker, submission, permit


def run_prepare(preparer, submission, permit, **overrides):
    values = {
        "intent": intent(),
        "decision": operational_decision(),
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
    assert snapshot_decision == operational_decision()
    assert snapshot_market == market()
    assert snapshot_approval == prepared.result.approval
    assert prepared.account_attestation_path == workspace.account_attestation_path
    assert prepared.expected_bracket_path == workspace.expected_bracket_path
    assert prepared.preparation_snapshot_path == workspace.root / "preparation_snapshot.json"
    assert prepared.core_provenance_path == workspace.root / "core_provenance.json"
    assert prepared.operator_context_path == workspace.operator_context_path
    assert prepared.manifest_path == workspace.manifest_path

    document = json.loads(prepared.core_provenance_path.read_text(encoding="utf-8"))
    document_hash = document.pop("document_hash")
    canonical = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=True,
    ).encode("utf-8")
    assert document_hash == sha256(canonical).hexdigest()
    assert document["package_hash"] == prepared.result.package.package_hash
    assert document["attempt_id"] == prepared.result.package.attempt_id
    assert document["core_provenance"]["safety_version"] == 2
    assert document["network_write_authorized"] is False
    assert document["external_order_submitted"] is False
    assert document["live_trading"] == "BLOCKED"
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
    assert second.core_provenance_path.read_bytes() == first.core_provenance_path.read_bytes()
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
    assert not (workspace.root / "core_provenance.json").exists()
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
    assert not (workspace.root / "core_provenance.json").exists()
    assert not workspace.operator_context_path.exists()
    assert broker.calls == 0


def test_preparer_rejects_coordinator_not_backed_by_workspace_core_before_operator_artifacts(tmp_path) -> None:
    coordinator, broker, _, submission, permit = stack(tmp_path / "foreign-coordinator")
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    preparer = PaperOperationalCanaryPreparer(workspace=workspace, coordinator=coordinator)

    with pytest.raises(PaperCoreProvenanceMissing, match="core database does not exist"):
        preparer.prepare(
            intent=intent(),
            decision=decision(),
            market=market(),
            account_attestation=attestation(),
            venue_rules=venue_rules(),
            take_profit_price=Decimal("11"),
            stop_loss_price=Decimal("9"),
            submission_registry=submission,
            permit_registry=permit,
            now=NOW,
            certified_tracks=TRACKS,
            reconciliation_clean=True,
            unresolved_unknown_orders=0,
            kill_switch_engaged=False,
            health_allows_new_exposure=True,
            prior_canary_submissions=0,
        )

    assert workspace.prepared_package_path.exists()
    assert workspace.expected_bracket_path.exists()
    assert (workspace.root / "preparation_snapshot.json").exists()
    assert not (workspace.root / "core_provenance.json").exists()
    assert not workspace.operator_context_path.exists()
    assert not workspace.manifest_path.exists()
    assert broker.calls == 0


def test_operational_preparer_requires_authoritative_types(tmp_path) -> None:
    coordinator, _, _, _, _ = stack(tmp_path / "coordinator")
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    with pytest.raises(TypeError, match="workspace"):
        PaperOperationalCanaryPreparer(workspace=object(), coordinator=coordinator)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Coordinator"):
        PaperOperationalCanaryPreparer(workspace=workspace, coordinator=object())  # type: ignore[arg-type]
