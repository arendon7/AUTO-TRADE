from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.control_center import ResearchControlCenter
from autotrade.research.external_data import ExternalDatasetManifest
from autotrade.research.multiple_testing import campaign_holm_evidence
from autotrade.research.trials import (
    CampaignSpec,
    SQLiteTrialLedger,
    TrialPhase,
    TrialSpec,
)


def make_ledger(tmp_path, now):
    ledger = SQLiteTrialLedger(tmp_path / "control.db")
    ledger.create_campaign(
        CampaignSpec(
            campaign_id="campaign",
            family_id="family",
            expected_trial_ids=("a", "b"),
            code_version="code",
            purpose="read-only review",
        ),
        now=now,
    )
    for idx, trial_id in enumerate(("a", "b")):
        ledger.preregister(
            TrialSpec(
                trial_id=trial_id,
                campaign_id="campaign",
                hypothesis_id=f"h-{trial_id}",
                strategy_id=f"strategy-{trial_id}",
                strategy_version="1",
                dataset_hash="d" * 64,
                split_name="development",
                phase=TrialPhase.DEVELOPMENT,
                parameters={"x": idx},
                code_version="code",
            ),
            now=now + timedelta(seconds=idx + 1),
        )
    ledger.record_completed(
        trial_id="a",
        metrics={"sharpe": 1.0},
        p_value=Decimal("0.02"),
        now=now + timedelta(seconds=10),
    )
    ledger.record_failed(
        trial_id="b",
        failure_code="NO_EDGE",
        now=now + timedelta(seconds=11),
    )
    return ledger


def manifest():
    return ExternalDatasetManifest(
        provider_id="BINANCE_SPOT_PUBLIC_KLINES",
        provider_version="r3-v1",
        endpoint="https://data-api.binance.vision/api/v3/klines",
        symbol="BTCUSDT",
        interval="1m",
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:10:00+00:00",
        expected_bars=10,
        received_bars=10,
        pages=1,
        source_payload_sha256="a" * 64,
        dataset_hash="b" * 64,
        provenance="sample",
    )


def test_control_center_projects_immutable_campaign_evidence(tmp_path, now):
    ledger = make_ledger(tmp_path, now)
    center = ResearchControlCenter(ledger)
    holm = campaign_holm_evidence(ledger, "campaign")
    view = center.campaign_view(
        "campaign", manifests=(manifest(),), holm=holm
    )
    assert view.complete is True
    assert view.expected_trials == 2
    assert view.completed_trials == 1
    assert view.failed_trials == 1
    assert view.missing_trial_ids == ()
    assert view.unterminated_trial_ids == ()
    assert tuple(item.trial_id for item in view.trials) == ("a", "b")
    assert view.datasets[0].dataset_hash == "b" * 64
    assert view.holm_family_size == 2
    assert view.holm_min_adjusted_p == pytest.approx(0.04)
    assert center.terminal_trial_ids("campaign") == ("a", "b")


def test_control_center_rejects_cross_campaign_statistics(tmp_path, now):
    ledger = make_ledger(tmp_path, now)
    center = ResearchControlCenter(ledger)
    holm = campaign_holm_evidence(ledger, "campaign")
    from dataclasses import replace

    with pytest.raises(ValueError, match="another campaign"):
        center.campaign_view(
            "campaign", holm=replace(holm, campaign_id="other")
        )


def test_control_center_has_no_mutation_or_execution_surface(tmp_path, now):
    center = ResearchControlCenter(make_ledger(tmp_path, now))
    forbidden = {
        "submit",
        "cancel",
        "replace_order",
        "process_intent",
        "record_completed",
        "record_failed",
        "preregister",
    }
    assert forbidden.isdisjoint(set(dir(center)))
