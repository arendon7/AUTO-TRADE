from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

from autotrade.strategy_lab_promotion import (
    PERMANENT_W79_PROMOTION_BLOCKERS,
    REQUIRED_W79_GATE_IDS,
    StrategyPromotionPolicy,
    StrategyPromotionThresholdPolicy,
)
from autotrade.strategy_promotion_assessment_read_model import (
    PromotionAssessmentReadError,
    PromotionAssessmentReadModel,
    PromotionAssessmentReadSnapshot,
)


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class StrategyLabReadModelError(RuntimeError):
    pass


class StrategyLabReadModelMissing(StrategyLabReadModelError):
    pass


class StrategyLabReadModelIntegrityError(StrategyLabReadModelError):
    pass


@dataclass(frozen=True, slots=True)
class ThresholdPolicyReadView:
    threshold_policy_id: str
    development_campaign_id: str
    holdout_campaign_id: str
    holdout_trial_id: str
    max_holm_adjusted_p: str
    min_holdout_net_return: str
    max_holdout_drawdown: str
    min_holdout_fills: int
    min_execution_fill_ratio: str
    max_execution_adverse_slippage_bps: str
    registered_at: str
    threshold_policy_hash: str
    candidate_binding_state: str

    def to_dict(self) -> dict[str, object]:
        return {
            "threshold_policy_id": self.threshold_policy_id,
            "development_campaign_id": self.development_campaign_id,
            "holdout_campaign_id": self.holdout_campaign_id,
            "holdout_trial_id": self.holdout_trial_id,
            "max_holm_adjusted_p": self.max_holm_adjusted_p,
            "min_holdout_net_return": self.min_holdout_net_return,
            "max_holdout_drawdown": self.max_holdout_drawdown,
            "min_holdout_fills": self.min_holdout_fills,
            "min_execution_fill_ratio": self.min_execution_fill_ratio,
            "max_execution_adverse_slippage_bps": self.max_execution_adverse_slippage_bps,
            "registered_at": self.registered_at,
            "threshold_policy_hash": self.threshold_policy_hash,
            "candidate_binding_state": self.candidate_binding_state,
        }


@dataclass(frozen=True, slots=True)
class CandidatePolicyReadView:
    policy_id: str
    threshold_policy_id: str
    development_campaign_id: str
    holdout_campaign_id: str
    holdout_trial_id: str
    selected_trial_id: str
    selected_strategy_id: str
    selected_strategy_version: str
    selected_trial_fingerprint: str
    tournament_fingerprint: str
    registered_at: str
    threshold_policy_hash: str
    policy_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "threshold_policy_id": self.threshold_policy_id,
            "development_campaign_id": self.development_campaign_id,
            "holdout_campaign_id": self.holdout_campaign_id,
            "holdout_trial_id": self.holdout_trial_id,
            "selected_trial_id": self.selected_trial_id,
            "selected_strategy_id": self.selected_strategy_id,
            "selected_strategy_version": self.selected_strategy_version,
            "selected_trial_fingerprint": self.selected_trial_fingerprint,
            "tournament_fingerprint": self.tournament_fingerprint,
            "registered_at": self.registered_at,
            "threshold_policy_hash": self.threshold_policy_hash,
            "policy_hash": self.policy_hash,
        }


