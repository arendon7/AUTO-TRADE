"""OSS-2G preregistered single-use FINAL_HOLDOUT protocol.

This module freezes *how* an OSS-2 candidate may later be judged, but it does
not read, accept, checkout, inspect or evaluate FINAL_HOLDOUT material. The
only scientific input is an already durable OSS-2F freeze receipt whose
mechanical decision is HOLDOUT_ELIGIBLE.

OSS-2G deliberately grants no holdout checkout permit object, broker, network,
OMS, Safety, capital, OrderIntent, PAPER-execution or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
import sqlite3

from .oss2_holdout_freeze import (
    OSS2F_CONTRACT_VERSION,
    OSS2HoldoutFreezeReceipt,
    OSS2HoldoutFreezeState,
)


OSS2G_CONTRACT_VERSION = "OSS2G_FINAL_HOLDOUT_PROTOCOL_V1"
_FINAL_HOLDOUT_SPLIT_NAME = "FINAL_HOLDOUT"
_FINAL_VALIDATION_PURPOSE = "final_validation"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class OSS2FinalHoldoutProtocolError(RuntimeError):
    pass


class OSS2FinalHoldoutProtocolGovernanceError(OSS2FinalHoldoutProtocolError):
    pass


class OSS2FinalHoldoutProtocolIntegrityError(OSS2FinalHoldoutProtocolError):
    pass


class OSS2FinalHoldoutProtocolConflict(OSS2FinalHoldoutProtocolError):
    pass


@dataclass(frozen=True, slots=True)
class OSS2FinalHoldoutProtocolPolicy:
    """Frozen ex-ante decision rule for the one future FINAL_HOLDOUT run."""

    min_net_return: float = 0.0
    min_sharpe: float = 0.0
    max_drawdown: float = 0.35
    max_evaluations: int = 1
    retuning_allowed: bool = False
    reselection_allowed: bool = False
    second_attempt_allowed: bool = False
    failure_is_terminal: bool = True
    split_name: str = _FINAL_HOLDOUT_SPLIT_NAME
    permit_purpose: str = _FINAL_VALIDATION_PURPOSE

    def __post_init__(self) -> None:
        for name, value in (
            ("min_net_return", self.min_net_return),
            ("min_sharpe", self.min_sharpe),
            ("max_drawdown", self.max_drawdown),
        ):
            if not isfinite(value):
                raise ValueError(f"OSS-2G {name} must be finite")
        if not 0 <= self.max_drawdown <= 1:
            raise ValueError("OSS-2G max_drawdown must be in [0,1]")
        if self.max_evaluations != 1:
            raise ValueError("OSS-2G permits exactly one FINAL_HOLDOUT evaluation")
        if self.retuning_allowed or self.reselection_allowed or self.second_attempt_allowed:
            raise ValueError("OSS-2G forbids retuning, reselection and second attempts")
        if self.failure_is_terminal is not True:
            raise ValueError("OSS-2G FINAL_HOLDOUT failure must be terminal")
        if self.split_name != _FINAL_HOLDOUT_SPLIT_NAME:
            raise ValueError("OSS-2G split_name is frozen to FINAL_HOLDOUT")
        if self.permit_purpose != _FINAL_VALIDATION_PURPOSE:
            raise ValueError("OSS-2G permit purpose is frozen to final_validation")

    @property
    def fingerprint(self) -> str:
        return _hash(_policy_payload(self))


@dataclass(frozen=True, slots=True)
class OSS2FinalHoldoutProtocolReceipt:
    protocol_id: str
    contract_version: str
    campaign_id: str
    selected_trial_id: str
    oss2f_contract_version: str
    freeze_receipt_id: str
    freeze_receipt_hash: str
    candidate_freeze_fingerprint: str
    oss2e_policy_fingerprint: str
    protocol_policy_fingerprint: str
    holdout_authorization_id: str
    split_name: str
    permit_purpose: str
    min_net_return: float
    min_sharpe: float
    max_drawdown: float
    max_evaluations: int
    retuning_allowed: bool
    reselection_allowed: bool
    second_attempt_allowed: bool
    failure_is_terminal: bool
    final_holdout_observed: bool
    final_holdout_consumed: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        for name, value in (
            ("protocol_id", self.protocol_id),
            ("campaign_id", self.campaign_id),
            ("selected_trial_id", self.selected_trial_id),
            ("freeze_receipt_id", self.freeze_receipt_id),
            ("holdout_authorization_id", self.holdout_authorization_id),
        ):
            _require_id(value, name)
        for name, value in (
            ("freeze_receipt_hash", self.freeze_receipt_hash),
            ("candidate_freeze_fingerprint", self.candidate_freeze_fingerprint),
            ("oss2e_policy_fingerprint", self.oss2e_policy_fingerprint),
            ("protocol_policy_fingerprint", self.protocol_policy_fingerprint),
            ("receipt_hash", self.receipt_hash),
        ):
            _require_hash(value, name)
        if self.contract_version != OSS2G_CONTRACT_VERSION:
            raise OSS2FinalHoldoutProtocolIntegrityError("noncanonical OSS-2G contract version")
        if self.oss2f_contract_version != OSS2F_CONTRACT_VERSION:
            raise OSS2FinalHoldoutProtocolIntegrityError("OSS-2G requires canonical OSS-2F input")

        policy = canonical_oss2g_policy()
        if self.protocol_policy_fingerprint != policy.fingerprint:
            raise OSS2FinalHoldoutProtocolIntegrityError("OSS-2G policy fingerprint mismatch")
        if (
            self.split_name != policy.split_name
            or self.permit_purpose != policy.permit_purpose
            or self.min_net_return != policy.min_net_return
            or self.min_sharpe != policy.min_sharpe
            or self.max_drawdown != policy.max_drawdown
            or self.max_evaluations != policy.max_evaluations
            or self.retuning_allowed is not policy.retuning_allowed
            or self.reselection_allowed is not policy.reselection_allowed
            or self.second_attempt_allowed is not policy.second_attempt_allowed
            or self.failure_is_terminal is not policy.failure_is_terminal
        ):
            raise OSS2FinalHoldoutProtocolIntegrityError("OSS-2G receipt policy fields drifted")

        expected_authorization_id = _authorization_id(
            protocol_id=self.protocol_id,
            campaign_id=self.campaign_id,
            selected_trial_id=self.selected_trial_id,
            freeze_receipt_hash=self.freeze_receipt_hash,
            candidate_freeze_fingerprint=self.candidate_freeze_fingerprint,
            policy_fingerprint=self.protocol_policy_fingerprint,
        )
        if self.holdout_authorization_id != expected_authorization_id:
            raise OSS2FinalHoldoutProtocolIntegrityError(
                "OSS-2G holdout authorization identity mismatch"
            )
        if self.final_holdout_observed is not False or self.final_holdout_consumed is not False:
            raise OSS2FinalHoldoutProtocolIntegrityError(
                "OSS-2G may not observe or consume FINAL_HOLDOUT"
            )
        if self.paper_execution_authorized is not False:
            raise OSS2FinalHoldoutProtocolIntegrityError(
                "OSS-2G may not authorize PAPER execution"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise OSS2FinalHoldoutProtocolIntegrityError(
                "OSS-2G may not grant capital or LIVE authority"
            )
        if self.receipt_hash != _hash(_receipt_payload(self, include_hash=False)):
            raise OSS2FinalHoldoutProtocolIntegrityError("OSS-2G receipt hash mismatch")

    @property
    def gate_specification(self) -> tuple[tuple[str, str, float], ...]:
        return (
            ("FINAL_NET_RETURN_MIN", ">=", self.min_net_return),
            ("FINAL_SHARPE_MIN", ">=", self.min_sharpe),
            ("FINAL_DRAWDOWN_MAX", "<=", self.max_drawdown),
        )

    def to_dict(self) -> dict[str, object]:
        return _receipt_payload(self, include_hash=True)


class SQLiteOSS2FinalHoldoutProtocolRegistry:
    """Append-only OSS-2G registry: exactly one protocol per OSS-2 campaign."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oss2_final_holdout_protocols (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    protocol_id TEXT NOT NULL UNIQUE,
                    campaign_id TEXT NOT NULL UNIQUE,
                    selected_trial_id TEXT NOT NULL,
                    freeze_receipt_id TEXT NOT NULL UNIQUE,
                    freeze_receipt_hash TEXT NOT NULL UNIQUE,
                    candidate_freeze_fingerprint TEXT NOT NULL UNIQUE,
                    protocol_policy_fingerprint TEXT NOT NULL,
                    holdout_authorization_id TEXT NOT NULL UNIQUE,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS oss2_final_holdout_protocols_no_update
                BEFORE UPDATE ON oss2_final_holdout_protocols
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-2G registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss2_final_holdout_protocols_no_delete
                BEFORE DELETE ON oss2_final_holdout_protocols
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-2G registry is append-only');
                END;
                """
            )
        finally:
            conn.close()

    def preregister_and_record(
        self,
        *,
        protocol_id: str,
        freeze: OSS2HoldoutFreezeReceipt,
    ) -> OSS2FinalHoldoutProtocolReceipt:
        _require_id(protocol_id, "protocol_id")
        _verify_eligible_freeze(freeze)
        candidate = _build_receipt(protocol_id=protocol_id, freeze=freeze)

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_id = conn.execute(
                "SELECT * FROM oss2_final_holdout_protocols WHERE protocol_id = ?",
                (protocol_id,),
            ).fetchone()
            if existing_id is not None and str(existing_id["campaign_id"]) != candidate.campaign_id:
                raise OSS2FinalHoldoutProtocolConflict(
                    "OSS-2G protocol_id is already bound to another campaign"
                )

            existing_row = conn.execute(
                "SELECT * FROM oss2_final_holdout_protocols WHERE campaign_id = ?",
                (candidate.campaign_id,),
            ).fetchone()
            if existing_row is not None:
                existing = _receipt_from_row(existing_row)
                if existing != candidate:
                    raise OSS2FinalHoldoutProtocolConflict(
                        "OSS-2G campaign already has a different frozen protocol"
                    )
                conn.execute("COMMIT")
                return existing

            conn.execute(
                """
                INSERT INTO oss2_final_holdout_protocols(
                    protocol_id, campaign_id, selected_trial_id,
                    freeze_receipt_id, freeze_receipt_hash,
                    candidate_freeze_fingerprint, protocol_policy_fingerprint,
                    holdout_authorization_id, receipt_hash, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.protocol_id,
                    candidate.campaign_id,
                    candidate.selected_trial_id,
                    candidate.freeze_receipt_id,
                    candidate.freeze_receipt_hash,
                    candidate.candidate_freeze_fingerprint,
                    candidate.protocol_policy_fingerprint,
                    candidate.holdout_authorization_id,
                    candidate.receipt_hash,
                    _canonical_json(candidate.to_dict()),
                ),
            )
            conn.execute("COMMIT")
            return candidate
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise OSS2FinalHoldoutProtocolConflict(
                "OSS-2G durable identity conflicts with an existing protocol"
            ) from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_for_campaign(self, campaign_id: str) -> OSS2FinalHoldoutProtocolReceipt | None:
        _require_id(campaign_id, "campaign_id")
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM oss2_final_holdout_protocols WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            return _receipt_from_row(row) if row is not None else None
        finally:
            conn.close()


