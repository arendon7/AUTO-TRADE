from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.paper_runtime_readiness_source as source_v1
from autotrade.paper_candidate_admission_final_verification import (
    PaperCandidateAdmissionFinalVerification,
)
from autotrade.paper_candidate_admission_lifecycle import PaperCandidateEligibilityState
from autotrade.paper_candidate_eligibility_final import PaperCandidateFinalEligibility


W85_DURABLE_ELIGIBILITY_SNAPSHOT_VERSION = "W86_W85_DURABLE_ELIGIBILITY_SNAPSHOT_V2"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class PaperRuntimeReadinessSnapshotError(RuntimeError):
    pass


class PaperRuntimeReadinessSnapshotIntegrityError(PaperRuntimeReadinessSnapshotError):
    pass


@dataclass(frozen=True, slots=True)
class W85DurableEligibilitySnapshotProof:
    """Atomic, read-only W85 authority proof consumed by W86.

    V2 pins all policy/admission/lifecycle reads to one SQLite read transaction
    and refuses to return a proof when another connection changes the durable W85
    authority while that snapshot is being verified. It is still only source
    evidence: it grants no PAPER execution, capital or LIVE authority.
    """

    proof_id: str
    contract_version: str
    authority_key: str
    admission_id: str
    admission_hash: str
    policy_id: str
    policy_hash: str
    policy_registration_hash: str
    final_admission_verification_hash: str
    supplied_final_eligibility_hash: str
    w84_admission_source_proof_hash: str
    selected_trial_fingerprint: str
    strategy_spec_hash: str
    loaded_runtime_code_hash: str
    fee_product_economics_hash: str
    intent_fingerprint: str
    admission_valid_until: datetime
    probation_notional_cap_usd: Decimal
    probation_order_cap: int
    lifecycle_head_hash: str
    lifecycle_events_count: int
    current_state: PaperCandidateEligibilityState
    supplied_eligibility_observed_at: datetime
    reproved_at: datetime
    sqlite_data_version: int
    candidate_currently_eligible: bool
    durable_admission_verified: bool
    durable_lifecycle_verified: bool
    sqlite_read_only: bool
    sqlite_snapshot_consistent: bool
    concurrent_durable_change_detected: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    proof_hash: str

    def __post_init__(self) -> None:
        _require_id(self.proof_id, "proof_id")
        _require_id(self.admission_id, "admission_id")
        _require_id(self.policy_id, "policy_id")
        if self.contract_version != W85_DURABLE_ELIGIBILITY_SNAPSHOT_VERSION:
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "W86 W85 snapshot-proof version is not canonical V2"
            )
        for label, value in (
            ("authority_key", self.authority_key),
            ("admission_hash", self.admission_hash),
            ("policy_hash", self.policy_hash),
            ("policy_registration_hash", self.policy_registration_hash),
            ("final_admission_verification_hash", self.final_admission_verification_hash),
            ("supplied_final_eligibility_hash", self.supplied_final_eligibility_hash),
            ("w84_admission_source_proof_hash", self.w84_admission_source_proof_hash),
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("loaded_runtime_code_hash", self.loaded_runtime_code_hash),
            ("fee_product_economics_hash", self.fee_product_economics_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("lifecycle_head_hash", self.lifecycle_head_hash),
            ("proof_hash", self.proof_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("admission_valid_until", self.admission_valid_until),
            ("supplied_eligibility_observed_at", self.supplied_eligibility_observed_at),
            ("reproved_at", self.reproved_at),
        ):
            _require_aware(value, label)
        if not isinstance(self.current_state, PaperCandidateEligibilityState):
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "invalid current W85 lifecycle state"
            )
        for label, value, minimum in (
            ("lifecycle_events_count", self.lifecycle_events_count, 0),
            ("probation_order_cap", self.probation_order_cap, 1),
            ("sqlite_data_version", self.sqlite_data_version, 0),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise PaperRuntimeReadinessSnapshotIntegrityError(
                    f"{label} must be integer >= {minimum}"
                )
        if (
            not isinstance(self.probation_notional_cap_usd, Decimal)
            or not self.probation_notional_cap_usd.is_finite()
            or self.probation_notional_cap_usd <= 0
        ):
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "probation_notional_cap_usd must be finite Decimal >0"
            )
        if _utc(self.reproved_at) < _utc(self.supplied_eligibility_observed_at):
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "W86 V2 source reproof cannot predate supplied W85 eligibility"
            )
        expected_eligible = self.current_state is PaperCandidateEligibilityState.ACTIVE
        if self.candidate_currently_eligible is not expected_eligible:
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "candidate flag disagrees with atomic durable W85 state"
            )
        if (
            self.durable_admission_verified is not True
            or self.durable_lifecycle_verified is not True
            or self.sqlite_read_only is not True
            or self.sqlite_snapshot_consistent is not True
            or self.concurrent_durable_change_detected is not False
        ):
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "W86 V2 proof requires unchanged read-only durable snapshot"
            )
        _require_no_execution_authority(
            paper_execution=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
        )
        if self.proof_hash != _hash(_proof_payload(self, include_hash=False)):
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "W86 W85 snapshot-proof hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _proof_payload(self, include_hash=True)


