from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

import autotrade.paper_candidate_admission as admission
import autotrade.paper_candidate_admission_final_verification as final_admission
import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.paper_candidate_eligibility_final as final_eligibility
from autotrade.paper_candidate_admission import (
    PaperCandidateAdmissionReceipt,
    PaperCandidateAdmissionStatus,
)
from autotrade.paper_candidate_admission_final_verification import (
    PaperCandidateAdmissionFinalVerification,
)
from autotrade.paper_candidate_admission_lifecycle import (
    PaperCandidateEligibilityState,
    PaperCandidateLifecycleAction,
    PaperCandidateLifecycleEvent,
)
from autotrade.paper_candidate_eligibility_final import PaperCandidateFinalEligibility


W85_DURABLE_ELIGIBILITY_SOURCE_VERSION = "W86_W85_DURABLE_ELIGIBILITY_SOURCE_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class PaperRuntimeReadinessSourceError(RuntimeError):
    pass


class PaperRuntimeReadinessSourceIntegrityError(PaperRuntimeReadinessSourceError):
    pass


@dataclass(frozen=True, slots=True)
class W85DurableEligibilitySourceProof:
    """Read-only proof of current durable W85 candidate truth.

    This object is an input proof for W86 only. It does not represent runtime
    readiness and cannot grant execution, capital, broker-write or LIVE authority.
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
    candidate_currently_eligible: bool
    durable_admission_verified: bool
    durable_lifecycle_verified: bool
    sqlite_read_only: bool
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
        if self.contract_version != W85_DURABLE_ELIGIBILITY_SOURCE_VERSION:
            raise PaperRuntimeReadinessSourceIntegrityError(
                "W86 W85-source proof version is not canonical"
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
            raise PaperRuntimeReadinessSourceIntegrityError("invalid current W85 state")
        if (
            isinstance(self.lifecycle_events_count, bool)
            or not isinstance(self.lifecycle_events_count, int)
            or self.lifecycle_events_count < 0
        ):
            raise PaperRuntimeReadinessSourceIntegrityError(
                "lifecycle_events_count must be integer >=0"
            )
        if (
            isinstance(self.probation_order_cap, bool)
            or not isinstance(self.probation_order_cap, int)
            or self.probation_order_cap < 1
        ):
            raise PaperRuntimeReadinessSourceIntegrityError(
                "probation_order_cap must be integer >=1"
            )
        if (
            not isinstance(self.probation_notional_cap_usd, Decimal)
            or not self.probation_notional_cap_usd.is_finite()
            or self.probation_notional_cap_usd <= 0
        ):
            raise PaperRuntimeReadinessSourceIntegrityError(
                "probation_notional_cap_usd must be finite Decimal >0"
            )
        if _utc(self.reproved_at) < _utc(self.supplied_eligibility_observed_at):
            raise PaperRuntimeReadinessSourceIntegrityError(
                "W86 source reproof cannot predate supplied W85 eligibility observation"
            )
        expected_eligible = self.current_state is PaperCandidateEligibilityState.ACTIVE
        if self.candidate_currently_eligible is not expected_eligible:
            raise PaperRuntimeReadinessSourceIntegrityError(
                "current W85 candidate flag disagrees with durable lifecycle state"
            )
        if (
            self.durable_admission_verified is not True
            or self.durable_lifecycle_verified is not True
            or self.sqlite_read_only is not True
        ):
            raise PaperRuntimeReadinessSourceIntegrityError(
                "W86 source proof requires read-only durable W85 revalidation"
            )
        _require_no_execution_authority(
            paper_execution=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
        )
        if self.proof_hash != _hash(_proof_payload(self, include_hash=False)):
            raise PaperRuntimeReadinessSourceIntegrityError(
                "W86 W85-source proof hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _proof_payload(self, include_hash=True)


class W85DurableEligibilitySourceReader:
    """Independent read-only reader for W85 admission + lifecycle authority.

    It deliberately opens SQLite itself in `mode=ro` + `query_only=ON` and never
    instantiates either W85 writer registry. The supplied W85 final objects are
    treated as claims to compare against durable truth, not as authority by
    themselves.
    """

    def __init__(self, core_path: str | Path) -> None:
        raw = Path(core_path).expanduser()
        if raw.is_symlink():
            raise PaperRuntimeReadinessSourceIntegrityError(
                "W86 refuses symlinked authoritative core database"
            )
        resolved = raw.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise PaperRuntimeReadinessSourceIntegrityError(
                "W86 requires an existing authoritative core database"
            )
        self._core_path = resolved

    def verify_current(
        self,
        *,
        proof_id: str,
        eligibility: PaperCandidateFinalEligibility,
        final_verification: PaperCandidateAdmissionFinalVerification,
    ) -> W85DurableEligibilitySourceProof:
        _require_id(proof_id, "proof_id")
        if not isinstance(eligibility, PaperCandidateFinalEligibility):
            raise TypeError("eligibility must be PaperCandidateFinalEligibility")
        if not isinstance(final_verification, PaperCandidateAdmissionFinalVerification):
            raise TypeError(
                "final_verification must be PaperCandidateAdmissionFinalVerification"
            )

        _validate_supplied_final_objects(eligibility, final_verification)
        now = _now_utc()
        _require_aware(now, "W86 W85-source process clock")
        if _utc(now) < _utc(eligibility.observed_at):
            raise PaperRuntimeReadinessSourceIntegrityError(
                "W86 source process clock predates supplied W85 eligibility"
            )

        conn = self._connect_read_only()
        try:
            _require_schema(conn)
            registration = _read_registration(conn, final_verification.policy_id)
            receipt = _read_receipt(conn, eligibility.admission_id)
            _validate_registration_and_receipt(registration, receipt)
            _validate_full_admission_chain(conn, receipt)
            events = _read_and_validate_lifecycle(conn, receipt)
        finally:
            conn.close()

        _match_final_verification_to_durable(
            final_verification=final_verification,
            receipt=receipt,
            registration=registration,
        )
        state = _current_state(receipt, events, now)
        head_hash = events[-1].event_hash if events else lifecycle.ZERO_EVENT_HASH
        _match_supplied_eligibility_to_current_durable(
            eligibility=eligibility,
            final_verification=final_verification,
            receipt=receipt,
            state=state,
            head_hash=head_hash,
            events_count=len(events),
        )

        assert receipt.valid_until is not None
        assert receipt.w84_admission_source_proof_hash is not None
        values = {
            "proof_id": proof_id,
            "contract_version": W85_DURABLE_ELIGIBILITY_SOURCE_VERSION,
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
            "candidate_currently_eligible": state is PaperCandidateEligibilityState.ACTIVE,
            "durable_admission_verified": True,
            "durable_lifecycle_verified": True,
            "sqlite_read_only": True,
            "paper_execution_authorized": False,
            "external_execution_authorized": False,
            "runtime_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        return W85DurableEligibilitySourceProof(
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
            raise PaperRuntimeReadinessSourceIntegrityError(
                "W86 could not enforce SQLite query_only mode"
            )
        return conn


def _validate_supplied_final_objects(
    eligibility: PaperCandidateFinalEligibility,
    final_verification: PaperCandidateAdmissionFinalVerification,
) -> None:
    eligibility_payload = eligibility.to_dict()
    eligibility_hash = eligibility_payload.pop("projection_hash", None)
    if eligibility_hash != eligibility.projection_hash or _hash(eligibility_payload) != eligibility.projection_hash:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "supplied W85 final eligibility self-hash mismatch"
        )
    verification_payload = final_verification.to_dict()
    verification_hash = verification_payload.pop("verification_hash", None)
    if (
        verification_hash != final_verification.verification_hash
        or _hash(verification_payload) != final_verification.verification_hash
    ):
        raise PaperRuntimeReadinessSourceIntegrityError(
            "supplied W85 final admission verification self-hash mismatch"
        )
    if eligibility.final_admission_verification_hash != final_verification.verification_hash:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "W85 final eligibility does not bind supplied final admission verification"
        )
    if eligibility.w84_admission_source_proof_hash != final_verification.w84_admission_source_proof_hash:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "W85 final eligibility/final verification source-proof mismatch"
        )
    if _utc(eligibility.observed_at) < _utc(final_verification.process_verified_at):
        raise PaperRuntimeReadinessSourceIntegrityError(
            "W85 final eligibility predates final admission verification"
        )
    _require_no_execution_authority(
        paper_execution=eligibility.paper_execution_authorized,
        external=eligibility.external_execution_authorized,
        runtime=eligibility.runtime_execution_authorized,
        capital=eligibility.capital_authority,
        live=eligibility.live_trading,
    )
    _require_no_execution_authority(
        paper_execution=final_verification.paper_execution_authorized,
        external=final_verification.external_execution_authorized,
        runtime=final_verification.runtime_execution_authorized,
        capital=final_verification.capital_authority,
        live=final_verification.live_trading,
    )


def _require_schema(conn: sqlite3.Connection) -> None:
    tables = {
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    required = {
        "paper_candidate_admission_policies",
        "paper_candidate_admissions",
        "paper_candidate_admission_events",
    }
    if not required.issubset(tables):
        raise PaperRuntimeReadinessSourceIntegrityError(
            "W86 source reader requires complete durable W85 admission/lifecycle schema"
        )


def _read_registration(conn: sqlite3.Connection, policy_id: str):
    row = conn.execute(
        "SELECT * FROM paper_candidate_admission_policies WHERE policy_id = ?",
        (policy_id,),
    ).fetchone()
    if row is None:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "durable W85 policy registration is missing"
        )
    try:
        return admission._registration_from_row(row)
    except Exception as exc:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "durable W85 policy registration failed integrity validation"
        ) from exc


def _read_receipt(
    conn: sqlite3.Connection, admission_id: str
) -> PaperCandidateAdmissionReceipt:
    row = conn.execute(
        "SELECT * FROM paper_candidate_admissions WHERE admission_id = ?",
        (admission_id,),
    ).fetchone()
    if row is None:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "durable W85 admission receipt is missing"
        )
    try:
        return admission._receipt_from_row(row)
    except Exception as exc:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "durable W85 admission receipt failed integrity validation"
        ) from exc


def _validate_full_admission_chain(
    conn: sqlite3.Connection, receipt: PaperCandidateAdmissionReceipt
) -> None:
    rows = conn.execute(
        """
        SELECT * FROM paper_candidate_admissions
        WHERE authority_key = ?
        ORDER BY sequence
        """,
        (receipt.authority_key,),
    ).fetchall()
    try:
        receipts = tuple(admission._receipt_from_row(row) for row in rows)
        admission._validate_admission_chain(receipts)
    except Exception as exc:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "durable W85 admission chain failed integrity validation"
        ) from exc
    if not any(item.admission_hash == receipt.admission_hash for item in receipts):
        raise PaperRuntimeReadinessSourceIntegrityError(
            "durable W85 authority chain does not contain supplied admission"
        )


def _validate_registration_and_receipt(registration, receipt: PaperCandidateAdmissionReceipt) -> None:
    policy = registration.policy
    if (
        receipt.status is not PaperCandidateAdmissionStatus.PASS
        or receipt.paper_candidate_authorized is not True
        or receipt.valid_until is None
    ):
        raise PaperRuntimeReadinessSourceIntegrityError(
            "W86 source requires a finite durable PASS W85 admission"
        )
    if (
        receipt.policy_id != policy.policy_id
        or receipt.policy_hash != policy.policy_hash
        or receipt.policy_registration_hash != registration.registration_hash
        or receipt.authority_key != policy.authority_key
        or receipt.probation_notional_cap_usd != policy.probation_notional_cap_usd
        or receipt.probation_order_cap != policy.probation_order_cap
    ):
        raise PaperRuntimeReadinessSourceIntegrityError(
            "durable W85 admission does not match frozen policy registration"
        )
    expected_valid_until = receipt.admitted_at + admission.timedelta(
        seconds=policy.candidate_validity_seconds
    )
    if receipt.valid_until != expected_valid_until:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "durable W85 candidate validity does not match frozen policy"
        )
    if (
        receipt.w84_admission_source_proof_hash is None
        or receipt.w84_admission_source_verification_hash is None
        or receipt.w84_admission_source_capture_at is None
        or receipt.w84_admission_source_verified_at is None
    ):
        raise PaperRuntimeReadinessSourceIntegrityError(
            "durable PASS W85 admission lacks V2 admission-source provenance"
        )
    if receipt.w84_admission_source_verified_at != receipt.admitted_at:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "durable W85 source proof is not bound to admission clock"
        )
    _require_no_execution_authority(
        paper_execution=receipt.paper_execution_authorized,
        external=receipt.external_execution_authorized,
        runtime=receipt.runtime_execution_authorized,
        capital=receipt.capital_authority,
        live=receipt.live_trading,
    )


def _read_and_validate_lifecycle(
    conn: sqlite3.Connection, receipt: PaperCandidateAdmissionReceipt
) -> tuple[PaperCandidateLifecycleEvent, ...]:
    rows = conn.execute(
        """
        SELECT * FROM paper_candidate_admission_events
        WHERE admission_id = ?
        ORDER BY sequence
        """,
        (receipt.admission_id,),
    ).fetchall()
    try:
        events = tuple(lifecycle._event_from_row(row) for row in rows)
        lifecycle._validate_event_chain(events, receipt)
    except Exception as exc:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "durable W85 lifecycle failed integrity validation"
        ) from exc
    return events


def _match_final_verification_to_durable(
    *,
    final_verification: PaperCandidateAdmissionFinalVerification,
    receipt: PaperCandidateAdmissionReceipt,
    registration,
) -> None:
    checks = (
        (final_verification.authority_key, receipt.authority_key, "authority key"),
        (final_verification.admission_id, receipt.admission_id, "admission id"),
        (final_verification.admission_hash, receipt.admission_hash, "admission hash"),
        (final_verification.policy_id, receipt.policy_id, "policy id"),
        (final_verification.policy_hash, receipt.policy_hash, "policy hash"),
        (
            final_verification.policy_registration_hash,
            receipt.policy_registration_hash,
            "policy registration hash",
        ),
        (
            final_verification.promotion_policy_hash,
            receipt.promotion_policy_hash,
            "promotion policy hash",
        ),
        (
            final_verification.threshold_policy_hash,
            receipt.threshold_policy_hash,
            "threshold policy hash",
        ),
        (final_verification.w80_assessment_hash, receipt.w80_assessment_hash, "W80 hash"),
        (final_verification.w81_resolution_hash, receipt.w81_resolution_hash, "W81 hash"),
        (final_verification.w82_resolution_hash, receipt.w82_resolution_hash, "W82 hash"),
        (final_verification.w83_resolution_hash, receipt.w83_resolution_hash, "W83 hash"),
        (
            final_verification.w84_finalization_hash,
            receipt.w84_finalization_hash,
            "W84 finalization hash",
        ),
        (
            final_verification.w84_source_verification_hash,
            receipt.w84_source_verification_hash,
            "W84 source verification hash",
        ),
        (
            final_verification.w84_measurement_plan_hash,
            receipt.w84_measurement_plan_hash,
            "W84 measurement plan hash",
        ),
        (
            final_verification.w84_admission_source_proof_hash,
            receipt.w84_admission_source_proof_hash,
            "W85 W84-source proof hash",
        ),
        (
            final_verification.w84_admission_source_verification_hash,
            receipt.w84_admission_source_verification_hash,
            "W85 admission source verification hash",
        ),
        (
            final_verification.w84_admission_source_capture_at,
            receipt.w84_admission_source_capture_at,
            "W85 source capture",
        ),
        (
            final_verification.w84_admission_source_verified_at,
            receipt.w84_admission_source_verified_at,
            "W85 source verified time",
        ),
        (
            final_verification.selected_trial_fingerprint,
            receipt.selected_trial_fingerprint,
            "selected trial fingerprint",
        ),
        (final_verification.strategy_spec_hash, receipt.strategy_spec_hash, "strategy spec hash"),
        (
            final_verification.loaded_runtime_code_hash,
            receipt.loaded_runtime_code_hash,
            "runtime code hash",
        ),
        (
            final_verification.fee_product_economics_hash,
            receipt.fee_product_economics_hash,
            "fee product economics hash",
        ),
        (final_verification.intent_fingerprint, receipt.intent_fingerprint, "intent fingerprint"),
        (final_verification.admitted_at, receipt.admitted_at, "admission time"),
        (final_verification.valid_until, receipt.valid_until, "valid until"),
    )
    for supplied, durable, label in checks:
        if supplied != durable:
            raise PaperRuntimeReadinessSourceIntegrityError(
                f"W85 final verification differs from durable {label}"
            )
    if final_verification.policy_hash != registration.policy.policy_hash:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "W85 final verification differs from durable policy registration"
        )


def _match_supplied_eligibility_to_current_durable(
    *,
    eligibility: PaperCandidateFinalEligibility,
    final_verification: PaperCandidateAdmissionFinalVerification,
    receipt: PaperCandidateAdmissionReceipt,
    state: PaperCandidateEligibilityState,
    head_hash: str,
    events_count: int,
) -> None:
    checks = (
        (eligibility.authority_key, receipt.authority_key, "authority key"),
        (eligibility.admission_id, receipt.admission_id, "admission id"),
        (eligibility.admission_hash, receipt.admission_hash, "admission hash"),
        (
            eligibility.final_admission_verification_hash,
            final_verification.verification_hash,
            "final admission verification hash",
        ),
        (
            eligibility.w84_admission_source_proof_hash,
            receipt.w84_admission_source_proof_hash,
            "W84 admission source-proof hash",
        ),
        (eligibility.admission_valid_until, receipt.valid_until, "candidate validity"),
        (eligibility.lifecycle_head_hash, head_hash, "lifecycle head"),
        (eligibility.lifecycle_events_count, events_count, "lifecycle event count"),
        (eligibility.state, state, "current lifecycle state"),
    )
    for supplied, durable, label in checks:
        if supplied != durable:
            raise PaperRuntimeReadinessSourceIntegrityError(
                f"supplied W85 eligibility is stale or differs from durable {label}"
            )
    expected_eligible = state is PaperCandidateEligibilityState.ACTIVE
    if eligibility.paper_candidate_currently_eligible is not expected_eligible:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "supplied W85 eligibility flag differs from durable current state"
        )


def _current_state(
    receipt: PaperCandidateAdmissionReceipt,
    events: tuple[PaperCandidateLifecycleEvent, ...],
    now: datetime,
) -> PaperCandidateEligibilityState:
    if receipt.valid_until is None:
        raise PaperRuntimeReadinessSourceIntegrityError(
            "W86 source requires finite candidate validity"
        )
    if _utc(now) > _utc(receipt.valid_until):
        return PaperCandidateEligibilityState.EXPIRED
    if not events:
        return PaperCandidateEligibilityState.ACTIVE
    action = events[-1].action
    if action is PaperCandidateLifecycleAction.REVOKE:
        return PaperCandidateEligibilityState.REVOKED
    if action is PaperCandidateLifecycleAction.SUSPEND:
        return PaperCandidateEligibilityState.SUSPENDED
    if action is PaperCandidateLifecycleAction.REINSTATE:
        return PaperCandidateEligibilityState.ACTIVE
    raise PaperRuntimeReadinessSourceIntegrityError(
        "durable W85 lifecycle cannot be projected"
    )


def _proof_payload(
    value: W85DurableEligibilitySourceProof, *, include_hash: bool
) -> dict[str, object]:
    names = tuple(
        field
        for field in W85DurableEligibilitySourceProof.__dataclass_fields__
        if field != "proof_hash"
    )
    payload = _proof_payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["proof_hash"] = value.proof_hash
    return payload


def _proof_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    state = payload["current_state"]
    if not isinstance(state, PaperCandidateEligibilityState):
        raise PaperRuntimeReadinessSourceIntegrityError("current_state type invalid")
    payload["current_state"] = state.value
    payload["probation_notional_cap_usd"] = str(payload["probation_notional_cap_usd"])
    for key in (
        "admission_valid_until",
        "supplied_eligibility_observed_at",
        "reproved_at",
    ):
        value = payload[key]
        if not isinstance(value, datetime):
            raise PaperRuntimeReadinessSourceIntegrityError(f"{key} type invalid")
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
        raise PaperRuntimeReadinessSourceIntegrityError(
            "W86 W85-source proof may not grant execution, capital or LIVE authority"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperRuntimeReadinessSourceIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeReadinessSourceIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperRuntimeReadinessSourceIntegrityError(
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
    "W85_DURABLE_ELIGIBILITY_SOURCE_VERSION",
    "PaperRuntimeReadinessSourceError",
    "PaperRuntimeReadinessSourceIntegrityError",
    "W85DurableEligibilitySourceProof",
    "W85DurableEligibilitySourceReader",
]
