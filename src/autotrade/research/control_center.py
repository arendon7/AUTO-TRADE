from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .external_data import ExternalDatasetManifest
from .multiple_testing import HolmEvidence
from .trials import CampaignAccounting, SQLiteTrialLedger, TrialStatus


@dataclass(frozen=True, slots=True)
class DatasetEvidenceView:
    provider_id: str
    symbol: str
    interval: str
    start: str
    end: str
    bars: int
    source_payload_sha256: str
    dataset_hash: str
    manifest_fingerprint: str


@dataclass(frozen=True, slots=True)
class TrialEvidenceView:
    trial_id: str
    phase: str
    split_name: str
    strategy_id: str
    status: str
    result_hash: str
    failure_code: str


@dataclass(frozen=True, slots=True)
class CampaignResearchView:
    campaign_id: str
    complete: bool
    expected_trials: int
    preregistered_trials: int
    completed_trials: int
    failed_trials: int
    missing_trial_ids: tuple[str, ...]
    unterminated_trial_ids: tuple[str, ...]
    trials: tuple[TrialEvidenceView, ...]
    datasets: tuple[DatasetEvidenceView, ...]
    holm_family_size: int | None = None
    holm_min_adjusted_p: float | None = None


class ResearchControlCenter:
    """Read-only projection over immutable research evidence.

    The object deliberately exposes no mutation, OrderIntent, OMS, broker or
    execution dependency. It can summarize evidence but cannot authorize or
    submit capital-bearing activity.
    """

    def __init__(self, trial_ledger: SQLiteTrialLedger) -> None:
        self._trial_ledger = trial_ledger

    def campaign_view(
        self,
        campaign_id: str,
        *,
        manifests: Iterable[ExternalDatasetManifest] = (),
        holm: HolmEvidence | None = None,
    ) -> CampaignResearchView:
        accounting = self._trial_ledger.campaign_accounting(campaign_id)
        trials = tuple(
            TrialEvidenceView(
                trial_id=record.spec.trial_id,
                phase=record.spec.phase.value,
                split_name=record.spec.split_name,
                strategy_id=record.spec.strategy_id,
                status=record.status.value,
                result_hash=record.result_hash,
                failure_code=record.failure_code,
            )
            for record in self._trial_ledger.list_trials(campaign_id)
        )
        datasets = tuple(
            sorted(
                (
                    DatasetEvidenceView(
                        provider_id=manifest.provider_id,
                        symbol=manifest.symbol,
                        interval=manifest.interval,
                        start=manifest.start,
                        end=manifest.end,
                        bars=manifest.received_bars,
                        source_payload_sha256=manifest.source_payload_sha256,
                        dataset_hash=manifest.dataset_hash,
                        manifest_fingerprint=manifest.fingerprint,
                    )
                    for manifest in manifests
                ),
                key=lambda value: (
                    value.provider_id,
                    value.symbol,
                    value.interval,
                    value.start,
                    value.dataset_hash,
                ),
            )
        )
        min_adjusted = None
        family_size = None
        if holm is not None:
            if holm.campaign_id != campaign_id:
                raise ValueError("Holm evidence belongs to another campaign")
            family_size = holm.family_size
            if holm.adjusted_p_values:
                min_adjusted = min(holm.adjusted_p_values.values())
        return CampaignResearchView(
            campaign_id=campaign_id,
            complete=accounting.complete,
            expected_trials=len(accounting.expected_trial_ids),
            preregistered_trials=len(accounting.preregistered_trial_ids),
            completed_trials=len(accounting.completed_trial_ids),
            failed_trials=len(accounting.failed_trial_ids),
            missing_trial_ids=accounting.missing_preregistration_ids,
            unterminated_trial_ids=accounting.unterminated_trial_ids,
            trials=trials,
            datasets=datasets,
            holm_family_size=family_size,
            holm_min_adjusted_p=min_adjusted,
        )

    def accounting(self, campaign_id: str) -> CampaignAccounting:
        return self._trial_ledger.campaign_accounting(campaign_id)

    def terminal_trial_ids(self, campaign_id: str) -> tuple[str, ...]:
        return tuple(
            record.spec.trial_id
            for record in self._trial_ledger.list_trials(campaign_id)
            if record.status.terminal
        )