class W85DurableEligibilitySnapshotReader:
    """Canonical W86 reader for atomic W85 durable eligibility truth."""

    def __init__(self, core_path: str | Path) -> None:
        raw = Path(core_path).expanduser()
        if raw.is_symlink():
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "W86 refuses symlinked authoritative core database"
            )
        resolved = raw.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "W86 requires an existing authoritative core database"
            )
        self._core_path = resolved

    def verify_current(
        self,
        *,
        proof_id: str,
        eligibility: PaperCandidateFinalEligibility,
        final_verification: PaperCandidateAdmissionFinalVerification,
    ) -> W85DurableEligibilitySnapshotProof:
        _require_id(proof_id, "proof_id")
        if not isinstance(eligibility, PaperCandidateFinalEligibility):
            raise TypeError("eligibility must be PaperCandidateFinalEligibility")
        if not isinstance(final_verification, PaperCandidateAdmissionFinalVerification):
            raise TypeError(
                "final_verification must be PaperCandidateAdmissionFinalVerification"
            )

        try:
            source_v1._validate_supplied_final_objects(eligibility, final_verification)
        except source_v1.PaperRuntimeReadinessSourceIntegrityError as exc:
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "supplied W85 final objects failed V2 source validation"
            ) from exc

        now = _now_utc()
        _require_aware(now, "W86 V2 source process clock")
        if _utc(now) < _utc(eligibility.observed_at):
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "W86 V2 source process clock predates supplied W85 eligibility"
            )

        conn = self._connect_read_only()
        try:
            data_version_before = _data_version(conn)
            conn.execute("BEGIN")
            try:
                source_v1._require_schema(conn)
                registration = source_v1._read_registration(
                    conn, final_verification.policy_id
                )
                receipt = source_v1._read_receipt(conn, eligibility.admission_id)
                source_v1._validate_registration_and_receipt(registration, receipt)
                source_v1._validate_full_admission_chain(conn, receipt)
                events = source_v1._read_and_validate_lifecycle(conn, receipt)
                source_v1._match_final_verification_to_durable(
                    final_verification=final_verification,
                    receipt=receipt,
                    registration=registration,
                )
                state = source_v1._current_state(receipt, events, now)
                head_hash = (
                    events[-1].event_hash if events else lifecycle.ZERO_EVENT_HASH
                )
                source_v1._match_supplied_eligibility_to_current_durable(
                    eligibility=eligibility,
                    final_verification=final_verification,
                    receipt=receipt,
                    state=state,
                    head_hash=head_hash,
                    events_count=len(events),
                )
                snapshot_identity = (
                    receipt.admission_hash,
                    len(events),
                    head_hash,
                )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

            data_version_after_snapshot = _data_version(conn)
            post_identity = _read_current_identity(
                conn,
                admission_id=receipt.admission_id,
            )
            data_version_after_postcheck = _data_version(conn)
            if (
                data_version_before != data_version_after_snapshot
                or data_version_after_snapshot != data_version_after_postcheck
                or snapshot_identity != post_identity
            ):
                raise PaperRuntimeReadinessSnapshotIntegrityError(
                    "durable W85 authority changed during W86 source snapshot"
                )
        except source_v1.PaperRuntimeReadinessSourceIntegrityError as exc:
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "durable W85 source failed atomic V2 integrity validation"
            ) from exc
        finally:
            conn.close()

        assert receipt.valid_until is not None
        assert receipt.w84_admission_source_proof_hash is not None
        values = {
            "proof_id": proof_id,
            "contract_version": W85_DURABLE_ELIGIBILITY_SNAPSHOT_VERSION,
            "authority_key": receipt.authority_key,
            "admission_id": receipt.admission_id,
            "admission_hash": receipt.admission_hash,
            "policy_id": receipt.policy_id,
            "policy_hash": receipt.policy_hash,
            "policy_registration_hash": receipt.policy_registration_hash,
            "final_admission_verification_hash": final_verification.verification_hash,
            "supplied_final_eligibility_hash": eligibility.projection_hash,
            "w84_admission_source_proof_hash": receipt.w84_admission_source_proof_hash,
            "selected_trial_fingerprint": receipt.selected_trial_fingerprint,
            "strategy_spec_hash": receipt.strategy_spec_hash,
            "loaded_runtime_code_hash": receipt.loaded_runtime_code_hash,
            "fee_product_economics_hash": receipt.fee_product_economics_hash,
            "intent_fingerprint": receipt.intent_fingerprint,
            "admission_valid_until": receipt.valid_until,
            "probation_notional_cap_usd": receipt.probation_notional_cap_usd,
            "probation_order_cap": receipt.probation_order_cap,
            "lifecycle_head_hash": head_hash,
            "lifecycle_events_count": len(events),
            "current_state": state,
            "supplied_eligibility_observed_at": eligibility.observed_at,
            "reproved_at": now,
            "sqlite_data_version": data_version_after_postcheck,
            "candidate_currently_eligible": state is PaperCandidateEligibilityState.ACTIVE,
            "durable_admission_verified": True,
            "durable_lifecycle_verified": True,
            "sqlite_read_only": True,
            "sqlite_snapshot_consistent": True,
            "concurrent_durable_change_detected": False,
            "paper_execution_authorized": False,
            "external_execution_authorized": False,
            "runtime_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        return W85DurableEligibilitySnapshotProof(
            **values,
            proof_hash=_hash(_proof_payload_from_values(values)),
        )

    def _connect_read_only(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            f"file:{self._core_path}?mode=ro",
            uri=True,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute("PRAGMA query_only").fetchone()
        if row is None or int(row[0]) != 1:
            conn.close()
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                "W86 V2 could not enforce SQLite query_only mode"
            )
        return conn


