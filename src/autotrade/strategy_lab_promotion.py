from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import OrderIntent, intent_fingerprint
from autotrade.paper_execution_lab import PaperExecutionSensitivityReport
from autotrade.persistence import SQLiteRuntime
from autotrade.research.multiple_testing import HolmEvidence
from autotrade.research.tournament import TournamentEvidence
from autotrade.research.trials import SQLiteTrialLedger, TrialPhase, TrialRecord, TrialStatus


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# W79 intentionally cannot grant PAPER/LIVE authority. These blockers make the
# scientific gaps machine-visible instead of letting a UI infer promotion from
# a collection of green-looking research cards.
PERMANENT_W79_PROMOTION_BLOCKERS = (
    "EXECUTION_STRATEGY_VERSION_UNBOUND",
    "FEE_ACCOUNTING_INCOMPLETE",
    "SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED",
    "TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN",
)


class StrategyPromotionError(RuntimeError):
    pass


class StrategyPromotionIntegrityError(StrategyPromotionError):
    pass


class StrategyPromotionConflict(StrategyPromotionError):
    pass


class PromotionGateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    MISSING = "MISSING"
    BLOCKED = "BLOCKED"


class PromotionAssessmentState(StrEnum):
    INCOMPLETE = "INCOMPLETE"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    EVIDENCE_QUALIFIED = "EVIDENCE_QUALIFIED"


