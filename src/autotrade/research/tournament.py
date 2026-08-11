from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json

from .trials import SQLiteTrialLedger, TrialPhase, TrialRecord, TrialStatus


class TournamentGovernanceError(RuntimeError):
    pass


class RankingDirection(StrEnum):
    MAXIMIZE = "MAXIMIZE"
    MINIMIZE = "MINIMIZE"


@dataclass(frozen=True, slots=True)
class TournamentSpec:
    tournament_id: str
    campaign_id: str
    metric_name: str
    direction: RankingDirection
    candidate_trial_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, value in (
            ("tournament_id", self.tournament_id),
            ("campaign_id", self.campaign_id),
            ("metric_name", self.metric_name),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        if not self.candidate_trial_ids:
            raise ValueError("candidate_trial_ids cannot be empty")
        if any(not trial_id.strip() for trial_id in self.candidate_trial_ids):
            raise ValueError("candidate_trial_ids cannot contain blanks")
        if len(set(self.candidate_trial_ids)) != len(self.candidate_trial_ids):
            raise ValueError("candidate_trial_ids must be unique")
        if self.candidate_trial_ids != tuple(sorted(self.candidate_trial_ids)):
            raise ValueError("candidate_trial_ids must be canonical sorted order")

    @property
    def fingerprint(self) -> str:
        return _hash_payload(
            {
                "tournament_id": self.tournament_id,
                "campaign_id": self.campaign_id,
                "metric_name": self.metric_name,
                "direction": self.direction.value,
                "candidate_trial_ids": list(self.candidate_trial_ids),
            }
        )


@dataclass(frozen=True, slots=True)
class TournamentEntry:
    rank: int
    trial_id: str
    strategy_id: str
    strategy_version: str
    status: TrialStatus
    eligible: bool
    metric_value: Decimal | None
    failure_code: str
    result_hash: str


@dataclass(frozen=True, slots=True)
class TournamentEvidence:
    tournament_id: str
    campaign_id: str
    metric_name: str
    direction: RankingDirection
    spec_fingerprint: str
    result_universe_hash: str
    entries: tuple[TournamentEntry, ...]
    winner_trial_id: str

    @property
    def completed_count(self) -> int:
        return sum(1 for entry in self.entries if entry.status is TrialStatus.COMPLETED)

    @property
    def failed_count(self) -> int:
        return sum(1 for entry in self.entries if entry.status is TrialStatus.FAILED)

    @property
    def fingerprint(self) -> str:
        return _hash_payload(self.to_payload(include_fingerprint=False))

    def to_payload(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "tournament_id": self.tournament_id,
            "campaign_id": self.campaign_id,
            "metric_name": self.metric_name,
            "direction": self.direction.value,
            "spec_fingerprint": self.spec_fingerprint,
            "result_universe_hash": self.result_universe_hash,
            "winner_trial_id": self.winner_trial_id,
            "entries": [
                {
                    "rank": entry.rank,
                    "trial_id": entry.trial_id,
                    "strategy_id": entry.strategy_id,
                    "strategy_version": entry.strategy_version,
                    "status": entry.status.value,
                    "eligible": entry.eligible,
                    "metric_value": (
                        str(entry.metric_value) if entry.metric_value is not None else None
                    ),
                    "failure_code": entry.failure_code,
                    "result_hash": entry.result_hash,
                }
                for entry in self.entries
            ],
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload


def evaluate_strategy_tournament(
    ledger: SQLiteTrialLedger,
    spec: TournamentSpec,
) -> TournamentEvidence:
    """Rank the complete frozen DEVELOPMENT universe deterministically.

    The function is intentionally read-only. It cannot authorize HOLDOUT use,
    promotion, OMS submission, broker I/O or capital-bearing activity.
    """

    ledger.require_complete_campaign(spec.campaign_id)
    records = ledger.list_trials(spec.campaign_id)
    if not records:
        raise TournamentGovernanceError("campaign contains no trials")

    if any(record.spec.phase is TrialPhase.FINAL_HOLDOUT for record in records):
        raise TournamentGovernanceError(
            "Strategy Tournament cannot observe a campaign containing FINAL_HOLDOUT trials"
        )

    development = tuple(
        sorted(
            (
                record
                for record in records
                if record.spec.phase is TrialPhase.DEVELOPMENT
            ),
            key=lambda record: record.spec.trial_id,
        )
    )
    development_ids = tuple(record.spec.trial_id for record in development)
    if not development_ids:
        raise TournamentGovernanceError("campaign contains no DEVELOPMENT candidates")
    if spec.candidate_trial_ids != development_ids:
        raise TournamentGovernanceError(
            "candidate universe must equal the complete frozen DEVELOPMENT trial universe"
        )

    scored: list[tuple[TrialRecord, Decimal]] = []
    failed: list[TrialRecord] = []
    for record in development:
        if not record.status.terminal:
            # require_complete_campaign should already enforce this; keep the
            # invariant local so future ledger changes cannot weaken Tournament.
            raise TournamentGovernanceError(
                f"candidate is not terminal: {record.spec.trial_id}"
            )
        if record.status is TrialStatus.FAILED:
            failed.append(record)
            continue
        value = _metric_decimal(record, spec.metric_name)
        scored.append((record, value))

    def completed_key(item: tuple[TrialRecord, Decimal]):
        record, metric = item
        primary = -metric if spec.direction is RankingDirection.MAXIMIZE else metric
        # Exact metric ties are deliberately broken by immutable strategy/trial
        # identity, never by HOLDOUT, p-value inspection or iteration order.
        return (
            primary,
            record.spec.strategy_id,
            record.spec.strategy_version,
            record.spec.trial_id,
        )

    scored.sort(key=completed_key)
    failed.sort(
        key=lambda record: (
            record.failure_code,
            record.spec.strategy_id,
            record.spec.strategy_version,
            record.spec.trial_id,
        )
    )

    entries: list[TournamentEntry] = []
    rank = 1
    for record, metric in scored:
        entries.append(
            TournamentEntry(
                rank=rank,
                trial_id=record.spec.trial_id,
                strategy_id=record.spec.strategy_id,
                strategy_version=record.spec.strategy_version,
                status=record.status,
                eligible=True,
                metric_value=metric,
                failure_code="",
                result_hash=record.result_hash,
            )
        )
        rank += 1
    for record in failed:
        entries.append(
            TournamentEntry(
                rank=rank,
                trial_id=record.spec.trial_id,
                strategy_id=record.spec.strategy_id,
                strategy_version=record.spec.strategy_version,
                status=record.status,
                eligible=False,
                metric_value=None,
                failure_code=record.failure_code,
                result_hash=record.result_hash,
            )
        )
        rank += 1

    universe_hash = _hash_payload(
        {
            "campaign_id": spec.campaign_id,
            "candidate_results": [
                {
                    "trial_id": record.spec.trial_id,
                    "status": record.status.value,
                    "result_hash": record.result_hash,
                }
                for record in development
            ],
        }
    )
    winner = entries[0].trial_id if entries and entries[0].eligible else ""
    return TournamentEvidence(
        tournament_id=spec.tournament_id,
        campaign_id=spec.campaign_id,
        metric_name=spec.metric_name,
        direction=spec.direction,
        spec_fingerprint=spec.fingerprint,
        result_universe_hash=universe_hash,
        entries=tuple(entries),
        winner_trial_id=winner,
    )


def _metric_decimal(record: TrialRecord, metric_name: str) -> Decimal:
    if metric_name not in record.metrics:
        raise TournamentGovernanceError(
            f"completed candidate missing ranking metric {metric_name}: {record.spec.trial_id}"
        )
    raw = record.metrics[metric_name]
    if isinstance(raw, bool):
        raise TournamentGovernanceError(
            f"ranking metric cannot be boolean: {record.spec.trial_id}"
        )
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise TournamentGovernanceError(
            f"ranking metric is not numeric: {record.spec.trial_id}"
        ) from exc
    if not value.is_finite():
        raise TournamentGovernanceError(
            f"ranking metric must be finite: {record.spec.trial_id}"
        )
    return value


def _hash_payload(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()