def _data_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA data_version").fetchone()
    if row is None:
        raise PaperRuntimeReadinessSnapshotIntegrityError(
            "SQLite data_version unavailable"
        )
    value = int(row[0])
    if value < 0:
        raise PaperRuntimeReadinessSnapshotIntegrityError(
            "SQLite data_version invalid"
        )
    return value


def _read_current_identity(
    conn: sqlite3.Connection,
    *,
    admission_id: str,
) -> tuple[str, int, str]:
    admission_row = conn.execute(
        "SELECT admission_hash FROM paper_candidate_admissions WHERE admission_id = ?",
        (admission_id,),
    ).fetchone()
    if admission_row is None:
        raise PaperRuntimeReadinessSnapshotIntegrityError(
            "durable W85 admission disappeared after source snapshot"
        )
    event_rows = conn.execute(
        """
        SELECT event_hash FROM paper_candidate_admission_events
        WHERE admission_id = ?
        ORDER BY sequence
        """,
        (admission_id,),
    ).fetchall()
    head_hash = (
        str(event_rows[-1]["event_hash"])
        if event_rows
        else lifecycle.ZERO_EVENT_HASH
    )
    return str(admission_row["admission_hash"]), len(event_rows), head_hash


def _proof_payload(
    value: W85DurableEligibilitySnapshotProof, *, include_hash: bool
) -> dict[str, object]:
    names = tuple(
        field
        for field in W85DurableEligibilitySnapshotProof.__dataclass_fields__
        if field != "proof_hash"
    )
    payload = _proof_payload_from_values(
        {name: getattr(value, name) for name in names}
    )
    if include_hash:
        payload["proof_hash"] = value.proof_hash
    return payload


def _proof_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    state = payload["current_state"]
    if not isinstance(state, PaperCandidateEligibilityState):
        raise PaperRuntimeReadinessSnapshotIntegrityError(
            "current_state type invalid"
        )
    payload["current_state"] = state.value
    payload["probation_notional_cap_usd"] = str(
        payload["probation_notional_cap_usd"]
    )
    for key in (
        "admission_valid_until",
        "supplied_eligibility_observed_at",
        "reproved_at",
    ):
        value = payload[key]
        if not isinstance(value, datetime):
            raise PaperRuntimeReadinessSnapshotIntegrityError(
                f"{key} type invalid"
            )
        payload[key] = _utc(value).isoformat()
    return payload


def _require_no_execution_authority(
    *, paper_execution: bool, external: bool, runtime: bool, capital: str, live: str
) -> None:
    if (
        paper_execution is not False
        or external is not False
        or runtime is not False
        or capital != "NONE"
        or live != "BLOCKED"
    ):
        raise PaperRuntimeReadinessSnapshotIntegrityError(
            "W86 V2 source proof may not grant execution, capital or LIVE authority"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperRuntimeReadinessSnapshotIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeReadinessSnapshotIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperRuntimeReadinessSnapshotIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "W85_DURABLE_ELIGIBILITY_SNAPSHOT_VERSION",
    "PaperRuntimeReadinessSnapshotError",
    "PaperRuntimeReadinessSnapshotIntegrityError",
    "W85DurableEligibilitySnapshotProof",
    "W85DurableEligibilitySnapshotReader",
]