@dataclass(frozen=True, slots=True)
class StrategyPromotionPolicy:
    """Frozen pre-HOLDOUT promotion thresholds for one selected candidate.

    The policy is candidate-specific and is frozen only after a complete
    DEVELOPMENT tournament has selected a winner, but before the separate
    FINAL_HOLDOUT campaign preregisters its one expected trial.
    """

    policy_id: str
    development_campaign_id: str
    holdout_campaign_id: str
    holdout_trial_id: str
    selected_trial_id: str
    selected_trial_fingerprint: str
    selected_strategy_id: str
    selected_strategy_version: str
    tournament_fingerprint: str
    max_holm_adjusted_p: Decimal
    min_holdout_net_return: Decimal
    max_holdout_drawdown: Decimal
    min_holdout_fills: int
    min_execution_fill_ratio: Decimal
    max_execution_adverse_slippage_bps: Decimal
    external_execution_authorized: bool
    live_trading: str
    policy_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("policy_id", self.policy_id),
            ("development_campaign_id", self.development_campaign_id),
            ("holdout_campaign_id", self.holdout_campaign_id),
            ("holdout_trial_id", self.holdout_trial_id),
            ("selected_trial_id", self.selected_trial_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
        ):
            if not isinstance(value, str) or not _ID_RE.fullmatch(value):
                raise StrategyPromotionIntegrityError(f"{label} must be a canonical identifier")
        if self.development_campaign_id == self.holdout_campaign_id:
            raise StrategyPromotionIntegrityError("development and HOLDOUT campaigns must be distinct")
        for label, value in (
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("tournament_fingerprint", self.tournament_fingerprint),
            ("policy_hash", self.policy_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("max_holm_adjusted_p", self.max_holm_adjusted_p),
            ("min_holdout_net_return", self.min_holdout_net_return),
            ("max_holdout_drawdown", self.max_holdout_drawdown),
            ("min_execution_fill_ratio", self.min_execution_fill_ratio),
            ("max_execution_adverse_slippage_bps", self.max_execution_adverse_slippage_bps),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise StrategyPromotionIntegrityError(f"{label} must be finite Decimal")
        if not Decimal("0") <= self.max_holm_adjusted_p <= Decimal("1"):
            raise StrategyPromotionIntegrityError("max_holm_adjusted_p must be within [0,1]")
        if self.min_holdout_net_return <= Decimal("-1"):
            raise StrategyPromotionIntegrityError("min_holdout_net_return must be greater than -1")
        if not Decimal("0") <= self.max_holdout_drawdown <= Decimal("1"):
            raise StrategyPromotionIntegrityError("max_holdout_drawdown must be within [0,1]")
        if isinstance(self.min_holdout_fills, bool) or not isinstance(self.min_holdout_fills, int):
            raise StrategyPromotionIntegrityError("min_holdout_fills must be integer")
        if self.min_holdout_fills < 1:
            raise StrategyPromotionIntegrityError("min_holdout_fills must be >=1")
        if not Decimal("0") <= self.min_execution_fill_ratio <= Decimal("1"):
            raise StrategyPromotionIntegrityError("min_execution_fill_ratio must be within [0,1]")
        if self.max_execution_adverse_slippage_bps < 0:
            raise StrategyPromotionIntegrityError(
                "max_execution_adverse_slippage_bps must be non-negative"
            )
        if self.external_execution_authorized is not False or self.live_trading != "BLOCKED":
            raise StrategyPromotionIntegrityError("W79 policy may not grant PAPER/LIVE authority")
        if self.policy_hash != _hash(_policy_payload(self, include_hash=False)):
            raise StrategyPromotionIntegrityError("promotion policy hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _policy_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PromotionGateEvidence:
    gate_id: str
    status: PromotionGateStatus
    reason_codes: tuple[str, ...]
    evidence_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.gate_id, str) or not _ID_RE.fullmatch(self.gate_id):
            raise StrategyPromotionIntegrityError("gate_id must be canonical")
        if not isinstance(self.status, PromotionGateStatus):
            raise StrategyPromotionIntegrityError("gate status must use PromotionGateStatus")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise StrategyPromotionIntegrityError("gate reason_codes must be unique sorted order")
        if any(not isinstance(value, str) or not value.strip() for value in self.reason_codes):
            raise StrategyPromotionIntegrityError("gate reason code is invalid")
        if self.evidence_hashes != tuple(sorted(set(self.evidence_hashes))):
            raise StrategyPromotionIntegrityError("gate evidence_hashes must be unique sorted order")
        for value in self.evidence_hashes:
            _require_hash(value, "gate evidence hash")
        if self.status is PromotionGateStatus.PASS and self.reason_codes:
            raise StrategyPromotionIntegrityError("PASS gate may not carry failure reasons")

    def to_dict(self) -> dict[str, object]:
        return {
            "gate_id": self.gate_id,
            "status": self.status.value,
            "reason_codes": list(self.reason_codes),
            "evidence_hashes": list(self.evidence_hashes),
        }


@dataclass(frozen=True, slots=True)
class StrategyPromotionEvidenceView:
    policy_id: str
    policy_hash: str
    selected_strategy_id: str
    selected_strategy_version: str
    gates: tuple[PromotionGateEvidence, ...]
    evidence_complete: bool
    assessment_state: PromotionAssessmentState
    promotion_blockers: tuple[str, ...]
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    live_trading: str
    view_hash: str

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.policy_id):
            raise StrategyPromotionIntegrityError("policy_id must be canonical")
        _require_hash(self.policy_hash, "policy_hash")
        _require_hash(self.view_hash, "view_hash")
        if not _ID_RE.fullmatch(self.selected_strategy_id) or not _ID_RE.fullmatch(
            self.selected_strategy_version
        ):
            raise StrategyPromotionIntegrityError("selected strategy identity is invalid")
        if not self.gates:
            raise StrategyPromotionIntegrityError("promotion evidence requires gates")
        if self.gates != tuple(sorted(self.gates, key=lambda item: item.gate_id)):
            raise StrategyPromotionIntegrityError("promotion gates must be sorted by gate_id")
        if len({item.gate_id for item in self.gates}) != len(self.gates):
            raise StrategyPromotionIntegrityError("duplicate promotion gate")
        expected_complete = all(item.status is PromotionGateStatus.PASS for item in self.gates)
        if self.evidence_complete is not expected_complete:
            raise StrategyPromotionIntegrityError("evidence_complete does not match gate states")
        expected_state = _assessment_state(self.gates)
        if self.assessment_state is not expected_state:
            raise StrategyPromotionIntegrityError("assessment_state does not match gate states")
        if self.promotion_blockers != tuple(sorted(set(self.promotion_blockers))):
            raise StrategyPromotionIntegrityError("promotion_blockers must be unique sorted order")
        if not self.promotion_blockers:
            raise StrategyPromotionIntegrityError("W79 must retain explicit promotion blockers")
        if self.paper_candidate_authorized is not False:
            raise StrategyPromotionIntegrityError("W79 may not authorize PAPER candidate promotion")
        if self.external_execution_authorized is not False or self.live_trading != "BLOCKED":
            raise StrategyPromotionIntegrityError("W79 evidence may not grant execution/LIVE authority")
        if self.view_hash != _hash(_view_payload(self, include_hash=False)):
            raise StrategyPromotionIntegrityError("promotion evidence view hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _view_payload(self, include_hash=True)


class SQLiteStrategyPromotionPolicyRegistry:
    """Append-only frozen policy registry on the same durable SQLite runtime."""

    def __init__(self, runtime: SQLiteRuntime | str) -> None:
        self._runtime = runtime if isinstance(runtime, SQLiteRuntime) else SQLiteRuntime(runtime)
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_promotion_policies (
                    policy_id TEXT PRIMARY KEY,
                    policy_hash TEXT NOT NULL UNIQUE,
                    development_campaign_id TEXT NOT NULL,
                    holdout_campaign_id TEXT NOT NULL UNIQUE,
                    registered_at TEXT NOT NULL,
                    policy_json TEXT NOT NULL
                )
                """
            )
        finally:
            conn.close()

    def register(
        self,
        policy: StrategyPromotionPolicy,
        *,
        trial_ledger: SQLiteTrialLedger,
        tournament: TournamentEvidence,
        now: datetime,
    ) -> StrategyPromotionPolicy:
        _require_aware(now, "now")
        _validate_freeze_preconditions(
            policy=policy,
            trial_ledger=trial_ledger,
            tournament=tournament,
        )
        payload = _canonical_json(policy.to_dict())
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT policy_hash, policy_json FROM strategy_promotion_policies WHERE policy_id = ?",
                (policy.policy_id,),
            ).fetchone()
            if row is not None:
                if row["policy_hash"] != policy.policy_hash or row["policy_json"] != payload:
                    raise StrategyPromotionConflict(f"promotion policy identity conflict: {policy.policy_id}")
                conn.execute("COMMIT")
                return policy
            other = conn.execute(
                "SELECT policy_id FROM strategy_promotion_policies WHERE holdout_campaign_id = ?",
                (policy.holdout_campaign_id,),
            ).fetchone()
            if other is not None:
                raise StrategyPromotionConflict(
                    f"HOLDOUT campaign already frozen by policy: {other['policy_id']}"
                )
            conn.execute(
                """
                INSERT INTO strategy_promotion_policies(
                    policy_id, policy_hash, development_campaign_id,
                    holdout_campaign_id, registered_at, policy_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    policy.policy_id,
                    policy.policy_hash,
                    policy.development_campaign_id,
                    policy.holdout_campaign_id,
                    now.isoformat(),
                    payload,
                ),
            )
            conn.execute("COMMIT")
            return policy
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, policy_id: str) -> StrategyPromotionPolicy | None:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT policy_json FROM strategy_promotion_policies WHERE policy_id = ?",
                (policy_id,),
            ).fetchone()
        finally:
            conn.close()
        return _policy_from_json(row["policy_json"]) if row is not None else None