@dataclass(frozen=True, slots=True)
class StrategyLabPromotionReadSnapshot:
    governance_state: str
    thresholds: tuple[ThresholdPolicyReadView, ...]
    candidates: tuple[CandidatePolicyReadView, ...]
    required_gate_ids: tuple[str, ...]
    gate_evidence_state: str
    promotion_blockers: tuple[str, ...]
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    capital_authority: str
    live_trading: str
    promotion_assessments: PromotionAssessmentReadSnapshot
    observed_at: datetime
    provenance_hash: str

    def __post_init__(self) -> None:
        if self.governance_state not in {
            "NO_GOVERNANCE_DATA",
            "THRESHOLDS_PREREGISTERED",
            "CANDIDATE_FROZEN",
        }:
            raise StrategyLabReadModelIntegrityError("unknown Strategy Lab governance state")
        if self.thresholds != tuple(sorted(self.thresholds, key=lambda item: item.threshold_policy_id)):
            raise StrategyLabReadModelIntegrityError("threshold read views must be sorted")
        if self.candidates != tuple(sorted(self.candidates, key=lambda item: item.policy_id)):
            raise StrategyLabReadModelIntegrityError("candidate read views must be sorted")
        if self.required_gate_ids != REQUIRED_W79_GATE_IDS:
            raise StrategyLabReadModelIntegrityError("Strategy Lab gate set is not canonical W79")
        if self.gate_evidence_state != "NOT_PERSISTED_BY_W79":
            raise StrategyLabReadModelIntegrityError("read model may not synthesize W79 gate evidence")
        if self.promotion_blockers != tuple(sorted(PERMANENT_W79_PROMOTION_BLOCKERS)):
            raise StrategyLabReadModelIntegrityError("promotion blocker set is not canonical W79")
        if self.paper_candidate_authorized is not False:
            raise StrategyLabReadModelIntegrityError("read model may not authorize PAPER candidate")
        if self.external_execution_authorized is not False:
            raise StrategyLabReadModelIntegrityError("read model may not authorize execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise StrategyLabReadModelIntegrityError("read model may not grant capital/LIVE authority")
        if not isinstance(self.promotion_assessments, PromotionAssessmentReadSnapshot):
            raise StrategyLabReadModelIntegrityError("W80 assessment projection is not canonical")
        if self.promotion_assessments.to_dict()["paper_candidate_authorized"] is not False:
            raise StrategyLabReadModelIntegrityError("W80 assessment projection may not authorize PAPER")
        _require_aware(self.observed_at, "observed_at")
        _require_hash(self.provenance_hash, "provenance_hash")
        if self.provenance_hash != _hash(self._provenance_payload()):
            raise StrategyLabReadModelIntegrityError("Strategy Lab provenance hash mismatch")

    def _provenance_payload(self) -> dict[str, object]:
        return {
            "governance_state": self.governance_state,
            "thresholds": [item.to_dict() for item in self.thresholds],
            "candidates": [item.to_dict() for item in self.candidates],
            "required_gate_ids": list(self.required_gate_ids),
            "gate_evidence_state": self.gate_evidence_state,
            "promotion_blockers": list(self.promotion_blockers),
            "paper_candidate_authorized": self.paper_candidate_authorized,
            "external_execution_authorized": self.external_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }

    def to_dict(self) -> dict[str, object]:
        value = self._provenance_payload()
        value.update(
            {
                "threshold_count": len(self.thresholds),
                "candidate_count": len(self.candidates),
                "promotion_assessments": self.promotion_assessments.to_dict(),
                "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
                "provenance_hash": self.provenance_hash,
                "broker_network_used": False,
                "broker_write_performed": False,
                "credentials_used": False,
            }
        )
        return value


class StrategyLabPromotionReadModel:
    """Immutable projection of W79 governance plus independent W80 evidence.

    W79 governance remains a distinct provenance domain and keeps
    gate_evidence_state=NOT_PERSISTED_BY_W79. W80 durable assessments are
    attached as a separately hash-bound read-only projection. Neither reader
    instantiates SQLiteRuntime/SQLiteTrialLedger or exposes broker, OMS, Safety,
    credential, OrderIntent or execution authority.
    """

    def __init__(self, core_db_path: str | Path) -> None:
        path = Path(core_db_path).expanduser()
        if path.is_symlink():
            raise StrategyLabReadModelIntegrityError("core.sqlite3 may not be a symlink")
        if not path.is_file():
            raise StrategyLabReadModelMissing("core.sqlite3 is missing")
        self._path = path.resolve()

    def snapshot(self, *, now: datetime | None = None) -> StrategyLabPromotionReadSnapshot:
        observed_at = now or datetime.now(timezone.utc)
        _require_aware(observed_at, "now")
        conn = self._connect_read_only()
        try:
            conn.execute("BEGIN")
            tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            threshold_table = "strategy_promotion_threshold_policies"
            candidate_table = "strategy_promotion_policies"
            present = {name for name in (threshold_table, candidate_table) if name in tables}
            if not present:
                threshold_rows: list[sqlite3.Row] = []
                candidate_rows: list[sqlite3.Row] = []
            elif present != {threshold_table, candidate_table}:
                raise StrategyLabReadModelIntegrityError(
                    "partial W79 promotion schema detected in core.sqlite3"
                )
            else:
                threshold_rows = conn.execute(
                    f"SELECT * FROM {threshold_table} ORDER BY threshold_policy_id"
                ).fetchall()
                candidate_rows = conn.execute(
                    f"SELECT * FROM {candidate_table} ORDER BY policy_id"
                ).fetchall()
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

        thresholds = tuple(_threshold_view(row) for row in threshold_rows)
        candidates = tuple(_candidate_view(row) for row in candidate_rows)
        candidate_by_threshold = {item.threshold_policy_id: item for item in candidates}
        if len(candidate_by_threshold) != len(candidates):
            raise StrategyLabReadModelIntegrityError("multiple candidates bind one threshold policy")
        threshold_by_id = {item.threshold_policy_id: item for item in thresholds}
        if len(threshold_by_id) != len(thresholds):
            raise StrategyLabReadModelIntegrityError("duplicate threshold policy read identity")
        for candidate in candidates:
            threshold = threshold_by_id.get(candidate.threshold_policy_id)
            if threshold is None:
                raise StrategyLabReadModelIntegrityError("candidate lost its threshold policy")
            if candidate.threshold_policy_hash != threshold.threshold_policy_hash:
                raise StrategyLabReadModelIntegrityError("candidate/threshold hash binding mismatch")
            if (
                candidate.development_campaign_id != threshold.development_campaign_id
                or candidate.holdout_campaign_id != threshold.holdout_campaign_id
                or candidate.holdout_trial_id != threshold.holdout_trial_id
            ):
                raise StrategyLabReadModelIntegrityError("candidate/threshold campaign binding mismatch")

        thresholds = tuple(
            ThresholdPolicyReadView(
                **{
                    **item.to_dict(),
                    "candidate_binding_state": (
                        "CANDIDATE_FROZEN"
                        if item.threshold_policy_id in candidate_by_threshold
                        else "AWAITING_CANDIDATE"
                    ),
                }
            )
            for item in thresholds
        )
        state = (
            "CANDIDATE_FROZEN"
            if candidates
            else "THRESHOLDS_PREREGISTERED"
            if thresholds
            else "NO_GOVERNANCE_DATA"
        )
        try:
            assessment_snapshot = PromotionAssessmentReadModel(self._path).snapshot(now=observed_at)
        except PromotionAssessmentReadError as exc:
            raise StrategyLabReadModelIntegrityError(
                "durable W80 assessment evidence failed independent verification"
            ) from exc
        values = {
            "governance_state": state,
            "thresholds": thresholds,
            "candidates": candidates,
            "required_gate_ids": REQUIRED_W79_GATE_IDS,
            "gate_evidence_state": "NOT_PERSISTED_BY_W79",
            "promotion_blockers": tuple(sorted(PERMANENT_W79_PROMOTION_BLOCKERS)),
            "paper_candidate_authorized": False,
            "external_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        return StrategyLabPromotionReadSnapshot(
            **values,
            promotion_assessments=assessment_snapshot,
            observed_at=observed_at,
            provenance_hash=_hash(_snapshot_payload(values)),
        )

    def _connect_read_only(self) -> sqlite3.Connection:
        encoded = quote(str(self._path), safe="/")
        conn = sqlite3.connect(
            f"file:{encoded}?mode=ro",
            uri=True,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def _threshold_view(row: sqlite3.Row) -> ThresholdPolicyReadView:
    value = _json_object(row["policy_json"], "threshold policy JSON")
    policy = StrategyPromotionThresholdPolicy(
        threshold_policy_id=_string(value, "threshold_policy_id"),
        development_campaign_id=_string(value, "development_campaign_id"),
        holdout_campaign_id=_string(value, "holdout_campaign_id"),
        holdout_trial_id=_string(value, "holdout_trial_id"),
        max_holm_adjusted_p=_decimal(value, "max_holm_adjusted_p"),
        min_holdout_net_return=_decimal(value, "min_holdout_net_return"),
        max_holdout_drawdown=_decimal(value, "max_holdout_drawdown"),
        min_holdout_fills=_integer(value, "min_holdout_fills"),
        min_execution_fill_ratio=_decimal(value, "min_execution_fill_ratio"),
        max_execution_adverse_slippage_bps=_decimal(
            value, "max_execution_adverse_slippage_bps"
        ),
        external_execution_authorized=_false(value, "external_execution_authorized"),
        live_trading=_string(value, "live_trading"),
        threshold_policy_hash=_string(value, "threshold_policy_hash"),
    )
    _require_row_match(
        row,
        {
            "threshold_policy_id": policy.threshold_policy_id,
            "threshold_policy_hash": policy.threshold_policy_hash,
            "development_campaign_id": policy.development_campaign_id,
            "holdout_campaign_id": policy.holdout_campaign_id,
            "policy_json": _canonical_json(policy.to_dict()),
        },
    )
    registered_at = _aware_iso(row["registered_at"], "threshold registered_at")
    return ThresholdPolicyReadView(
        threshold_policy_id=policy.threshold_policy_id,
        development_campaign_id=policy.development_campaign_id,
        holdout_campaign_id=policy.holdout_campaign_id,
        holdout_trial_id=policy.holdout_trial_id,
        max_holm_adjusted_p=_format_decimal(policy.max_holm_adjusted_p),
        min_holdout_net_return=_format_decimal(policy.min_holdout_net_return),
        max_holdout_drawdown=_format_decimal(policy.max_holdout_drawdown),
        min_holdout_fills=policy.min_holdout_fills,
        min_execution_fill_ratio=_format_decimal(policy.min_execution_fill_ratio),
        max_execution_adverse_slippage_bps=_format_decimal(
            policy.max_execution_adverse_slippage_bps
        ),
        registered_at=registered_at,
        threshold_policy_hash=policy.threshold_policy_hash,
        candidate_binding_state="AWAITING_CANDIDATE",
    )


def _candidate_view(row: sqlite3.Row) -> CandidatePolicyReadView:
    value = _json_object(row["policy_json"], "candidate policy JSON")
    policy = StrategyPromotionPolicy(
        policy_id=_string(value, "policy_id"),
        threshold_policy_id=_string(value, "threshold_policy_id"),
        threshold_policy_hash=_string(value, "threshold_policy_hash"),
        development_campaign_id=_string(value, "development_campaign_id"),
        holdout_campaign_id=_string(value, "holdout_campaign_id"),
        holdout_trial_id=_string(value, "holdout_trial_id"),
        selected_trial_id=_string(value, "selected_trial_id"),
        selected_trial_fingerprint=_string(value, "selected_trial_fingerprint"),
        selected_strategy_id=_string(value, "selected_strategy_id"),
        selected_strategy_version=_string(value, "selected_strategy_version"),
        tournament_fingerprint=_string(value, "tournament_fingerprint"),
        external_execution_authorized=_false(value, "external_execution_authorized"),
        live_trading=_string(value, "live_trading"),
        policy_hash=_string(value, "policy_hash"),
    )
    _require_row_match(
        row,
        {
            "policy_id": policy.policy_id,
            "policy_hash": policy.policy_hash,
            "threshold_policy_id": policy.threshold_policy_id,
            "threshold_policy_hash": policy.threshold_policy_hash,
            "development_campaign_id": policy.development_campaign_id,
            "holdout_campaign_id": policy.holdout_campaign_id,
            "policy_json": _canonical_json(policy.to_dict()),
        },
    )
    registered_at = _aware_iso(row["registered_at"], "candidate registered_at")
    return CandidatePolicyReadView(
        policy_id=policy.policy_id,
        threshold_policy_id=policy.threshold_policy_id,
        development_campaign_id=policy.development_campaign_id,
        holdout_campaign_id=policy.holdout_campaign_id,
        holdout_trial_id=policy.holdout_trial_id,
        selected_trial_id=policy.selected_trial_id,
        selected_strategy_id=policy.selected_strategy_id,
        selected_strategy_version=policy.selected_strategy_version,
        selected_trial_fingerprint=policy.selected_trial_fingerprint,
        tournament_fingerprint=policy.tournament_fingerprint,
        registered_at=registered_at,
        threshold_policy_hash=policy.threshold_policy_hash,
        policy_hash=policy.policy_hash,
    )


def _snapshot_payload(values: dict[str, object]) -> dict[str, object]:
    return {
        "governance_state": values["governance_state"],
        "thresholds": [item.to_dict() for item in values["thresholds"]],
        "candidates": [item.to_dict() for item in values["candidates"]],
        "required_gate_ids": list(values["required_gate_ids"]),
        "gate_evidence_state": values["gate_evidence_state"],
        "promotion_blockers": list(values["promotion_blockers"]),
        "paper_candidate_authorized": values["paper_candidate_authorized"],
        "external_execution_authorized": values["external_execution_authorized"],
        "capital_authority": values["capital_authority"],
        "live_trading": values["live_trading"],
    }


def _require_row_match(row: sqlite3.Row, expected: dict[str, str]) -> None:
    for key, value in expected.items():
        if str(row[key]) != value:
            raise StrategyLabReadModelIntegrityError(f"SQLite column mismatch: {key}")


def _json_object(raw: object, label: str) -> dict[str, object]:
    if not isinstance(raw, str):
        raise StrategyLabReadModelIntegrityError(f"{label} must be text")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StrategyLabReadModelIntegrityError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise StrategyLabReadModelIntegrityError(f"{label} must be an object")
    return value


def _string(value: dict[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise StrategyLabReadModelIntegrityError(f"{key} must be string")
    return raw


def _false(value: dict[str, object], key: str) -> bool:
    raw = value.get(key)
    if raw is not False:
        raise StrategyLabReadModelIntegrityError(f"{key} must remain false")
    return False


def _decimal(value: dict[str, object], key: str) -> Decimal:
    raw = value.get(key)
    if isinstance(raw, bool):
        raise StrategyLabReadModelIntegrityError(f"{key} must be decimal")
    try:
        result = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise StrategyLabReadModelIntegrityError(f"{key} must be decimal") from exc
    if not result.is_finite():
        raise StrategyLabReadModelIntegrityError(f"{key} must be finite")
    return result


def _integer(value: dict[str, object], key: str) -> int:
    raw = value.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise StrategyLabReadModelIntegrityError(f"{key} must be integer")
    return raw


def _aware_iso(raw: object, label: str) -> str:
    if not isinstance(raw, str):
        raise StrategyLabReadModelIntegrityError(f"{label} must be text")
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise StrategyLabReadModelIntegrityError(f"{label} is invalid") from exc
    _require_aware(value, label)
    return value.astimezone(timezone.utc).isoformat()


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StrategyLabReadModelIntegrityError(f"{label} must be timezone-aware")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise StrategyLabReadModelIntegrityError(f"{label} must be lowercase sha256")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "CandidatePolicyReadView",
    "StrategyLabPromotionReadModel",
    "StrategyLabPromotionReadSnapshot",
    "StrategyLabReadModelError",
    "StrategyLabReadModelIntegrityError",
    "StrategyLabReadModelMissing",
    "ThresholdPolicyReadView",
]