def read_oss2g_protocol_read_only(
    path: str | Path,
    *,
    campaign_id: str,
) -> OSS2FinalHoldoutProtocolReceipt | None:
    """Independently verify a preregistered protocol without mutating schema."""
    _require_id(campaign_id, "campaign_id")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise OSS2FinalHoldoutProtocolIntegrityError("OSS-2G durable registry does not exist")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='oss2_final_holdout_protocols'"
        ).fetchone()
        if table is None:
            raise OSS2FinalHoldoutProtocolIntegrityError("OSS-2G durable table is missing")
        row = conn.execute(
            "SELECT * FROM oss2_final_holdout_protocols WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        return _receipt_from_row(row) if row is not None else None
    finally:
        conn.close()


def canonical_oss2g_policy() -> OSS2FinalHoldoutProtocolPolicy:
    """Return the frozen policy; callers cannot tune it to observed holdout data."""
    return OSS2FinalHoldoutProtocolPolicy()


def _verify_eligible_freeze(freeze: OSS2HoldoutFreezeReceipt) -> None:
    if freeze.contract_version != OSS2F_CONTRACT_VERSION:
        raise OSS2FinalHoldoutProtocolGovernanceError("OSS-2G requires canonical OSS-2F receipt")
    if freeze.decision is not OSS2HoldoutFreezeState.HOLDOUT_ELIGIBLE:
        raise OSS2FinalHoldoutProtocolGovernanceError(
            "OSS-2G protocol requires an OSS-2F HOLDOUT_ELIGIBLE freeze"
        )
    if freeze.failed_gate_ids:
        raise OSS2FinalHoldoutProtocolGovernanceError("eligible OSS-2F freeze may not have failed gates")
    if freeze.final_holdout_observed is not False:
        raise OSS2FinalHoldoutProtocolGovernanceError(
            "OSS-2G requires FINAL_HOLDOUT to remain unobserved"
        )
    if freeze.paper_execution_authorized is not False:
        raise OSS2FinalHoldoutProtocolGovernanceError("OSS-2F input may not authorize PAPER execution")
    if freeze.capital_authority != "NONE" or freeze.live_trading != "BLOCKED":
        raise OSS2FinalHoldoutProtocolGovernanceError(
            "OSS-2F input may not grant capital or LIVE authority"
        )


def _build_receipt(
    *,
    protocol_id: str,
    freeze: OSS2HoldoutFreezeReceipt,
) -> OSS2FinalHoldoutProtocolReceipt:
    policy = canonical_oss2g_policy()
    authorization_id = _authorization_id(
        protocol_id=protocol_id,
        campaign_id=freeze.campaign_id,
        selected_trial_id=freeze.selected_trial_id,
        freeze_receipt_hash=freeze.receipt_hash,
        candidate_freeze_fingerprint=freeze.candidate_freeze_fingerprint,
        policy_fingerprint=policy.fingerprint,
    )
    payload: dict[str, object] = {
        "protocol_id": protocol_id,
        "contract_version": OSS2G_CONTRACT_VERSION,
        "campaign_id": freeze.campaign_id,
        "selected_trial_id": freeze.selected_trial_id,
        "oss2f_contract_version": freeze.contract_version,
        "freeze_receipt_id": freeze.receipt_id,
        "freeze_receipt_hash": freeze.receipt_hash,
        "candidate_freeze_fingerprint": freeze.candidate_freeze_fingerprint,
        "oss2e_policy_fingerprint": freeze.oss2e_policy_fingerprint,
        "protocol_policy_fingerprint": policy.fingerprint,
        "holdout_authorization_id": authorization_id,
        "split_name": policy.split_name,
        "permit_purpose": policy.permit_purpose,
        "min_net_return": policy.min_net_return,
        "min_sharpe": policy.min_sharpe,
        "max_drawdown": policy.max_drawdown,
        "max_evaluations": policy.max_evaluations,
        "retuning_allowed": policy.retuning_allowed,
        "reselection_allowed": policy.reselection_allowed,
        "second_attempt_allowed": policy.second_attempt_allowed,
        "failure_is_terminal": policy.failure_is_terminal,
        "final_holdout_observed": False,
        "final_holdout_consumed": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS2FinalHoldoutProtocolReceipt(
        protocol_id=protocol_id,
        contract_version=OSS2G_CONTRACT_VERSION,
        campaign_id=freeze.campaign_id,
        selected_trial_id=freeze.selected_trial_id,
        oss2f_contract_version=freeze.contract_version,
        freeze_receipt_id=freeze.receipt_id,
        freeze_receipt_hash=freeze.receipt_hash,
        candidate_freeze_fingerprint=freeze.candidate_freeze_fingerprint,
        oss2e_policy_fingerprint=freeze.oss2e_policy_fingerprint,
        protocol_policy_fingerprint=policy.fingerprint,
        holdout_authorization_id=authorization_id,
        split_name=policy.split_name,
        permit_purpose=policy.permit_purpose,
        min_net_return=policy.min_net_return,
        min_sharpe=policy.min_sharpe,
        max_drawdown=policy.max_drawdown,
        max_evaluations=policy.max_evaluations,
        retuning_allowed=policy.retuning_allowed,
        reselection_allowed=policy.reselection_allowed,
        second_attempt_allowed=policy.second_attempt_allowed,
        failure_is_terminal=policy.failure_is_terminal,
        final_holdout_observed=False,
        final_holdout_consumed=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
        receipt_hash=_hash(payload),
    )


def _receipt_from_row(row: sqlite3.Row) -> OSS2FinalHoldoutProtocolReceipt:
    try:
        payload = json.loads(str(row["receipt_json"]))
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise OSS2FinalHoldoutProtocolIntegrityError("invalid OSS-2G durable receipt JSON") from exc
    if not isinstance(payload, dict):
        raise OSS2FinalHoldoutProtocolIntegrityError("OSS-2G durable receipt must be an object")
    try:
        receipt = OSS2FinalHoldoutProtocolReceipt(
            protocol_id=str(payload["protocol_id"]),
            contract_version=str(payload["contract_version"]),
            campaign_id=str(payload["campaign_id"]),
            selected_trial_id=str(payload["selected_trial_id"]),
            oss2f_contract_version=str(payload["oss2f_contract_version"]),
            freeze_receipt_id=str(payload["freeze_receipt_id"]),
            freeze_receipt_hash=str(payload["freeze_receipt_hash"]),
            candidate_freeze_fingerprint=str(payload["candidate_freeze_fingerprint"]),
            oss2e_policy_fingerprint=str(payload["oss2e_policy_fingerprint"]),
            protocol_policy_fingerprint=str(payload["protocol_policy_fingerprint"]),
            holdout_authorization_id=str(payload["holdout_authorization_id"]),
            split_name=str(payload["split_name"]),
            permit_purpose=str(payload["permit_purpose"]),
            min_net_return=float(payload["min_net_return"]),
            min_sharpe=float(payload["min_sharpe"]),
            max_drawdown=float(payload["max_drawdown"]),
            max_evaluations=int(payload["max_evaluations"]),
            retuning_allowed=payload["retuning_allowed"],
            reselection_allowed=payload["reselection_allowed"],
            second_attempt_allowed=payload["second_attempt_allowed"],
            failure_is_terminal=payload["failure_is_terminal"],
            final_holdout_observed=payload["final_holdout_observed"],
            final_holdout_consumed=payload["final_holdout_consumed"],
            paper_execution_authorized=payload["paper_execution_authorized"],
            capital_authority=str(payload["capital_authority"]),
            live_trading=str(payload["live_trading"]),
            receipt_hash=str(payload["receipt_hash"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OSS2FinalHoldoutProtocolIntegrityError("invalid OSS-2G durable receipt fields") from exc

    side_columns = {
        "protocol_id": receipt.protocol_id,
        "campaign_id": receipt.campaign_id,
        "selected_trial_id": receipt.selected_trial_id,
        "freeze_receipt_id": receipt.freeze_receipt_id,
        "freeze_receipt_hash": receipt.freeze_receipt_hash,
        "candidate_freeze_fingerprint": receipt.candidate_freeze_fingerprint,
        "protocol_policy_fingerprint": receipt.protocol_policy_fingerprint,
        "holdout_authorization_id": receipt.holdout_authorization_id,
        "receipt_hash": receipt.receipt_hash,
    }
    for key, value in side_columns.items():
        try:
            stored = str(row[key])
        except (IndexError, KeyError) as exc:
            raise OSS2FinalHoldoutProtocolIntegrityError(
                f"OSS-2G missing side-column: {key}"
            ) from exc
        if stored != value:
            raise OSS2FinalHoldoutProtocolIntegrityError(f"OSS-2G side-column mismatch: {key}")
    return receipt


def _authorization_id(
    *,
    protocol_id: str,
    campaign_id: str,
    selected_trial_id: str,
    freeze_receipt_hash: str,
    candidate_freeze_fingerprint: str,
    policy_fingerprint: str,
) -> str:
    digest = _hash(
        {
            "protocol_id": protocol_id,
            "campaign_id": campaign_id,
            "selected_trial_id": selected_trial_id,
            "freeze_receipt_hash": freeze_receipt_hash,
            "candidate_freeze_fingerprint": candidate_freeze_fingerprint,
            "policy_fingerprint": policy_fingerprint,
            "purpose": _FINAL_VALIDATION_PURPOSE,
        }
    )
    return f"oss2g:{digest[:24]}"


def _policy_payload(policy: OSS2FinalHoldoutProtocolPolicy) -> dict[str, object]:
    return {
        "min_net_return": policy.min_net_return,
        "min_sharpe": policy.min_sharpe,
        "max_drawdown": policy.max_drawdown,
        "max_evaluations": policy.max_evaluations,
        "retuning_allowed": policy.retuning_allowed,
        "reselection_allowed": policy.reselection_allowed,
        "second_attempt_allowed": policy.second_attempt_allowed,
        "failure_is_terminal": policy.failure_is_terminal,
        "split_name": policy.split_name,
        "permit_purpose": policy.permit_purpose,
        "gate_specification": [
            ["FINAL_NET_RETURN_MIN", ">=", policy.min_net_return],
            ["FINAL_SHARPE_MIN", ">=", policy.min_sharpe],
            ["FINAL_DRAWDOWN_MAX", "<=", policy.max_drawdown],
        ],
    }


def _receipt_payload(
    receipt: OSS2FinalHoldoutProtocolReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol_id": receipt.protocol_id,
        "contract_version": receipt.contract_version,
        "campaign_id": receipt.campaign_id,
        "selected_trial_id": receipt.selected_trial_id,
        "oss2f_contract_version": receipt.oss2f_contract_version,
        "freeze_receipt_id": receipt.freeze_receipt_id,
        "freeze_receipt_hash": receipt.freeze_receipt_hash,
        "candidate_freeze_fingerprint": receipt.candidate_freeze_fingerprint,
        "oss2e_policy_fingerprint": receipt.oss2e_policy_fingerprint,
        "protocol_policy_fingerprint": receipt.protocol_policy_fingerprint,
        "holdout_authorization_id": receipt.holdout_authorization_id,
        "split_name": receipt.split_name,
        "permit_purpose": receipt.permit_purpose,
        "min_net_return": receipt.min_net_return,
        "min_sharpe": receipt.min_sharpe,
        "max_drawdown": receipt.max_drawdown,
        "max_evaluations": receipt.max_evaluations,
        "retuning_allowed": receipt.retuning_allowed,
        "reselection_allowed": receipt.reselection_allowed,
        "second_attempt_allowed": receipt.second_attempt_allowed,
        "failure_is_terminal": receipt.failure_is_terminal,
        "final_holdout_observed": receipt.final_holdout_observed,
        "final_holdout_consumed": receipt.final_holdout_consumed,
        "paper_execution_authorized": receipt.paper_execution_authorized,
        "capital_authority": receipt.capital_authority,
        "live_trading": receipt.live_trading,
    }
    if include_hash:
        payload["receipt_hash"] = receipt.receipt_hash
    return payload


def _require_id(value: str, name: str) -> None:
    if not _ID_RE.fullmatch(value):
        raise OSS2FinalHoldoutProtocolIntegrityError(f"invalid {name}")


def _require_hash(value: str, name: str) -> None:
    if not _HASH_RE.fullmatch(value):
        raise OSS2FinalHoldoutProtocolIntegrityError(f"invalid {name}")


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