def build_strategy_promotion_policy(
    *,
    policy_id: str,
    development_campaign_id: str,
    holdout_campaign_id: str,
    holdout_trial_id: str,
    trial_ledger: SQLiteTrialLedger,
    tournament: TournamentEvidence,
    max_holm_adjusted_p: Decimal,
    min_holdout_net_return: Decimal,
    max_holdout_drawdown: Decimal,
    min_holdout_fills: int,
    min_execution_fill_ratio: Decimal,
    max_execution_adverse_slippage_bps: Decimal,
) -> StrategyPromotionPolicy:
    if tournament.campaign_id != development_campaign_id:
        raise StrategyPromotionIntegrityError("tournament belongs to another development campaign")
    if not tournament.winner_trial_id:
        raise StrategyPromotionIntegrityError("promotion policy requires an eligible tournament winner")
    trial_ledger.require_complete_campaign(development_campaign_id)
    selected = trial_ledger.get_trial(tournament.winner_trial_id)
    if selected is None:
        raise StrategyPromotionIntegrityError("tournament winner is missing from trial ledger")
    if selected.status is not TrialStatus.COMPLETED or selected.spec.phase is not TrialPhase.DEVELOPMENT:
        raise StrategyPromotionIntegrityError("tournament winner must be completed DEVELOPMENT trial")
    entry = next(
        (item for item in tournament.entries if item.trial_id == tournament.winner_trial_id),
        None,
    )
    if entry is None or not entry.eligible:
        raise StrategyPromotionIntegrityError("tournament winner entry is not eligible")
    if (
        entry.strategy_id != selected.spec.strategy_id
        or entry.strategy_version != selected.spec.strategy_version
        or entry.result_hash != selected.result_hash
    ):
        raise StrategyPromotionIntegrityError("tournament winner identity does not match durable trial")

    holdout = trial_ledger.campaign_accounting(holdout_campaign_id)
    if holdout.expected_trial_ids != (holdout_trial_id,):
        raise StrategyPromotionIntegrityError("HOLDOUT campaign must freeze exactly one expected trial")
    if holdout.preregistered_trial_ids:
        raise StrategyPromotionIntegrityError(
            "promotion thresholds must be frozen before HOLDOUT trial preregistration"
        )

    values = {
        "policy_id": policy_id,
        "development_campaign_id": development_campaign_id,
        "holdout_campaign_id": holdout_campaign_id,
        "holdout_trial_id": holdout_trial_id,
        "selected_trial_id": selected.spec.trial_id,
        "selected_trial_fingerprint": selected.spec.fingerprint,
        "selected_strategy_id": selected.spec.strategy_id,
        "selected_strategy_version": selected.spec.strategy_version,
        "tournament_fingerprint": tournament.fingerprint,
        "max_holm_adjusted_p": max_holm_adjusted_p,
        "min_holdout_net_return": min_holdout_net_return,
        "max_holdout_drawdown": max_holdout_drawdown,
        "min_holdout_fills": min_holdout_fills,
        "min_execution_fill_ratio": min_execution_fill_ratio,
        "max_execution_adverse_slippage_bps": max_execution_adverse_slippage_bps,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    return StrategyPromotionPolicy(
        **values,
        policy_hash=_hash(_policy_payload_from_values(values)),
    )


def evaluate_strategy_promotion(
    *,
    registry: SQLiteStrategyPromotionPolicyRegistry,
    policy_id: str,
    trial_ledger: SQLiteTrialLedger,
    tournament: TournamentEvidence,
    holm: HolmEvidence | None = None,
    execution_report: PaperExecutionSensitivityReport | None = None,
    execution_intent: OrderIntent | None = None,
) -> StrategyPromotionEvidenceView:
    policy = registry.get(policy_id)
    if policy is None:
        raise StrategyPromotionIntegrityError(f"unknown frozen promotion policy: {policy_id}")

    gates = tuple(
        sorted(
            (
                _development_gate(policy, trial_ledger, tournament),
                _holm_gate(policy, holm),
                _holdout_gate(policy, trial_ledger),
                _execution_gate(policy, execution_report, execution_intent),
            ),
            key=lambda item: item.gate_id,
        )
    )
    blockers = tuple(sorted(PERMANENT_W79_PROMOTION_BLOCKERS))
    values = {
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "selected_strategy_id": policy.selected_strategy_id,
        "selected_strategy_version": policy.selected_strategy_version,
        "gates": gates,
        "evidence_complete": all(item.status is PromotionGateStatus.PASS for item in gates),
        "assessment_state": _assessment_state(gates),
        "promotion_blockers": blockers,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    return StrategyPromotionEvidenceView(
        **values,
        view_hash=_hash(_view_payload_from_values(values)),
    )


def _validate_freeze_preconditions(
    *,
    policy: StrategyPromotionPolicy,
    trial_ledger: SQLiteTrialLedger,
    tournament: TournamentEvidence,
) -> None:
    development = trial_ledger.require_complete_campaign(policy.development_campaign_id)
    if not development.completed_trial_ids:
        raise StrategyPromotionIntegrityError("development campaign has no completed trials")
    if tournament.campaign_id != policy.development_campaign_id:
        raise StrategyPromotionIntegrityError("tournament/development campaign mismatch")
    if tournament.fingerprint != policy.tournament_fingerprint:
        raise StrategyPromotionIntegrityError("tournament fingerprint differs from frozen policy")
    if tournament.winner_trial_id != policy.selected_trial_id:
        raise StrategyPromotionIntegrityError("tournament winner differs from frozen candidate")
    selected = trial_ledger.get_trial(policy.selected_trial_id)
    if selected is None or selected.status is not TrialStatus.COMPLETED:
        raise StrategyPromotionIntegrityError("frozen candidate trial is not completed")
    if selected.spec.phase is not TrialPhase.DEVELOPMENT:
        raise StrategyPromotionIntegrityError("frozen candidate must come from DEVELOPMENT")
    if selected.spec.fingerprint != policy.selected_trial_fingerprint:
        raise StrategyPromotionIntegrityError("selected trial fingerprint changed")
    if (
        selected.spec.strategy_id != policy.selected_strategy_id
        or selected.spec.strategy_version != policy.selected_strategy_version
    ):
        raise StrategyPromotionIntegrityError("selected strategy identity changed")
    holdout = trial_ledger.campaign_accounting(policy.holdout_campaign_id)
    if holdout.expected_trial_ids != (policy.holdout_trial_id,):
        raise StrategyPromotionIntegrityError("HOLDOUT expected-trial universe changed")
    if holdout.preregistered_trial_ids:
        raise StrategyPromotionIntegrityError(
            "policy registration occurred after HOLDOUT preregistration"
        )


def _development_gate(
    policy: StrategyPromotionPolicy,
    trial_ledger: SQLiteTrialLedger,
    tournament: TournamentEvidence,
) -> PromotionGateEvidence:
    reasons: list[str] = []
    hashes: list[str] = []
    if tournament.campaign_id != policy.development_campaign_id:
        reasons.append("TOURNAMENT_CAMPAIGN_MISMATCH")
    else:
        hashes.append(tournament.fingerprint)
    if tournament.fingerprint != policy.tournament_fingerprint:
        reasons.append("TOURNAMENT_FINGERPRINT_MISMATCH")
    if tournament.winner_trial_id != policy.selected_trial_id:
        reasons.append("TOURNAMENT_WINNER_MISMATCH")
    selected = trial_ledger.get_trial(policy.selected_trial_id)
    if selected is None:
        reasons.append("SELECTED_TRIAL_MISSING")
    else:
        hashes.extend((selected.spec.fingerprint, selected.result_hash))
        if selected.status is not TrialStatus.COMPLETED:
            reasons.append("SELECTED_TRIAL_NOT_COMPLETED")
        if selected.spec.phase is not TrialPhase.DEVELOPMENT:
            reasons.append("SELECTED_TRIAL_NOT_DEVELOPMENT")
        if selected.spec.fingerprint != policy.selected_trial_fingerprint:
            reasons.append("SELECTED_TRIAL_FINGERPRINT_MISMATCH")
        if (
            selected.spec.strategy_id != policy.selected_strategy_id
            or selected.spec.strategy_version != policy.selected_strategy_version
        ):
            reasons.append("SELECTED_STRATEGY_IDENTITY_MISMATCH")
    return _gate(
        "DEVELOPMENT_SELECTION",
        PromotionGateStatus.BLOCKED if reasons else PromotionGateStatus.PASS,
        reasons,
        hashes,
    )


def _holm_gate(
    policy: StrategyPromotionPolicy,
    holm: HolmEvidence | None,
) -> PromotionGateEvidence:
    if holm is None:
        return _gate(
            "MULTIPLE_TESTING",
            PromotionGateStatus.MISSING,
            ("HOLM_EVIDENCE_MISSING",),
            (),
        )
    if holm.campaign_id != policy.development_campaign_id:
        return _gate(
            "MULTIPLE_TESTING",
            PromotionGateStatus.BLOCKED,
            ("HOLM_CAMPAIGN_MISMATCH",),
            (),
        )
    if policy.selected_trial_id not in holm.adjusted_p_values:
        return _gate(
            "MULTIPLE_TESTING",
            PromotionGateStatus.BLOCKED,
            ("SELECTED_TRIAL_ADJUSTED_P_MISSING",),
            (),
        )
    adjusted = _finite_decimal(
        holm.adjusted_p_values[policy.selected_trial_id],
        "Holm adjusted p-value",
    )
    evidence_hash = _hash(
        {
            "campaign_id": holm.campaign_id,
            "family_size": holm.family_size,
            "raw_p_values": dict(sorted(holm.raw_p_values.items())),
            "adjusted_p_values": dict(sorted(holm.adjusted_p_values.items())),
            "failed_trial_ids": list(holm.failed_trial_ids),
        }
    )
    reasons: list[str] = []
    if policy.selected_trial_id in holm.failed_trial_ids:
        reasons.append("SELECTED_TRIAL_FAILED_IN_FAMILY")
    if adjusted > policy.max_holm_adjusted_p:
        reasons.append("HOLM_ADJUSTED_P_ABOVE_POLICY")
    return _gate(
        "MULTIPLE_TESTING",
        PromotionGateStatus.FAIL if reasons else PromotionGateStatus.PASS,
        reasons,
        (evidence_hash,),
    )


def _holdout_gate(
    policy: StrategyPromotionPolicy,
    trial_ledger: SQLiteTrialLedger,
) -> PromotionGateEvidence:
    accounting = trial_ledger.campaign_accounting(policy.holdout_campaign_id)
    if accounting.expected_trial_ids != (policy.holdout_trial_id,):
        return _gate(
            "FINAL_HOLDOUT",
            PromotionGateStatus.BLOCKED,
            ("HOLDOUT_UNIVERSE_MISMATCH",),
            (),
        )
    record = trial_ledger.get_trial(policy.holdout_trial_id)
    if record is None or record.status is TrialStatus.PREREGISTERED:
        hashes = (record.spec.fingerprint,) if record is not None else ()
        return _gate(
            "FINAL_HOLDOUT",
            PromotionGateStatus.MISSING,
            ("HOLDOUT_RESULT_MISSING",),
            hashes,
        )
    hashes = (record.spec.fingerprint, record.result_hash)
    identity_reasons = _holdout_identity_reasons(policy, record)
    if identity_reasons:
        return _gate(
            "FINAL_HOLDOUT",
            PromotionGateStatus.BLOCKED,
            identity_reasons,
            hashes,
        )
    if record.status is TrialStatus.FAILED:
        return _gate(
            "FINAL_HOLDOUT",
            PromotionGateStatus.FAIL,
            ("HOLDOUT_TRIAL_FAILED",),
            hashes,
        )

    reasons: list[str] = []
    net_return = _metric_decimal(record, "net_return")
    max_drawdown = _metric_decimal(record, "max_drawdown")
    fills = _metric_int(record, "fills")
    if net_return < policy.min_holdout_net_return:
        reasons.append("HOLDOUT_NET_RETURN_BELOW_POLICY")
    if max_drawdown > policy.max_holdout_drawdown:
        reasons.append("HOLDOUT_DRAWDOWN_ABOVE_POLICY")
    if fills < policy.min_holdout_fills:
        reasons.append("HOLDOUT_FILLS_BELOW_POLICY")
    return _gate(
        "FINAL_HOLDOUT",
        PromotionGateStatus.FAIL if reasons else PromotionGateStatus.PASS,
        reasons,
        hashes,
    )


def _execution_gate(
    policy: StrategyPromotionPolicy,
    report: PaperExecutionSensitivityReport | None,
    intent: OrderIntent | None,
) -> PromotionGateEvidence:
    if report is None or intent is None:
        return _gate(
            "EXECUTION_SENSITIVITY",
            PromotionGateStatus.MISSING,
            ("EXECUTION_REPORT_OR_INTENT_MISSING",),
            (),
        )
    hashes = (report.measurement_report_hash, report.trace_report_hash)
    identity_reasons: list[str] = []
    if intent.strategy_id != policy.selected_strategy_id:
        identity_reasons.append("EXECUTION_STRATEGY_ID_MISMATCH")
    if report.intent_fingerprint != intent_fingerprint(intent):
        identity_reasons.append("EXECUTION_INTENT_FINGERPRINT_MISMATCH")
    if identity_reasons:
        return _gate(
            "EXECUTION_SENSITIVITY",
            PromotionGateStatus.BLOCKED,
            identity_reasons,
            hashes,
        )

    reasons: list[str] = []
    if report.risk_rejection_count:
        reasons.append("EXECUTION_RISK_REJECTION_PRESENT")
    if report.broker_rejection_count:
        reasons.append("EXECUTION_BROKER_REJECTION_PRESENT")
    if report.minimum_fill_ratio is None:
        reasons.append("EXECUTION_FILL_RATIO_MISSING")
    elif report.minimum_fill_ratio < policy.min_execution_fill_ratio:
        reasons.append("EXECUTION_FILL_RATIO_BELOW_POLICY")
    if report.maximum_adverse_slippage_bps is None:
        reasons.append("EXECUTION_SLIPPAGE_MISSING")
    elif report.maximum_adverse_slippage_bps > policy.max_execution_adverse_slippage_bps:
        reasons.append("EXECUTION_SLIPPAGE_ABOVE_POLICY")
    return _gate(
        "EXECUTION_SENSITIVITY",
        PromotionGateStatus.FAIL if reasons else PromotionGateStatus.PASS,
        reasons,
        hashes,
    )


def _holdout_identity_reasons(
    policy: StrategyPromotionPolicy,
    record: TrialRecord,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if record.spec.campaign_id != policy.holdout_campaign_id:
        reasons.append("HOLDOUT_CAMPAIGN_MISMATCH")
    if record.spec.phase is not TrialPhase.FINAL_HOLDOUT:
        reasons.append("HOLDOUT_PHASE_INVALID")
    if record.spec.trial_id != policy.holdout_trial_id:
        reasons.append("HOLDOUT_TRIAL_ID_MISMATCH")
    if record.spec.strategy_id != policy.selected_strategy_id:
        reasons.append("HOLDOUT_STRATEGY_ID_MISMATCH")
    if record.spec.strategy_version != policy.selected_strategy_version:
        reasons.append("HOLDOUT_STRATEGY_VERSION_MISMATCH")
    if not record.spec.holdout_authorization_id:
        reasons.append("HOLDOUT_AUTHORIZATION_MISSING")
    return tuple(sorted(reasons))


def _metric_decimal(record: TrialRecord, name: str) -> Decimal:
    if name not in record.metrics:
        raise StrategyPromotionIntegrityError(f"HOLDOUT metric missing: {name}")
    return _finite_decimal(record.metrics[name], f"HOLDOUT metric {name}")


def _metric_int(record: TrialRecord, name: str) -> int:
    if name not in record.metrics:
        raise StrategyPromotionIntegrityError(f"HOLDOUT metric missing: {name}")
    raw = record.metrics[name]
    if isinstance(raw, bool):
        raise StrategyPromotionIntegrityError(f"HOLDOUT metric {name} must be integer")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float) and raw.is_integer():
        value = int(raw)
    elif isinstance(raw, str):
        try:
            decimal = Decimal(raw)
        except InvalidOperation as exc:
            raise StrategyPromotionIntegrityError(f"HOLDOUT metric {name} must be integer") from exc
        if not decimal.is_finite() or decimal != decimal.to_integral_value():
            raise StrategyPromotionIntegrityError(f"HOLDOUT metric {name} must be integer")
        value = int(decimal)
    else:
        raise StrategyPromotionIntegrityError(f"HOLDOUT metric {name} must be integer")
    if value < 0:
        raise StrategyPromotionIntegrityError(f"HOLDOUT metric {name} must be non-negative")
    return value


def _finite_decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool):
        raise StrategyPromotionIntegrityError(f"{label} must be numeric")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise StrategyPromotionIntegrityError(f"{label} must be numeric") from exc
    if not decimal.is_finite():
        raise StrategyPromotionIntegrityError(f"{label} must be finite")
    return decimal


def _gate(
    gate_id: str,
    status: PromotionGateStatus,
    reasons,
    hashes,
) -> PromotionGateEvidence:
    return PromotionGateEvidence(
        gate_id=gate_id,
        status=status,
        reason_codes=tuple(sorted(set(reasons))),
        evidence_hashes=tuple(sorted(set(hashes))),
    )


def _assessment_state(gates: tuple[PromotionGateEvidence, ...]) -> PromotionAssessmentState:
    statuses = {item.status for item in gates}
    if PromotionGateStatus.FAIL in statuses:
        return PromotionAssessmentState.REJECTED
    if PromotionGateStatus.BLOCKED in statuses:
        return PromotionAssessmentState.BLOCKED
    if PromotionGateStatus.MISSING in statuses:
        return PromotionAssessmentState.INCOMPLETE
    return PromotionAssessmentState.EVIDENCE_QUALIFIED


def _policy_payload(
    value: StrategyPromotionPolicy,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload = _policy_payload_from_values(
        {
            "policy_id": value.policy_id,
            "development_campaign_id": value.development_campaign_id,
            "holdout_campaign_id": value.holdout_campaign_id,
            "holdout_trial_id": value.holdout_trial_id,
            "selected_trial_id": value.selected_trial_id,
            "selected_trial_fingerprint": value.selected_trial_fingerprint,
            "selected_strategy_id": value.selected_strategy_id,
            "selected_strategy_version": value.selected_strategy_version,
            "tournament_fingerprint": value.tournament_fingerprint,
            "max_holm_adjusted_p": value.max_holm_adjusted_p,
            "min_holdout_net_return": value.min_holdout_net_return,
            "max_holdout_drawdown": value.max_holdout_drawdown,
            "min_holdout_fills": value.min_holdout_fills,
            "min_execution_fill_ratio": value.min_execution_fill_ratio,
            "max_execution_adverse_slippage_bps": value.max_execution_adverse_slippage_bps,
            "external_execution_authorized": value.external_execution_authorized,
            "live_trading": value.live_trading,
        }
    )
    if include_hash:
        payload["policy_hash"] = value.policy_hash
    return payload


def _policy_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    for key in (
        "max_holm_adjusted_p",
        "min_holdout_net_return",
        "max_holdout_drawdown",
        "min_execution_fill_ratio",
        "max_execution_adverse_slippage_bps",
    ):
        payload[key] = _decimal(payload[key])  # type: ignore[arg-type]
    return payload


def _policy_from_json(raw: str) -> StrategyPromotionPolicy:
    value = json.loads(raw)
    return StrategyPromotionPolicy(
        policy_id=value["policy_id"],
        development_campaign_id=value["development_campaign_id"],
        holdout_campaign_id=value["holdout_campaign_id"],
        holdout_trial_id=value["holdout_trial_id"],
        selected_trial_id=value["selected_trial_id"],
        selected_trial_fingerprint=value["selected_trial_fingerprint"],
        selected_strategy_id=value["selected_strategy_id"],
        selected_strategy_version=value["selected_strategy_version"],
        tournament_fingerprint=value["tournament_fingerprint"],
        max_holm_adjusted_p=Decimal(value["max_holm_adjusted_p"]),
        min_holdout_net_return=Decimal(value["min_holdout_net_return"]),
        max_holdout_drawdown=Decimal(value["max_holdout_drawdown"]),
        min_holdout_fills=int(value["min_holdout_fills"]),
        min_execution_fill_ratio=Decimal(value["min_execution_fill_ratio"]),
        max_execution_adverse_slippage_bps=Decimal(value["max_execution_adverse_slippage_bps"]),
        external_execution_authorized=value["external_execution_authorized"],
        live_trading=value["live_trading"],
        policy_hash=value["policy_hash"],
    )


def _view_payload(
    value: StrategyPromotionEvidenceView,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload = _view_payload_from_values(
        {
            "policy_id": value.policy_id,
            "policy_hash": value.policy_hash,
            "selected_strategy_id": value.selected_strategy_id,
            "selected_strategy_version": value.selected_strategy_version,
            "gates": value.gates,
            "evidence_complete": value.evidence_complete,
            "assessment_state": value.assessment_state,
            "promotion_blockers": value.promotion_blockers,
            "paper_candidate_authorized": value.paper_candidate_authorized,
            "external_execution_authorized": value.external_execution_authorized,
            "live_trading": value.live_trading,
        }
    )
    if include_hash:
        payload["view_hash"] = value.view_hash
    return payload


def _view_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    gates = payload["gates"]
    payload["gates"] = [item.to_dict() for item in gates]  # type: ignore[union-attr]
    state = payload["assessment_state"]
    payload["assessment_state"] = state.value  # type: ignore[union-attr]
    payload["promotion_blockers"] = list(payload["promotion_blockers"])  # type: ignore[arg-type]
    return payload


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise StrategyPromotionIntegrityError(f"{label} must be lowercase sha256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StrategyPromotionIntegrityError(f"{label} must be timezone-aware datetime")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "PERMANENT_W79_PROMOTION_BLOCKERS",
    "PromotionAssessmentState",
    "PromotionGateEvidence",
    "PromotionGateStatus",
    "SQLiteStrategyPromotionPolicyRegistry",
    "StrategyPromotionConflict",
    "StrategyPromotionError",
    "StrategyPromotionEvidenceView",
    "StrategyPromotionIntegrityError",
    "StrategyPromotionPolicy",
    "build_strategy_promotion_policy",
    "evaluate_strategy_promotion",
]
