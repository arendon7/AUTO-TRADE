"""OSS-2F durable pre-holdout freeze for the certified OSS-2 research chain.

This module materializes the mechanical OSS-2E decision into one hash-bound,
append-only SQLite receipt per campaign. It deliberately has no FINAL_HOLDOUT
input or reader and grants no broker, network, OMS, capital, OrderIntent,
PAPER-execution or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from .oss2_holdout_eligibility import (
    OSS2HoldoutEligibilityDecision,
    OSS2HoldoutEligibilityEvidence,
    evaluate_oss2e_holdout_eligibility,
)
from .oss2_robustness import OSS2RobustnessEvidence


OSS2F_CONTRACT_VERSION = "OSS2F_HOLDOUT_FREEZE_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class OSS2HoldoutFreezeError(RuntimeError):
    pass


class OSS2HoldoutFreezeIntegrityError(OSS2HoldoutFreezeError):
    pass


class OSS2HoldoutFreezeConflict(OSS2HoldoutFreezeError):
    pass


class OSS2HoldoutFreezeState(str, Enum):
    HOLDOUT_ELIGIBLE = "HOLDOUT_ELIGIBLE"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class OSS2HoldoutFreezeReceipt:
    receipt_id: str
    contract_version: str
    campaign_id: str
    selected_trial_id: str
    oss2d_evidence_fingerprint: str
    oss2e_policy_fingerprint: str
    oss2e_evidence_fingerprint: str
    candidate_freeze_fingerprint: str
    decision: OSS2HoldoutFreezeState
    failed_gate_ids: tuple[str, ...]
    final_holdout_observed: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        _require_id(self.receipt_id, "receipt_id")
        _require_id(self.campaign_id, "campaign_id")
        _require_id(self.selected_trial_id, "selected_trial_id")
        if self.contract_version != OSS2F_CONTRACT_VERSION:
            raise OSS2HoldoutFreezeIntegrityError("noncanonical OSS-2F contract version")
        for name, value in (
            ("oss2d_evidence_fingerprint", self.oss2d_evidence_fingerprint),
            ("oss2e_policy_fingerprint", self.oss2e_policy_fingerprint),
            ("oss2e_evidence_fingerprint", self.oss2e_evidence_fingerprint),
            ("candidate_freeze_fingerprint", self.candidate_freeze_fingerprint),
            ("receipt_hash", self.receipt_hash),
        ):
            _require_hash(value, name)
        if self.decision is OSS2HoldoutFreezeState.HOLDOUT_ELIGIBLE and self.failed_gate_ids:
            raise OSS2HoldoutFreezeIntegrityError("eligible freeze may not contain failed gates")
        if self.decision is OSS2HoldoutFreezeState.REJECT and not self.failed_gate_ids:
            raise OSS2HoldoutFreezeIntegrityError("rejected freeze must contain failed gates")
        if self.final_holdout_observed is not False:
            raise OSS2HoldoutFreezeIntegrityError("OSS-2F may not observe FINAL_HOLDOUT")
        if self.paper_execution_authorized is not False:
            raise OSS2HoldoutFreezeIntegrityError("OSS-2F may not authorize PAPER execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise OSS2HoldoutFreezeIntegrityError("OSS-2F may not grant capital or LIVE authority")
        if self.receipt_hash != _hash(_receipt_payload(self, include_hash=False)):
            raise OSS2HoldoutFreezeIntegrityError("OSS-2F receipt hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _receipt_payload(self, include_hash=True)


class SQLiteOSS2HoldoutFreezeRegistry:
    """Append-only OSS-2F research registry: exactly one freeze per campaign."""

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
                CREATE TABLE IF NOT EXISTS oss2_holdout_freezes (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id TEXT NOT NULL UNIQUE,
                    campaign_id TEXT NOT NULL UNIQUE,
                    selected_trial_id TEXT NOT NULL,
                    oss2d_evidence_fingerprint TEXT NOT NULL,
                    oss2e_policy_fingerprint TEXT NOT NULL,
                    oss2e_evidence_fingerprint TEXT NOT NULL UNIQUE,
                    candidate_freeze_fingerprint TEXT NOT NULL UNIQUE,
                    decision TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL
                );
                """
            )
        finally:
            conn.close()

    def freeze_and_record(
        self,
        *,
        receipt_id: str,
        robustness: OSS2RobustnessEvidence,
    ) -> OSS2HoldoutFreezeReceipt:
        _require_id(receipt_id, "receipt_id")
        eligibility = evaluate_oss2e_holdout_eligibility(robustness)
        candidate = _build_receipt(receipt_id=receipt_id, eligibility=eligibility)

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_row = conn.execute(
                "SELECT * FROM oss2_holdout_freezes WHERE campaign_id = ?",
                (candidate.campaign_id,),
            ).fetchone()
            if existing_row is not None:
                existing = _receipt_from_row(existing_row)
                if (
                    existing.receipt_id != receipt_id
                    or existing.oss2d_evidence_fingerprint != candidate.oss2d_evidence_fingerprint
                    or existing.oss2e_policy_fingerprint != candidate.oss2e_policy_fingerprint
                    or existing.oss2e_evidence_fingerprint != candidate.oss2e_evidence_fingerprint
                    or existing.candidate_freeze_fingerprint != candidate.candidate_freeze_fingerprint
                    or existing.decision is not candidate.decision
                ):
                    raise OSS2HoldoutFreezeConflict(
                        "OSS-2F campaign is already frozen under different evidence"
                    )
                conn.execute("COMMIT")
                return existing

            conn.execute(
                """
                INSERT INTO oss2_holdout_freezes(
                    receipt_id, campaign_id, selected_trial_id,
                    oss2d_evidence_fingerprint, oss2e_policy_fingerprint,
                    oss2e_evidence_fingerprint, candidate_freeze_fingerprint,
                    decision, receipt_hash, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.receipt_id,
                    candidate.campaign_id,
                    candidate.selected_trial_id,
                    candidate.oss2d_evidence_fingerprint,
                    candidate.oss2e_policy_fingerprint,
                    candidate.oss2e_evidence_fingerprint,
                    candidate.candidate_freeze_fingerprint,
                    candidate.decision.value,
                    candidate.receipt_hash,
                    _canonical_json(candidate.to_dict()),
                ),
            )
            conn.execute("COMMIT")
            return candidate
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_for_campaign(self, campaign_id: str) -> OSS2HoldoutFreezeReceipt | None:
        _require_id(campaign_id, "campaign_id")
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM oss2_holdout_freezes WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            return _receipt_from_row(row) if row is not None else None
        finally:
            conn.close()


def read_oss2f_freeze_read_only(
    path: str | Path,
    *,
    campaign_id: str,
) -> OSS2HoldoutFreezeReceipt | None:
    """Independently verify a durable freeze without creating or mutating schema."""
    _require_id(campaign_id, "campaign_id")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise OSS2HoldoutFreezeIntegrityError("OSS-2F durable registry does not exist")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='oss2_holdout_freezes'"
        ).fetchone()
        if table is None:
            raise OSS2HoldoutFreezeIntegrityError("OSS-2F durable table is missing")
        row = conn.execute(
            "SELECT * FROM oss2_holdout_freezes WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        return _receipt_from_row(row) if row is not None else None
    finally:
        conn.close()


def _build_receipt(
    *,
    receipt_id: str,
    eligibility: OSS2HoldoutEligibilityEvidence,
) -> OSS2HoldoutFreezeReceipt:
    decision = OSS2HoldoutFreezeState(eligibility.decision.value)
    payload = {
        "receipt_id": receipt_id,
        "contract_version": OSS2F_CONTRACT_VERSION,
        "campaign_id": eligibility.campaign_id,
        "selected_trial_id": eligibility.selected_trial_id,
        "oss2d_evidence_fingerprint": eligibility.oss2d_evidence_fingerprint,
        "oss2e_policy_fingerprint": eligibility.policy_fingerprint,
        "oss2e_evidence_fingerprint": eligibility.fingerprint,
        "candidate_freeze_fingerprint": eligibility.candidate_freeze_fingerprint,
        "decision": decision.value,
        "failed_gate_ids": list(eligibility.failed_gate_ids),
        "final_holdout_observed": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS2HoldoutFreezeReceipt(
        receipt_id=receipt_id,
        contract_version=OSS2F_CONTRACT_VERSION,
        campaign_id=eligibility.campaign_id,
        selected_trial_id=eligibility.selected_trial_id,
        oss2d_evidence_fingerprint=eligibility.oss2d_evidence_fingerprint,
        oss2e_policy_fingerprint=eligibility.policy_fingerprint,
        oss2e_evidence_fingerprint=eligibility.fingerprint,
        candidate_freeze_fingerprint=eligibility.candidate_freeze_fingerprint,
        decision=decision,
        failed_gate_ids=eligibility.failed_gate_ids,
        final_holdout_observed=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
        receipt_hash=_hash(payload),
    )


def _receipt_from_row(row: sqlite3.Row) -> OSS2HoldoutFreezeReceipt:
    try:
        payload = json.loads(str(row["receipt_json"]))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise OSS2HoldoutFreezeIntegrityError("invalid OSS-2F durable receipt JSON") from exc
    if not isinstance(payload, dict):
        raise OSS2HoldoutFreezeIntegrityError("OSS-2F durable receipt must be an object")
    try:
        receipt = OSS2HoldoutFreezeReceipt(
            receipt_id=str(payload["receipt_id"]),
            contract_version=str(payload["contract_version"]),
            campaign_id=str(payload["campaign_id"]),
            selected_trial_id=str(payload["selected_trial_id"]),
            oss2d_evidence_fingerprint=str(payload["oss2d_evidence_fingerprint"]),
            oss2e_policy_fingerprint=str(payload["oss2e_policy_fingerprint"]),
            oss2e_evidence_fingerprint=str(payload["oss2e_evidence_fingerprint"]),
            candidate_freeze_fingerprint=str(payload["candidate_freeze_fingerprint"]),
            decision=OSS2HoldoutFreezeState(str(payload["decision"])),
            failed_gate_ids=tuple(str(value) for value in payload["failed_gate_ids"]),
            final_holdout_observed=payload["final_holdout_observed"],
            paper_execution_authorized=payload["paper_execution_authorized"],
            capital_authority=str(payload["capital_authority"]),
            live_trading=str(payload["live_trading"]),
            receipt_hash=str(payload["receipt_hash"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OSS2HoldoutFreezeIntegrityError("invalid OSS-2F durable receipt fields") from exc
    side_columns = {
        "receipt_id": receipt.receipt_id,
        "campaign_id": receipt.campaign_id,
        "selected_trial_id": receipt.selected_trial_id,
        "oss2d_evidence_fingerprint": receipt.oss2d_evidence_fingerprint,
        "oss2e_policy_fingerprint": receipt.oss2e_policy_fingerprint,
        "oss2e_evidence_fingerprint": receipt.oss2e_evidence_fingerprint,
        "candidate_freeze_fingerprint": receipt.candidate_freeze_fingerprint,
        "decision": receipt.decision.value,
        "receipt_hash": receipt.receipt_hash,
    }
    for key, value in side_columns.items():
        if str(row[key]) != value:
            raise OSS2HoldoutFreezeIntegrityError(f"OSS-2F side-column mismatch: {key}")
    return receipt


def _receipt_payload(
    receipt: OSS2HoldoutFreezeReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "receipt_id": receipt.receipt_id,
        "contract_version": receipt.contract_version,
        "campaign_id": receipt.campaign_id,
        "selected_trial_id": receipt.selected_trial_id,
        "oss2d_evidence_fingerprint": receipt.oss2d_evidence_fingerprint,
        "oss2e_policy_fingerprint": receipt.oss2e_policy_fingerprint,
        "oss2e_evidence_fingerprint": receipt.oss2e_evidence_fingerprint,
        "candidate_freeze_fingerprint": receipt.candidate_freeze_fingerprint,
        "decision": receipt.decision.value,
        "failed_gate_ids": list(receipt.failed_gate_ids),
        "final_holdout_observed": receipt.final_holdout_observed,
        "paper_execution_authorized": receipt.paper_execution_authorized,
        "capital_authority": receipt.capital_authority,
        "live_trading": receipt.live_trading,
    }
    if include_hash:
        payload["receipt_hash"] = receipt.receipt_hash
    return payload


def _require_id(value: str, name: str) -> None:
    if not _ID_RE.fullmatch(value):
        raise OSS2HoldoutFreezeIntegrityError(f"invalid {name}")


def _require_hash(value: str, name: str) -> None:
    if not _HASH_RE.fullmatch(value):
        raise OSS2HoldoutFreezeIntegrityError(f"invalid {name}")


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(payload: object) -> str:
    return sha256(_canonical_json(payload).encode()).hexdigest()
