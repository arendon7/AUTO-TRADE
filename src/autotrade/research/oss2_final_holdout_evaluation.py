"""OSS-2H single-use FINAL_HOLDOUT evaluator.

OSS-2H is the first OSS-2 boundary allowed to consume protected FINAL_HOLDOUT
material. It accepts exactly one canonical OSS-2G protocol, independently
binds the selected DEVELOPMENT trial from durable research state, reconstructs
the exact frozen cross-sectional backtest configuration, burns the
``final_validation`` authorization before checkout, runs one deterministic
research backtest, evaluates only the three preregistered OSS-2G gates and
writes an append-only terminal PASS/FAIL receipt.

A consumed authorization is never reusable. Any evaluation error after
checkout is a terminal scientific FAIL. This module owns no broker, network,
OMS, Safety writer, OrderIntent, PAPER-execution, capital or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
import sqlite3

from .cross_sectional_backtest import CrossSectionalBacktestEngine
from .oss2_campaign import backtest_config_from_oss2_trial
from .oss2_final_holdout_protocol import (
    OSS2G_CONTRACT_VERSION,
    OSS2FinalHoldoutProtocolReceipt,
    canonical_oss2g_policy,
)
from .registry import HoldoutPermit, SQLiteExperimentRegistry
from .trials import TrialPhase, TrialSpec, TrialStatus
from .universe import AlignedMarketUniverse


OSS2H_CONTRACT_VERSION = "OSS2H_FINAL_HOLDOUT_EVALUATION_V1"
_ISSUED_BY = "OSS2H_FINAL_HOLDOUT_EVALUATOR"
_FINAL_HOLDOUT = "FINAL_HOLDOUT"
_FINAL_VALIDATION = "final_validation"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class OSS2FinalHoldoutEvaluationError(RuntimeError):
    pass


class OSS2FinalHoldoutEvaluationGovernanceError(OSS2FinalHoldoutEvaluationError):
    pass


class OSS2FinalHoldoutEvaluationIntegrityError(OSS2FinalHoldoutEvaluationError):
    pass


class OSS2FinalHoldoutAlreadyConsumed(OSS2FinalHoldoutEvaluationError):
    pass


class OSS2FinalHoldoutDecision(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class OSS2SelectedCandidateBinding:
    """Read-only binding to the exact completed DEVELOPMENT winner."""

    campaign_id: str
    trial_id: str
    trial_fingerprint: str
    result_hash: str
    config_hash: str
    spec: TrialSpec

    def __post_init__(self) -> None:
        _require_id(self.campaign_id, "campaign_id")
        _require_id(self.trial_id, "trial_id")
        _require_hash(self.trial_fingerprint, "trial_fingerprint")
        _require_hash(self.result_hash, "result_hash")
        _require_hash(self.config_hash, "config_hash")
        if self.spec.campaign_id != self.campaign_id or self.spec.trial_id != self.trial_id:
            raise OSS2FinalHoldoutEvaluationIntegrityError("candidate binding identity mismatch")
        if self.spec.phase is not TrialPhase.DEVELOPMENT:
            raise OSS2FinalHoldoutEvaluationIntegrityError(
                "OSS-2H selected candidate must be a DEVELOPMENT trial"
            )
        if self.spec.holdout_authorization_id:
            raise OSS2FinalHoldoutEvaluationIntegrityError(
                "DEVELOPMENT candidate may not carry holdout authorization"
            )
        if self.spec.fingerprint != self.trial_fingerprint:
            raise OSS2FinalHoldoutEvaluationIntegrityError("candidate trial fingerprint mismatch")
        config = backtest_config_from_oss2_trial(self.spec)
        if config.config_hash != self.config_hash:
            raise OSS2FinalHoldoutEvaluationIntegrityError("candidate config hash mismatch")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "campaign_id": self.campaign_id,
                "trial_id": self.trial_id,
                "trial_fingerprint": self.trial_fingerprint,
                "result_hash": self.result_hash,
                "config_hash": self.config_hash,
            }
        )


class ProtectedOSS2FinalHoldout:
    """Opaque one-process wrapper around the protected aligned universe.

    Only the immutable universe hash is exposed before consumption. The actual
    universe can be returned once, and only to an exact canonical holdout
    permit identity supplied by OSS-2H after durable consumption.
    """

    __slots__ = ("__universe", "__checked_out")

    def __init__(self, universe: AlignedMarketUniverse) -> None:
        if not isinstance(universe, AlignedMarketUniverse):
            raise TypeError("FINAL_HOLDOUT must be an AlignedMarketUniverse")
        self.__universe = universe
        self.__checked_out = False

    @property
    def universe_hash(self) -> str:
        return self.__universe.universe_hash

    def _checkout(
        self,
        *,
        permit: HoldoutPermit,
        expected_authorization_id: str,
    ) -> AlignedMarketUniverse:
        if self.__checked_out:
            raise OSS2FinalHoldoutAlreadyConsumed("protected FINAL_HOLDOUT already checked out")
        if (
            permit.permit_id != expected_authorization_id
            or permit.purpose != _FINAL_VALIDATION
            or permit.issued_by != _ISSUED_BY
        ):
            raise OSS2FinalHoldoutEvaluationGovernanceError(
                "FINAL_HOLDOUT checkout requires the exact consumed OSS-2H permit"
            )
        self.__checked_out = True
        return self.__universe


@dataclass(frozen=True, slots=True)
class OSS2FinalHoldoutGate:
    gate_id: str
    passed: bool
    observed: float
    comparison: str
    threshold: float

    def __post_init__(self) -> None:
        if self.gate_id not in {
            "FINAL_NET_RETURN_MIN",
            "FINAL_SHARPE_MIN",
            "FINAL_DRAWDOWN_MAX",
        }:
            raise ValueError("noncanonical OSS-2H gate id")
        if self.comparison not in {">=", "<="}:
            raise ValueError("invalid OSS-2H gate comparison")
        if not isfinite(self.observed) or not isfinite(self.threshold):
            raise ValueError("OSS-2H gate values must be finite")
        expected = self.observed >= self.threshold if self.comparison == ">=" else self.observed <= self.threshold
        if self.passed is not expected:
            raise ValueError("OSS-2H gate pass flag does not match comparison")


@dataclass(frozen=True, slots=True)
class OSS2FinalHoldoutStartReceipt:
    evaluation_id: str
    contract_version: str
    campaign_id: str
    selected_trial_id: str
    protocol_id: str
    protocol_receipt_hash: str
    candidate_binding_fingerprint: str
    holdout_authorization_id: str
    holdout_universe_hash: str
    config_hash: str
    started_at: str
    start_hash: str

    def __post_init__(self) -> None:
        for name, value in (
            ("evaluation_id", self.evaluation_id),
            ("campaign_id", self.campaign_id),
            ("selected_trial_id", self.selected_trial_id),
            ("protocol_id", self.protocol_id),
            ("holdout_authorization_id", self.holdout_authorization_id),
        ):
            _require_id(value, name)
        for name, value in (
            ("protocol_receipt_hash", self.protocol_receipt_hash),
            ("candidate_binding_fingerprint", self.candidate_binding_fingerprint),
            ("holdout_universe_hash", self.holdout_universe_hash),
            ("config_hash", self.config_hash),
            ("start_hash", self.start_hash),
        ):
            _require_hash(value, name)
        if self.contract_version != OSS2H_CONTRACT_VERSION:
            raise OSS2FinalHoldoutEvaluationIntegrityError("noncanonical OSS-2H start version")
        _require_aware_iso(self.started_at, "started_at")
        if self.start_hash != _hash(_start_payload(self, include_hash=False)):
            raise OSS2FinalHoldoutEvaluationIntegrityError("OSS-2H start hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _start_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class OSS2FinalHoldoutEvaluationReceipt:
    evaluation_id: str
    contract_version: str
    campaign_id: str
    selected_trial_id: str
    protocol_id: str
    protocol_receipt_hash: str
    start_hash: str
    candidate_binding_fingerprint: str
    holdout_authorization_id: str
    holdout_universe_hash: str
    config_hash: str
    result_hash: str
    decision: OSS2FinalHoldoutDecision
    gates: tuple[OSS2FinalHoldoutGate, ...]
    failed_gate_ids: tuple[str, ...]
    net_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    failure_code: str
    started_at: str
    terminal_at: str
    final_holdout_observed: bool
    final_holdout_consumed: bool
    retuning_allowed: bool
    reselection_allowed: bool
    second_attempt_allowed: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        for name, value in (
            ("evaluation_id", self.evaluation_id),
            ("campaign_id", self.campaign_id),
            ("selected_trial_id", self.selected_trial_id),
            ("protocol_id", self.protocol_id),
            ("holdout_authorization_id", self.holdout_authorization_id),
        ):
            _require_id(value, name)
        for name, value in (
            ("protocol_receipt_hash", self.protocol_receipt_hash),
            ("start_hash", self.start_hash),
            ("candidate_binding_fingerprint", self.candidate_binding_fingerprint),
            ("holdout_universe_hash", self.holdout_universe_hash),
            ("config_hash", self.config_hash),
            ("receipt_hash", self.receipt_hash),
        ):
            _require_hash(value, name)
        if self.result_hash:
            _require_hash(self.result_hash, "result_hash")
        if self.contract_version != OSS2H_CONTRACT_VERSION:
            raise OSS2FinalHoldoutEvaluationIntegrityError("noncanonical OSS-2H receipt version")
        _require_aware_iso(self.started_at, "started_at")
        _require_aware_iso(self.terminal_at, "terminal_at")
        if datetime.fromisoformat(self.terminal_at) < datetime.fromisoformat(self.started_at):
            raise OSS2FinalHoldoutEvaluationIntegrityError("terminal_at predates started_at")
        if self.final_holdout_observed is not True or self.final_holdout_consumed is not True:
            raise OSS2FinalHoldoutEvaluationIntegrityError(
                "OSS-2H terminal receipt must reflect consumed/observed holdout"
            )
        if self.retuning_allowed or self.reselection_allowed or self.second_attempt_allowed:
            raise OSS2FinalHoldoutEvaluationIntegrityError(
                "OSS-2H forbids retuning, reselection and second attempts"
            )
        if self.paper_execution_authorized:
            raise OSS2FinalHoldoutEvaluationIntegrityError(
                "OSS-2H may not authorize PAPER execution"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise OSS2FinalHoldoutEvaluationIntegrityError(
                "OSS-2H may not grant capital or LIVE authority"
            )

        if self.failure_code:
            if self.decision is not OSS2FinalHoldoutDecision.FAIL:
                raise OSS2FinalHoldoutEvaluationIntegrityError(
                    "structural evaluation failure must be terminal FAIL"
                )
            if self.result_hash or self.gates or self.failed_gate_ids:
                raise OSS2FinalHoldoutEvaluationIntegrityError(
                    "structural failure may not fabricate result/gate evidence"
                )
            if any(value is not None for value in (self.net_return, self.sharpe, self.max_drawdown)):
                raise OSS2FinalHoldoutEvaluationIntegrityError(
                    "structural failure may not fabricate metric evidence"
                )
        else:
            values = (self.net_return, self.sharpe, self.max_drawdown)
            if any(value is None or not isfinite(value) for value in values):
                raise OSS2FinalHoldoutEvaluationIntegrityError(
                    "successful evaluation requires three finite metrics"
                )
            if self.max_drawdown is None or not 0 <= self.max_drawdown <= 1:
                raise OSS2FinalHoldoutEvaluationIntegrityError("invalid FINAL_HOLDOUT drawdown")
            if len(self.gates) != 3:
                raise OSS2FinalHoldoutEvaluationIntegrityError(
                    "OSS-2H requires exactly three preregistered gates"
                )
            expected_ids = (
                "FINAL_NET_RETURN_MIN",
                "FINAL_SHARPE_MIN",
                "FINAL_DRAWDOWN_MAX",
            )
            if tuple(gate.gate_id for gate in self.gates) != expected_ids:
                raise OSS2FinalHoldoutEvaluationIntegrityError("OSS-2H gate order drifted")
            failed = tuple(gate.gate_id for gate in self.gates if not gate.passed)
            if failed != self.failed_gate_ids:
                raise OSS2FinalHoldoutEvaluationIntegrityError("failed gate list mismatch")
            expected_decision = (
                OSS2FinalHoldoutDecision.PASS
                if not failed
                else OSS2FinalHoldoutDecision.FAIL
            )
            if self.decision is not expected_decision:
                raise OSS2FinalHoldoutEvaluationIntegrityError(
                    "OSS-2H decision must be mechanically derived from gates"
                )
        if self.receipt_hash != _hash(_terminal_payload(self, include_hash=False)):
            raise OSS2FinalHoldoutEvaluationIntegrityError("OSS-2H receipt hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _terminal_payload(self, include_hash=True)


class SQLiteOSS2FinalHoldoutEvaluationRegistry:
    """One-attempt durable OSS-2H evaluator and append-only terminal registry."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Reuse the canonical research holdout-permit table contract.
        SQLiteExperimentRegistry(self.path)
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oss2_final_holdout_evaluation_starts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_id TEXT NOT NULL UNIQUE,
                    campaign_id TEXT NOT NULL UNIQUE,
                    selected_trial_id TEXT NOT NULL,
                    protocol_id TEXT NOT NULL UNIQUE,
                    protocol_receipt_hash TEXT NOT NULL UNIQUE,
                    candidate_binding_fingerprint TEXT NOT NULL UNIQUE,
                    holdout_authorization_id TEXT NOT NULL UNIQUE,
                    holdout_universe_hash TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    start_hash TEXT NOT NULL UNIQUE,
                    start_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS oss2_final_holdout_evaluations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_id TEXT NOT NULL UNIQUE,
                    campaign_id TEXT NOT NULL UNIQUE,
                    protocol_id TEXT NOT NULL UNIQUE,
                    start_hash TEXT NOT NULL UNIQUE,
                    holdout_authorization_id TEXT NOT NULL UNIQUE,
                    decision TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS oss2_final_holdout_starts_no_update
                BEFORE UPDATE ON oss2_final_holdout_evaluation_starts
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-2H start registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss2_final_holdout_starts_no_delete
                BEFORE DELETE ON oss2_final_holdout_evaluation_starts
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-2H start registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss2_final_holdout_evaluations_no_update
                BEFORE UPDATE ON oss2_final_holdout_evaluations
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-2H terminal registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss2_final_holdout_evaluations_no_delete
                BEFORE DELETE ON oss2_final_holdout_evaluations
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-2H terminal registry is append-only');
                END;
                """
            )
        finally:
            conn.close()

    def evaluate_and_record(
        self,
        *,
        evaluation_id: str,
        protocol: OSS2FinalHoldoutProtocolReceipt,
        candidate: OSS2SelectedCandidateBinding,
        holdout: ProtectedOSS2FinalHoldout,
        now: datetime,
    ) -> OSS2FinalHoldoutEvaluationReceipt:
        """Consume FINAL_HOLDOUT exactly once and return a terminal PASS/FAIL receipt."""
        _require_id(evaluation_id, "evaluation_id")
        _require_aware(now, "now")
        _verify_protocol(protocol)
        _verify_candidate_binding(protocol=protocol, candidate=candidate)
        if not isinstance(holdout, ProtectedOSS2FinalHoldout):
            raise TypeError("holdout must be ProtectedOSS2FinalHoldout")

        config = backtest_config_from_oss2_trial(candidate.spec)
        if config.config_hash != candidate.config_hash:
            raise OSS2FinalHoldoutEvaluationIntegrityError("candidate config reconstruction drift")
        permit = HoldoutPermit(
            permit_id=protocol.holdout_authorization_id,
            issued_by=_ISSUED_BY,
            purpose=_FINAL_VALIDATION,
        )
        start = _build_start(
            evaluation_id=evaluation_id,
            protocol=protocol,
            candidate=candidate,
            holdout_universe_hash=holdout.universe_hash,
            config_hash=config.config_hash,
            started_at=now,
        )
        self._consume_and_record_start(permit=permit, start=start)

        try:
            universe = holdout._checkout(
                permit=permit,
                expected_authorization_id=protocol.holdout_authorization_id,
            )
            if universe.universe_hash != start.holdout_universe_hash:
                raise OSS2FinalHoldoutEvaluationIntegrityError(
                    "FINAL_HOLDOUT universe changed after durable consumption"
                )
            result = CrossSectionalBacktestEngine().run(universe=universe, config=config)
            if result.universe_hash != start.holdout_universe_hash:
                raise OSS2FinalHoldoutEvaluationIntegrityError("result universe hash mismatch")
            if result.config_hash != start.config_hash:
                raise OSS2FinalHoldoutEvaluationIntegrityError("result config hash mismatch")
            receipt = _build_metric_receipt(
                start=start,
                protocol=protocol,
                result_hash=result.result_hash,
                net_return=float(result.metrics.net_return),
                sharpe=float(result.metrics.sharpe),
                max_drawdown=float(result.metrics.max_drawdown),
                terminal_at=now,
            )
        except Exception as exc:
            receipt = _build_structural_failure_receipt(
                start=start,
                protocol=protocol,
                failure_code=f"EVALUATION_ERROR:{type(exc).__name__}",
                terminal_at=now,
            )
        return self._record_terminal(receipt)

    def _consume_and_record_start(
        self,
        *,
        permit: HoldoutPermit,
        start: OSS2FinalHoldoutStartReceipt,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT evaluation_id FROM oss2_final_holdout_evaluation_starts "
                "WHERE campaign_id = ? OR holdout_authorization_id = ? OR protocol_id = ?",
                (start.campaign_id, start.holdout_authorization_id, start.protocol_id),
            ).fetchone()
            if prior is not None:
                raise OSS2FinalHoldoutAlreadyConsumed(
                    "OSS-2 FINAL_HOLDOUT campaign already consumed"
                )
            try:
                conn.execute(
                    "INSERT INTO holdout_permits(permit_id, issued_by, purpose, used_at) "
                    "VALUES (?, ?, ?, ?)",
                    (permit.permit_id, permit.issued_by, permit.purpose, start.started_at),
                )
            except sqlite3.IntegrityError as exc:
                raise OSS2FinalHoldoutAlreadyConsumed(permit.permit_id) from exc
            conn.execute(
                """
                INSERT INTO oss2_final_holdout_evaluation_starts(
                    evaluation_id, campaign_id, selected_trial_id, protocol_id,
                    protocol_receipt_hash, candidate_binding_fingerprint,
                    holdout_authorization_id, holdout_universe_hash, config_hash,
                    started_at, start_hash, start_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    start.evaluation_id,
                    start.campaign_id,
                    start.selected_trial_id,
                    start.protocol_id,
                    start.protocol_receipt_hash,
                    start.candidate_binding_fingerprint,
                    start.holdout_authorization_id,
                    start.holdout_universe_hash,
                    start.config_hash,
                    start.started_at,
                    start.start_hash,
                    _canonical_json(start.to_dict()),
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise OSS2FinalHoldoutAlreadyConsumed(
                "OSS-2 FINAL_HOLDOUT start conflicts with durable state"
            ) from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _record_terminal(
        self,
        receipt: OSS2FinalHoldoutEvaluationReceipt,
    ) -> OSS2FinalHoldoutEvaluationReceipt:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM oss2_final_holdout_evaluations WHERE evaluation_id = ?",
                (receipt.evaluation_id,),
            ).fetchone()
            if existing is not None:
                current = _terminal_from_row(existing)
                if current != receipt:
                    raise OSS2FinalHoldoutEvaluationIntegrityError(
                        "terminal FINAL_HOLDOUT result conflict"
                    )
                conn.execute("COMMIT")
                return current
            start = conn.execute(
                "SELECT start_hash FROM oss2_final_holdout_evaluation_starts "
                "WHERE evaluation_id = ?",
                (receipt.evaluation_id,),
            ).fetchone()
            if start is None or str(start["start_hash"]) != receipt.start_hash:
                raise OSS2FinalHoldoutEvaluationIntegrityError(
                    "terminal receipt has no exact durable start"
                )
            conn.execute(
                """
                INSERT INTO oss2_final_holdout_evaluations(
                    evaluation_id, campaign_id, protocol_id, start_hash,
                    holdout_authorization_id, decision, receipt_hash, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.evaluation_id,
                    receipt.campaign_id,
                    receipt.protocol_id,
                    receipt.start_hash,
                    receipt.holdout_authorization_id,
                    receipt.decision.value,
                    receipt.receipt_hash,
                    _canonical_json(receipt.to_dict()),
                ),
            )
            conn.execute("COMMIT")
            return receipt
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def read_oss2_selected_candidate_read_only(
    path: str | Path,
    *,
    protocol: OSS2FinalHoldoutProtocolReceipt,
) -> OSS2SelectedCandidateBinding:
    """Read the exact completed DEVELOPMENT winner with SQLite query_only."""
    _verify_protocol(protocol)
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise OSS2FinalHoldoutEvaluationIntegrityError("research trial ledger does not exist")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='research_trials'"
        ).fetchone()
        if table is None:
            raise OSS2FinalHoldoutEvaluationIntegrityError("research_trials table is missing")
        row = conn.execute(
            "SELECT campaign_id, fingerprint, spec_json, status, result_hash "
            "FROM research_trials WHERE trial_id = ?",
            (protocol.selected_trial_id,),
        ).fetchone()
        if row is None:
            raise OSS2FinalHoldoutEvaluationIntegrityError("selected OSS-2 trial is missing")
        if str(row["campaign_id"]) != protocol.campaign_id:
            raise OSS2FinalHoldoutEvaluationIntegrityError("selected trial campaign mismatch")
        if TrialStatus(str(row["status"])) is not TrialStatus.COMPLETED:
            raise OSS2FinalHoldoutEvaluationGovernanceError(
                "selected OSS-2 trial must be durably COMPLETED"
            )
        spec = _trial_spec_from_json(str(row["spec_json"]))
        if spec.trial_id != protocol.selected_trial_id or spec.campaign_id != protocol.campaign_id:
            raise OSS2FinalHoldoutEvaluationIntegrityError("selected trial spec identity mismatch")
        if spec.phase is not TrialPhase.DEVELOPMENT:
            raise OSS2FinalHoldoutEvaluationGovernanceError(
                "selected OSS-2 trial must remain DEVELOPMENT-only"
            )
        stored_fingerprint = str(row["fingerprint"])
        if stored_fingerprint != spec.fingerprint:
            raise OSS2FinalHoldoutEvaluationIntegrityError("selected trial side-column mismatch")
        result_hash = str(row["result_hash"])
        _require_hash(result_hash, "selected result_hash")
        config = backtest_config_from_oss2_trial(spec)
        return OSS2SelectedCandidateBinding(
            campaign_id=spec.campaign_id,
            trial_id=spec.trial_id,
            trial_fingerprint=spec.fingerprint,
            result_hash=result_hash,
            config_hash=config.config_hash,
            spec=spec,
        )
    finally:
        conn.close()


def read_oss2h_evaluation_read_only(
    path: str | Path,
    *,
    campaign_id: str,
) -> OSS2FinalHoldoutEvaluationReceipt | None:
    """Verify the terminal receipt independently without creating schema."""
    _require_id(campaign_id, "campaign_id")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise OSS2FinalHoldoutEvaluationIntegrityError("OSS-2H durable registry does not exist")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        start_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='oss2_final_holdout_evaluation_starts'"
        ).fetchone()
        terminal_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='oss2_final_holdout_evaluations'"
        ).fetchone()
        if start_table is None or terminal_table is None:
            raise OSS2FinalHoldoutEvaluationIntegrityError("OSS-2H durable tables are missing")
        start = conn.execute(
            "SELECT * FROM oss2_final_holdout_evaluation_starts WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        terminal = conn.execute(
            "SELECT * FROM oss2_final_holdout_evaluations WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if terminal is None:
            if start is not None:
                raise OSS2FinalHoldoutAlreadyConsumed(
                    "FINAL_HOLDOUT authorization was consumed without terminal receipt; no retry allowed"
                )
            return None
        receipt = _terminal_from_row(terminal)
        if start is None:
            raise OSS2FinalHoldoutEvaluationIntegrityError("terminal receipt is missing durable start")
        start_receipt = _start_from_row(start)
        if (
            receipt.start_hash != start_receipt.start_hash
            or receipt.evaluation_id != start_receipt.evaluation_id
            or receipt.protocol_id != start_receipt.protocol_id
            or receipt.holdout_authorization_id != start_receipt.holdout_authorization_id
        ):
            raise OSS2FinalHoldoutEvaluationIntegrityError("OSS-2H start/terminal chain mismatch")
        permit = conn.execute(
            "SELECT issued_by, purpose, used_at FROM holdout_permits WHERE permit_id = ?",
            (receipt.holdout_authorization_id,),
        ).fetchone()
        if (
            permit is None
            or str(permit["issued_by"]) != _ISSUED_BY
            or str(permit["purpose"]) != _FINAL_VALIDATION
            or str(permit["used_at"]) != receipt.started_at
        ):
            raise OSS2FinalHoldoutEvaluationIntegrityError("OSS-2H permit-consumption chain mismatch")
        return receipt
    finally:
        conn.close()


def _verify_protocol(protocol: OSS2FinalHoldoutProtocolReceipt) -> None:
    if not isinstance(protocol, OSS2FinalHoldoutProtocolReceipt):
        raise TypeError("protocol must be OSS2FinalHoldoutProtocolReceipt")
    if protocol.contract_version != OSS2G_CONTRACT_VERSION:
        raise OSS2FinalHoldoutEvaluationGovernanceError("OSS-2H requires canonical OSS-2G")
    policy = canonical_oss2g_policy()
    if protocol.protocol_policy_fingerprint != policy.fingerprint:
        raise OSS2FinalHoldoutEvaluationGovernanceError("OSS-2G policy drifted before consumption")
    if (
        protocol.split_name != _FINAL_HOLDOUT
        or protocol.permit_purpose != _FINAL_VALIDATION
        or protocol.max_evaluations != 1
        or protocol.retuning_allowed
        or protocol.reselection_allowed
        or protocol.second_attempt_allowed
        or protocol.failure_is_terminal is not True
    ):
        raise OSS2FinalHoldoutEvaluationGovernanceError("OSS-2G one-shot contract drifted")
    if protocol.final_holdout_observed or protocol.final_holdout_consumed:
        raise OSS2FinalHoldoutEvaluationGovernanceError(
            "OSS-2H requires an unconsumed/unobserved OSS-2G protocol"
        )
    if protocol.paper_execution_authorized or protocol.capital_authority != "NONE":
        raise OSS2FinalHoldoutEvaluationGovernanceError("OSS-2G input grants forbidden authority")
    if protocol.live_trading != "BLOCKED":
        raise OSS2FinalHoldoutEvaluationGovernanceError("LIVE must remain blocked")


def _verify_candidate_binding(
    *,
    protocol: OSS2FinalHoldoutProtocolReceipt,
    candidate: OSS2SelectedCandidateBinding,
) -> None:
    if not isinstance(candidate, OSS2SelectedCandidateBinding):
        raise TypeError("candidate must be OSS2SelectedCandidateBinding")
    if candidate.campaign_id != protocol.campaign_id:
        raise OSS2FinalHoldoutEvaluationGovernanceError("candidate campaign differs from OSS-2G")
    if candidate.trial_id != protocol.selected_trial_id:
        raise OSS2FinalHoldoutEvaluationGovernanceError("candidate trial differs from OSS-2G")
    backtest_config_from_oss2_trial(candidate.spec)


def _build_start(
    *,
    evaluation_id: str,
    protocol: OSS2FinalHoldoutProtocolReceipt,
    candidate: OSS2SelectedCandidateBinding,
    holdout_universe_hash: str,
    config_hash: str,
    started_at: datetime,
) -> OSS2FinalHoldoutStartReceipt:
    _require_hash(holdout_universe_hash, "holdout_universe_hash")
    payload = {
        "evaluation_id": evaluation_id,
        "contract_version": OSS2H_CONTRACT_VERSION,
        "campaign_id": protocol.campaign_id,
        "selected_trial_id": protocol.selected_trial_id,
        "protocol_id": protocol.protocol_id,
        "protocol_receipt_hash": protocol.receipt_hash,
        "candidate_binding_fingerprint": candidate.fingerprint,
        "holdout_authorization_id": protocol.holdout_authorization_id,
        "holdout_universe_hash": holdout_universe_hash,
        "config_hash": config_hash,
        "started_at": started_at.isoformat(),
    }
    return OSS2FinalHoldoutStartReceipt(
        evaluation_id=evaluation_id,
        contract_version=OSS2H_CONTRACT_VERSION,
        campaign_id=protocol.campaign_id,
        selected_trial_id=protocol.selected_trial_id,
        protocol_id=protocol.protocol_id,
        protocol_receipt_hash=protocol.receipt_hash,
        candidate_binding_fingerprint=candidate.fingerprint,
        holdout_authorization_id=protocol.holdout_authorization_id,
        holdout_universe_hash=holdout_universe_hash,
        config_hash=config_hash,
        started_at=started_at.isoformat(),
        start_hash=_hash(payload),
    )


def _build_metric_receipt(
    *,
    start: OSS2FinalHoldoutStartReceipt,
    protocol: OSS2FinalHoldoutProtocolReceipt,
    result_hash: str,
    net_return: float,
    sharpe: float,
    max_drawdown: float,
    terminal_at: datetime,
) -> OSS2FinalHoldoutEvaluationReceipt:
    _require_hash(result_hash, "result_hash")
    for value in (net_return, sharpe, max_drawdown):
        if not isfinite(value):
            raise OSS2FinalHoldoutEvaluationIntegrityError("FINAL_HOLDOUT metric is non-finite")
    if not 0 <= max_drawdown <= 1:
        raise OSS2FinalHoldoutEvaluationIntegrityError("FINAL_HOLDOUT drawdown outside [0,1]")
    gates = (
        OSS2FinalHoldoutGate(
            "FINAL_NET_RETURN_MIN",
            net_return >= protocol.min_net_return,
            net_return,
            ">=",
            protocol.min_net_return,
        ),
        OSS2FinalHoldoutGate(
            "FINAL_SHARPE_MIN",
            sharpe >= protocol.min_sharpe,
            sharpe,
            ">=",
            protocol.min_sharpe,
        ),
        OSS2FinalHoldoutGate(
            "FINAL_DRAWDOWN_MAX",
            max_drawdown <= protocol.max_drawdown,
            max_drawdown,
            "<=",
            protocol.max_drawdown,
        ),
    )
    failed = tuple(gate.gate_id for gate in gates if not gate.passed)
    decision = OSS2FinalHoldoutDecision.PASS if not failed else OSS2FinalHoldoutDecision.FAIL
    return _make_terminal(
        start=start,
        protocol=protocol,
        result_hash=result_hash,
        decision=decision,
        gates=gates,
        failed_gate_ids=failed,
        net_return=net_return,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        failure_code="",
        terminal_at=terminal_at,
    )


def _build_structural_failure_receipt(
    *,
    start: OSS2FinalHoldoutStartReceipt,
    protocol: OSS2FinalHoldoutProtocolReceipt,
    failure_code: str,
    terminal_at: datetime,
) -> OSS2FinalHoldoutEvaluationReceipt:
    if not failure_code.strip():
        raise ValueError("failure_code is required")
    return _make_terminal(
        start=start,
        protocol=protocol,
        result_hash="",
        decision=OSS2FinalHoldoutDecision.FAIL,
        gates=(),
        failed_gate_ids=(),
        net_return=None,
        sharpe=None,
        max_drawdown=None,
        failure_code=failure_code,
        terminal_at=terminal_at,
    )


def _make_terminal(
    *,
    start: OSS2FinalHoldoutStartReceipt,
    protocol: OSS2FinalHoldoutProtocolReceipt,
    result_hash: str,
    decision: OSS2FinalHoldoutDecision,
    gates: tuple[OSS2FinalHoldoutGate, ...],
    failed_gate_ids: tuple[str, ...],
    net_return: float | None,
    sharpe: float | None,
    max_drawdown: float | None,
    failure_code: str,
    terminal_at: datetime,
) -> OSS2FinalHoldoutEvaluationReceipt:
    _require_aware(terminal_at, "terminal_at")
    base: dict[str, object] = {
        "evaluation_id": start.evaluation_id,
        "contract_version": OSS2H_CONTRACT_VERSION,
        "campaign_id": start.campaign_id,
        "selected_trial_id": start.selected_trial_id,
        "protocol_id": start.protocol_id,
        "protocol_receipt_hash": start.protocol_receipt_hash,
        "start_hash": start.start_hash,
        "candidate_binding_fingerprint": start.candidate_binding_fingerprint,
        "holdout_authorization_id": start.holdout_authorization_id,
        "holdout_universe_hash": start.holdout_universe_hash,
        "config_hash": start.config_hash,
        "result_hash": result_hash,
        "decision": decision.value,
        "gates": [_gate_payload(gate) for gate in gates],
        "failed_gate_ids": list(failed_gate_ids),
        "net_return": net_return,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "failure_code": failure_code,
        "started_at": start.started_at,
        "terminal_at": terminal_at.isoformat(),
        "final_holdout_observed": True,
        "final_holdout_consumed": True,
        "retuning_allowed": False,
        "reselection_allowed": False,
        "second_attempt_allowed": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    receipt = OSS2FinalHoldoutEvaluationReceipt(
        evaluation_id=start.evaluation_id,
        contract_version=OSS2H_CONTRACT_VERSION,
        campaign_id=start.campaign_id,
        selected_trial_id=start.selected_trial_id,
        protocol_id=start.protocol_id,
        protocol_receipt_hash=start.protocol_receipt_hash,
        start_hash=start.start_hash,
        candidate_binding_fingerprint=start.candidate_binding_fingerprint,
        holdout_authorization_id=start.holdout_authorization_id,
        holdout_universe_hash=start.holdout_universe_hash,
        config_hash=start.config_hash,
        result_hash=result_hash,
        decision=decision,
        gates=gates,
        failed_gate_ids=failed_gate_ids,
        net_return=net_return,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        failure_code=failure_code,
        started_at=start.started_at,
        terminal_at=terminal_at.isoformat(),
        final_holdout_observed=True,
        final_holdout_consumed=True,
        retuning_allowed=False,
        reselection_allowed=False,
        second_attempt_allowed=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
        receipt_hash=_hash(base),
    )
    if protocol.receipt_hash != receipt.protocol_receipt_hash:
        raise OSS2FinalHoldoutEvaluationIntegrityError("terminal protocol binding mismatch")
    return receipt


def _trial_spec_from_json(raw: str) -> TrialSpec:
    try:
        value = json.loads(raw)
        return TrialSpec(
            trial_id=str(value["trial_id"]),
            campaign_id=str(value["campaign_id"]),
            hypothesis_id=str(value["hypothesis_id"]),
            strategy_id=str(value["strategy_id"]),
            strategy_version=str(value["strategy_version"]),
            dataset_hash=str(value["dataset_hash"]),
            split_name=str(value["split_name"]),
            phase=TrialPhase(str(value["phase"])),
            parameters=value["parameters"],
            code_version=str(value["code_version"]),
            holdout_authorization_id=str(value.get("holdout_authorization_id", "")),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OSS2FinalHoldoutEvaluationIntegrityError("invalid selected trial JSON") from exc


def _start_from_row(row: sqlite3.Row) -> OSS2FinalHoldoutStartReceipt:
    try:
        payload = json.loads(str(row["start_json"]))
        receipt = OSS2FinalHoldoutStartReceipt(
            evaluation_id=str(payload["evaluation_id"]),
            contract_version=str(payload["contract_version"]),
            campaign_id=str(payload["campaign_id"]),
            selected_trial_id=str(payload["selected_trial_id"]),
            protocol_id=str(payload["protocol_id"]),
            protocol_receipt_hash=str(payload["protocol_receipt_hash"]),
            candidate_binding_fingerprint=str(payload["candidate_binding_fingerprint"]),
            holdout_authorization_id=str(payload["holdout_authorization_id"]),
            holdout_universe_hash=str(payload["holdout_universe_hash"]),
            config_hash=str(payload["config_hash"]),
            started_at=str(payload["started_at"]),
            start_hash=str(payload["start_hash"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OSS2FinalHoldoutEvaluationIntegrityError("invalid OSS-2H start receipt") from exc
    side = {
        "evaluation_id": receipt.evaluation_id,
        "campaign_id": receipt.campaign_id,
        "selected_trial_id": receipt.selected_trial_id,
        "protocol_id": receipt.protocol_id,
        "protocol_receipt_hash": receipt.protocol_receipt_hash,
        "candidate_binding_fingerprint": receipt.candidate_binding_fingerprint,
        "holdout_authorization_id": receipt.holdout_authorization_id,
        "holdout_universe_hash": receipt.holdout_universe_hash,
        "config_hash": receipt.config_hash,
        "started_at": receipt.started_at,
        "start_hash": receipt.start_hash,
    }
    _verify_side_columns(row, side, "OSS-2H start")
    return receipt


def _terminal_from_row(row: sqlite3.Row) -> OSS2FinalHoldoutEvaluationReceipt:
    try:
        payload = json.loads(str(row["receipt_json"]))
        gates = tuple(
            OSS2FinalHoldoutGate(
                gate_id=str(item["gate_id"]),
                passed=item["passed"],
                observed=float(item["observed"]),
                comparison=str(item["comparison"]),
                threshold=float(item["threshold"]),
            )
            for item in payload["gates"]
        )
        receipt = OSS2FinalHoldoutEvaluationReceipt(
            evaluation_id=str(payload["evaluation_id"]),
            contract_version=str(payload["contract_version"]),
            campaign_id=str(payload["campaign_id"]),
            selected_trial_id=str(payload["selected_trial_id"]),
            protocol_id=str(payload["protocol_id"]),
            protocol_receipt_hash=str(payload["protocol_receipt_hash"]),
            start_hash=str(payload["start_hash"]),
            candidate_binding_fingerprint=str(payload["candidate_binding_fingerprint"]),
            holdout_authorization_id=str(payload["holdout_authorization_id"]),
            holdout_universe_hash=str(payload["holdout_universe_hash"]),
            config_hash=str(payload["config_hash"]),
            result_hash=str(payload["result_hash"]),
            decision=OSS2FinalHoldoutDecision(str(payload["decision"])),
            gates=gates,
            failed_gate_ids=tuple(str(value) for value in payload["failed_gate_ids"]),
            net_return=None if payload["net_return"] is None else float(payload["net_return"]),
            sharpe=None if payload["sharpe"] is None else float(payload["sharpe"]),
            max_drawdown=(
                None if payload["max_drawdown"] is None else float(payload["max_drawdown"])
            ),
            failure_code=str(payload["failure_code"]),
            started_at=str(payload["started_at"]),
            terminal_at=str(payload["terminal_at"]),
            final_holdout_observed=payload["final_holdout_observed"],
            final_holdout_consumed=payload["final_holdout_consumed"],
            retuning_allowed=payload["retuning_allowed"],
            reselection_allowed=payload["reselection_allowed"],
            second_attempt_allowed=payload["second_attempt_allowed"],
            paper_execution_authorized=payload["paper_execution_authorized"],
            capital_authority=str(payload["capital_authority"]),
            live_trading=str(payload["live_trading"]),
            receipt_hash=str(payload["receipt_hash"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OSS2FinalHoldoutEvaluationIntegrityError("invalid OSS-2H terminal receipt") from exc
    side = {
        "evaluation_id": receipt.evaluation_id,
        "campaign_id": receipt.campaign_id,
        "protocol_id": receipt.protocol_id,
        "start_hash": receipt.start_hash,
        "holdout_authorization_id": receipt.holdout_authorization_id,
        "decision": receipt.decision.value,
        "receipt_hash": receipt.receipt_hash,
    }
    _verify_side_columns(row, side, "OSS-2H terminal")
    return receipt


def _verify_side_columns(row: sqlite3.Row, expected: dict[str, str], label: str) -> None:
    for key, value in expected.items():
        try:
            stored = str(row[key])
        except (IndexError, KeyError) as exc:
            raise OSS2FinalHoldoutEvaluationIntegrityError(
                f"{label} missing side-column: {key}"
            ) from exc
        if stored != value:
            raise OSS2FinalHoldoutEvaluationIntegrityError(
                f"{label} side-column mismatch: {key}"
            )


def _start_payload(
    receipt: OSS2FinalHoldoutStartReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "evaluation_id": receipt.evaluation_id,
        "contract_version": receipt.contract_version,
        "campaign_id": receipt.campaign_id,
        "selected_trial_id": receipt.selected_trial_id,
        "protocol_id": receipt.protocol_id,
        "protocol_receipt_hash": receipt.protocol_receipt_hash,
        "candidate_binding_fingerprint": receipt.candidate_binding_fingerprint,
        "holdout_authorization_id": receipt.holdout_authorization_id,
        "holdout_universe_hash": receipt.holdout_universe_hash,
        "config_hash": receipt.config_hash,
        "started_at": receipt.started_at,
    }
    if include_hash:
        payload["start_hash"] = receipt.start_hash
    return payload


def _terminal_payload(
    receipt: OSS2FinalHoldoutEvaluationReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "evaluation_id": receipt.evaluation_id,
        "contract_version": receipt.contract_version,
        "campaign_id": receipt.campaign_id,
        "selected_trial_id": receipt.selected_trial_id,
        "protocol_id": receipt.protocol_id,
        "protocol_receipt_hash": receipt.protocol_receipt_hash,
        "start_hash": receipt.start_hash,
        "candidate_binding_fingerprint": receipt.candidate_binding_fingerprint,
        "holdout_authorization_id": receipt.holdout_authorization_id,
        "holdout_universe_hash": receipt.holdout_universe_hash,
        "config_hash": receipt.config_hash,
        "result_hash": receipt.result_hash,
        "decision": receipt.decision.value,
        "gates": [_gate_payload(gate) for gate in receipt.gates],
        "failed_gate_ids": list(receipt.failed_gate_ids),
        "net_return": receipt.net_return,
        "sharpe": receipt.sharpe,
        "max_drawdown": receipt.max_drawdown,
        "failure_code": receipt.failure_code,
        "started_at": receipt.started_at,
        "terminal_at": receipt.terminal_at,
        "final_holdout_observed": receipt.final_holdout_observed,
        "final_holdout_consumed": receipt.final_holdout_consumed,
        "retuning_allowed": receipt.retuning_allowed,
        "reselection_allowed": receipt.reselection_allowed,
        "second_attempt_allowed": receipt.second_attempt_allowed,
        "paper_execution_authorized": receipt.paper_execution_authorized,
        "capital_authority": receipt.capital_authority,
        "live_trading": receipt.live_trading,
    }
    if include_hash:
        payload["receipt_hash"] = receipt.receipt_hash
    return payload


def _gate_payload(gate: OSS2FinalHoldoutGate) -> dict[str, object]:
    return {
        "gate_id": gate.gate_id,
        "passed": gate.passed,
        "observed": gate.observed,
        "comparison": gate.comparison,
        "threshold": gate.threshold,
    }


def _require_id(value: str, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{name} must be a canonical id")


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase sha256 hex")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_aware_iso(value: str, name: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise OSS2FinalHoldoutEvaluationIntegrityError(f"invalid {name}") from exc
    _require_aware(parsed, name)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()
