from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from autotrade.persistence import SQLiteRuntime
import autotrade.promotion_cost_continuity as w81
import autotrade.promotion_fee_accounting as w82
import autotrade.promotion_shadow_forward_final_verification as w84
import autotrade.promotion_strategy_version_binding as w83
import autotrade.strategy_lab_promotion as w79
import autotrade.strategy_promotion_assessment as w80
from autotrade.paper_candidate_admission_source_verification import (
    PaperCandidateAdmissionSourceIntegrityError,
    W84AdmissionSourcePackage,
    W84AdmissionSourceProof,
    verify_w84_sources_for_candidate_admission,
)
from autotrade.promotion_cost_continuity import (
    PromotionCostContinuityResolution,
    PromotionCostContinuityStatus,
)
from autotrade.promotion_fee_accounting import (
    PromotionFeeAccountingResolution,
    PromotionFeeAccountingStatus,
)
from autotrade.promotion_shadow_forward_final_verification import (
    PromotionShadowForwardFinalVerification,
)
from autotrade.promotion_strategy_version_binding import PromotionStrategyVersionResolution
from autotrade.strategy_lab_promotion import StrategyPromotionPolicy
from autotrade.strategy_promotion_assessment import StrategyPromotionAssessmentReceipt


ADMISSION_POLICY_VERSION = "W85_PAPER_CANDIDATE_ADMISSION_POLICY_V2"
POLICY_REGISTRATION_VERSION = "W85_PAPER_CANDIDATE_POLICY_REGISTRATION_V2"
ADMISSION_RECEIPT_VERSION = "W85_PAPER_CANDIDATE_ADMISSION_RECEIPT_V2"
ZERO_ADMISSION_HASH = "0" * 64
MAX_FINALIZATION_AGE_SECONDS = 86_400
MAX_CANDIDATE_VALIDITY_SECONDS = 604_800
MAX_PROBATION_NOTIONAL_USD = Decimal("5")
MAX_PROBATION_ORDERS = 1
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class PaperCandidateAdmissionError(RuntimeError):
    pass


class PaperCandidateAdmissionIntegrityError(PaperCandidateAdmissionError):
    pass


class PaperCandidateAdmissionConflict(PaperCandidateAdmissionError):
    pass


class PaperCandidateAdmissionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class PaperCandidateAdmissionPolicy:
    """Frozen candidate-specific W85 admission governance.

    W79 owns scientific performance thresholds. W85 freezes only candidate
    identity, source-age/validity budgets and descriptive probation ceilings.
    `max_w84_finalization_age_seconds` is intentionally evaluated against the
    independently re-proved durable W84 measurement capture, never against a
    caller-provided/historical `process_verified_at` field.
    """

    policy_id: str
    contract_version: str
    promotion_policy_id: str
    promotion_policy_hash: str
    threshold_policy_hash: str
    selected_trial_id: str
    selected_trial_fingerprint: str
    selected_strategy_id: str
    selected_strategy_version: str
    strategy_spec_hash: str
    loaded_runtime_code_hash: str
    fee_product_economics_hash: str
    intent_fingerprint: str
    authority_key: str
    max_w84_finalization_age_seconds: int
    candidate_validity_seconds: int
    probation_notional_cap_usd: Decimal
    probation_order_cap: int
    probation_budget_is_execution_authority: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    policy_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("policy_id", self.policy_id),
            ("promotion_policy_id", self.promotion_policy_id),
            ("selected_trial_id", self.selected_trial_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
        ):
            _require_id(value, label)
        if self.contract_version != ADMISSION_POLICY_VERSION:
            raise PaperCandidateAdmissionIntegrityError(
                "admission policy version is not canonical W85"
            )
        for label, value in (
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("threshold_policy_hash", self.threshold_policy_hash),
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("loaded_runtime_code_hash", self.loaded_runtime_code_hash),
            ("fee_product_economics_hash", self.fee_product_economics_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("authority_key", self.authority_key),
            ("policy_hash", self.policy_hash),
        ):
            _require_hash(value, label)
        _require_positive_bounded_int(
            self.max_w84_finalization_age_seconds,
            MAX_FINALIZATION_AGE_SECONDS,
            "max_w84_finalization_age_seconds",
        )
        _require_positive_bounded_int(
            self.candidate_validity_seconds,
            MAX_CANDIDATE_VALIDITY_SECONDS,
            "candidate_validity_seconds",
        )
        _require_non_negative_decimal(
            self.probation_notional_cap_usd, "probation_notional_cap_usd"
        )
        if self.probation_notional_cap_usd > MAX_PROBATION_NOTIONAL_USD:
            raise PaperCandidateAdmissionIntegrityError(
                "probation notional cap exceeds canonical W85 ceiling"
            )
        _require_positive_bounded_int(
            self.probation_order_cap,
            MAX_PROBATION_ORDERS,
            "probation_order_cap",
        )
        if self.probation_budget_is_execution_authority is not False:
            raise PaperCandidateAdmissionIntegrityError(
                "W85 probation budget is descriptive only and grants no execution authority"
            )
        _require_no_execution_authority(
            paper_execution=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
            label="W85 admission policy",
        )
        if self.authority_key != _authority_key(
            promotion_policy_hash=self.promotion_policy_hash,
            selected_trial_fingerprint=self.selected_trial_fingerprint,
            strategy_spec_hash=self.strategy_spec_hash,
            loaded_runtime_code_hash=self.loaded_runtime_code_hash,
            fee_product_economics_hash=self.fee_product_economics_hash,
            intent_fingerprint=self.intent_fingerprint,
        ):
            raise PaperCandidateAdmissionIntegrityError(
                "admission policy authority_key does not match frozen candidate identity"
            )
        if self.policy_hash != _hash(_policy_payload(self, include_hash=False)):
            raise PaperCandidateAdmissionIntegrityError("admission policy hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _policy_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PaperCandidateAdmissionPolicyRegistration:
    registration_version: str
    policy: PaperCandidateAdmissionPolicy
    registered_at: datetime
    registration_hash: str

    def __post_init__(self) -> None:
        if self.registration_version != POLICY_REGISTRATION_VERSION:
            raise PaperCandidateAdmissionIntegrityError(
                "policy registration version is not canonical W85"
            )
        if not isinstance(self.policy, PaperCandidateAdmissionPolicy):
            raise PaperCandidateAdmissionIntegrityError(
                "policy registration requires PaperCandidateAdmissionPolicy"
            )
        _require_aware(self.registered_at, "registered_at")
        _require_hash(self.registration_hash, "registration_hash")
        if self.registration_hash != _hash(
            _registration_payload(self, include_hash=False)
        ):
            raise PaperCandidateAdmissionIntegrityError(
                "policy registration hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _registration_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PaperCandidateAdmissionReceipt:
    admission_id: str
    contract_version: str
    ordinal: int
    authority_key: str
    policy_id: str
    policy_hash: str
    policy_registration_hash: str
    promotion_policy_id: str
    promotion_policy_hash: str
    threshold_policy_hash: str
    w80_assessment_id: str
    w80_assessment_hash: str
    w81_resolution_id: str
    w81_resolution_hash: str
    w82_resolution_id: str
    w82_resolution_hash: str
    w83_resolution_id: str
    w83_resolution_hash: str
    w84_finalization_id: str | None
    w84_finalization_hash: str | None
    w84_source_verification_hash: str | None
    w84_measurement_plan_hash: str | None
    w84_admission_source_proof_hash: str | None
    w84_admission_source_verification_hash: str | None
    w84_admission_source_capture_at: datetime | None
    w84_admission_source_verified_at: datetime | None
    selected_trial_id: str
    selected_trial_fingerprint: str
    selected_strategy_id: str
    selected_strategy_version: str
    strategy_spec_hash: str
    loaded_runtime_code_hash: str
    fee_product_economics_hash: str
    intent_fingerprint: str
    previous_admission_hash: str
    status: PaperCandidateAdmissionStatus
    reason_codes: tuple[str, ...]
    admitted_at: datetime
    valid_until: datetime | None
    probation_notional_cap_usd: Decimal
    probation_order_cap: int
    probation_budget_is_execution_authority: bool
    paper_candidate_authorized: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    admission_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("admission_id", self.admission_id),
            ("policy_id", self.policy_id),
            ("promotion_policy_id", self.promotion_policy_id),
            ("w80_assessment_id", self.w80_assessment_id),
            ("w81_resolution_id", self.w81_resolution_id),
            ("w82_resolution_id", self.w82_resolution_id),
            ("w83_resolution_id", self.w83_resolution_id),
            ("selected_trial_id", self.selected_trial_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
        ):
            _require_id(value, label)
        if self.w84_finalization_id is not None:
            _require_id(self.w84_finalization_id, "w84_finalization_id")
        if self.contract_version != ADMISSION_RECEIPT_VERSION:
            raise PaperCandidateAdmissionIntegrityError(
                "admission receipt version is not canonical W85"
            )
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise PaperCandidateAdmissionIntegrityError(
                "admission ordinal must be integer >=1"
            )
        for label, value in (
            ("authority_key", self.authority_key),
            ("policy_hash", self.policy_hash),
            ("policy_registration_hash", self.policy_registration_hash),
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("threshold_policy_hash", self.threshold_policy_hash),
            ("w80_assessment_hash", self.w80_assessment_hash),
            ("w81_resolution_hash", self.w81_resolution_hash),
            ("w82_resolution_hash", self.w82_resolution_hash),
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("loaded_runtime_code_hash", self.loaded_runtime_code_hash),
            ("fee_product_economics_hash", self.fee_product_economics_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("previous_admission_hash", self.previous_admission_hash),
            ("admission_hash", self.admission_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("w84_finalization_hash", self.w84_finalization_hash),
            ("w84_source_verification_hash", self.w84_source_verification_hash),
            ("w84_measurement_plan_hash", self.w84_measurement_plan_hash),
            ("w84_admission_source_proof_hash", self.w84_admission_source_proof_hash),
            (
                "w84_admission_source_verification_hash",
                self.w84_admission_source_verification_hash,
            ),
        ):
            if value is not None:
                _require_hash(value, label)
        for label, value in (
            ("w84_admission_source_capture_at", self.w84_admission_source_capture_at),
            ("w84_admission_source_verified_at", self.w84_admission_source_verified_at),
        ):
            if value is not None:
                _require_aware(value, label)
        if not isinstance(self.status, PaperCandidateAdmissionStatus):
            raise PaperCandidateAdmissionIntegrityError("invalid W85 admission status")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise PaperCandidateAdmissionIntegrityError(
                "admission reason codes must be unique sorted"
            )
        if any(not isinstance(value, str) or not value.strip() for value in self.reason_codes):
            raise PaperCandidateAdmissionIntegrityError(
                "admission reason code is invalid"
            )
        _require_aware(self.admitted_at, "admitted_at")
        if self.valid_until is not None:
            _require_aware(self.valid_until, "valid_until")
            if _utc(self.valid_until) <= _utc(self.admitted_at):
                raise PaperCandidateAdmissionIntegrityError(
                    "candidate valid_until must follow admitted_at"
                )
        expected_candidate = self.status is PaperCandidateAdmissionStatus.PASS
        if self.paper_candidate_authorized is not expected_candidate:
            raise PaperCandidateAdmissionIntegrityError(
                "paper_candidate_authorized does not match admission status"
            )
        proof_values = (
            self.w84_admission_source_proof_hash,
            self.w84_admission_source_verification_hash,
            self.w84_admission_source_capture_at,
            self.w84_admission_source_verified_at,
        )
        if self.status is PaperCandidateAdmissionStatus.PASS:
            if self.reason_codes:
                raise PaperCandidateAdmissionIntegrityError(
                    "PASS admission may not carry failure reasons"
                )
            if self.valid_until is None:
                raise PaperCandidateAdmissionIntegrityError(
                    "PASS admission requires finite candidate validity"
                )
            if (
                self.w84_finalization_hash is None
                or self.w84_source_verification_hash is None
                or self.w84_measurement_plan_hash is None
                or any(value is None for value in proof_values)
            ):
                raise PaperCandidateAdmissionIntegrityError(
                    "PASS admission requires final W84 provenance plus admission-time source proof"
                )
            assert self.w84_admission_source_capture_at is not None
            assert self.w84_admission_source_verified_at is not None
            if _utc(self.w84_admission_source_verified_at) != _utc(self.admitted_at):
                raise PaperCandidateAdmissionIntegrityError(
                    "PASS admission source verification must use exact admission process clock"
                )
            if _utc(self.w84_admission_source_capture_at) > _utc(self.admitted_at):
                raise PaperCandidateAdmissionIntegrityError(
                    "PASS admission cannot predate durable source capture"
                )
        else:
            if not self.reason_codes:
                raise PaperCandidateAdmissionIntegrityError(
                    "non-PASS admission requires reason code"
                )
            if self.valid_until is not None:
                raise PaperCandidateAdmissionIntegrityError(
                    "non-PASS admission may not mint candidate validity"
                )
        _require_non_negative_decimal(
            self.probation_notional_cap_usd, "probation_notional_cap_usd"
        )
        if self.probation_notional_cap_usd > MAX_PROBATION_NOTIONAL_USD:
            raise PaperCandidateAdmissionIntegrityError(
                "receipt probation notional exceeds W85 ceiling"
            )
        _require_positive_bounded_int(
            self.probation_order_cap, MAX_PROBATION_ORDERS, "probation_order_cap"
        )
        if self.probation_budget_is_execution_authority is not False:
            raise PaperCandidateAdmissionIntegrityError(
                "probation budget cannot be execution authority"
            )
        _require_no_execution_authority(
            paper_execution=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
            label="W85 admission receipt",
        )
        if self.admission_hash != _hash(_receipt_payload(self, include_hash=False)):
            raise PaperCandidateAdmissionIntegrityError("admission receipt hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _receipt_payload(self, include_hash=True)


class SQLitePaperCandidateAdmissionRegistry:
    """Append-only W85 policy + admission journal on canonical core SQLite."""

    def __init__(self, runtime: SQLiteRuntime | str) -> None:
        self._runtime = runtime if isinstance(runtime, SQLiteRuntime) else SQLiteRuntime(runtime)
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            required = {
                "strategy_promotion_threshold_policies",
                "strategy_promotion_policies",
                "strategy_promotion_assessments",
            }
            if not required.issubset(tables):
                raise PaperCandidateAdmissionIntegrityError(
                    "W85 admission registry requires initialized W79/W80 promotion schema"
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_candidate_admission_policies (
                    policy_id TEXT PRIMARY KEY,
                    policy_hash TEXT NOT NULL UNIQUE,
                    authority_key TEXT NOT NULL UNIQUE,
                    promotion_policy_id TEXT NOT NULL,
                    promotion_policy_hash TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    registration_hash TEXT NOT NULL UNIQUE,
                    registration_json TEXT NOT NULL,
                    FOREIGN KEY(promotion_policy_id)
                        REFERENCES strategy_promotion_policies(policy_id)
                );

                CREATE TABLE IF NOT EXISTS paper_candidate_admissions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    admission_id TEXT NOT NULL UNIQUE,
                    admission_hash TEXT NOT NULL UNIQUE,
                    authority_key TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    previous_admission_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    admitted_at TEXT NOT NULL,
                    valid_until TEXT,
                    receipt_json TEXT NOT NULL,
                    FOREIGN KEY(policy_id)
                        REFERENCES paper_candidate_admission_policies(policy_id)
                );
                CREATE INDEX IF NOT EXISTS idx_paper_candidate_admissions_authority_sequence
                    ON paper_candidate_admissions(authority_key, sequence);
                """
            )
        finally:
            conn.close()

    def register_policy(
        self,
        policy: PaperCandidateAdmissionPolicy,
        *,
        promotion_policy: StrategyPromotionPolicy,
        w83_resolution: PromotionStrategyVersionResolution,
    ) -> PaperCandidateAdmissionPolicyRegistration:
        if not isinstance(policy, PaperCandidateAdmissionPolicy):
            raise TypeError("policy must be PaperCandidateAdmissionPolicy")
        _validate_w79_policy(promotion_policy)
        _validate_w83_resolution(w83_resolution)
        _require_policy_identity(
            policy=policy,
            promotion_policy=promotion_policy,
            w83_resolution=w83_resolution,
        )
        now = _now_utc()
        _require_aware(now, "policy registration process clock")

        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM paper_candidate_admission_policies WHERE policy_id = ?",
                (policy.policy_id,),
            ).fetchone()
            if row is not None:
                existing = _registration_from_row(row)
                if existing.policy != policy:
                    raise PaperCandidateAdmissionConflict(
                        f"admission policy identity conflict: {policy.policy_id}"
                    )
                conn.execute("COMMIT")
                return existing

            authority_row = conn.execute(
                "SELECT policy_id FROM paper_candidate_admission_policies WHERE authority_key = ?",
                (policy.authority_key,),
            ).fetchone()
            if authority_row is not None:
                raise PaperCandidateAdmissionConflict(
                    "exact candidate already has a different frozen W85 admission policy"
                )

            values = {
                "registration_version": POLICY_REGISTRATION_VERSION,
                "policy": policy,
                "registered_at": now,
            }
            registration = PaperCandidateAdmissionPolicyRegistration(
                **values,
                registration_hash=_hash(_registration_payload_from_values(values)),
            )
            conn.execute(
                """
                INSERT INTO paper_candidate_admission_policies(
                    policy_id, policy_hash, authority_key,
                    promotion_policy_id, promotion_policy_hash,
                    registered_at, registration_hash, registration_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy.policy_id,
                    policy.policy_hash,
                    policy.authority_key,
                    policy.promotion_policy_id,
                    policy.promotion_policy_hash,
                    _utc(now).isoformat(),
                    registration.registration_hash,
                    _canonical_json(registration.to_dict()),
                ),
            )
            conn.execute("COMMIT")
            return registration
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def assess_and_record(
        self,
        *,
        admission_id: str,
        policy_id: str,
        promotion_policy: StrategyPromotionPolicy,
        w80_assessment: StrategyPromotionAssessmentReceipt,
        w81_resolution: PromotionCostContinuityResolution,
        w82_resolution: PromotionFeeAccountingResolution,
        w83_resolution: PromotionStrategyVersionResolution,
        w84_finalization: PromotionShadowForwardFinalVerification | None,
        w84_source_package: W84AdmissionSourcePackage | None = None,
    ) -> PaperCandidateAdmissionReceipt:
        _require_id(admission_id, "admission_id")
        _require_id(policy_id, "policy_id")
        registration = self.get_policy(policy_id)
        if registration is None:
            raise PaperCandidateAdmissionIntegrityError(
                f"unknown W85 admission policy: {policy_id}"
            )
        _validate_chain(
            policy=registration.policy,
            promotion_policy=promotion_policy,
            w80_assessment=w80_assessment,
            w81_resolution=w81_resolution,
            w82_resolution=w82_resolution,
            w83_resolution=w83_resolution,
            w84_finalization=w84_finalization,
        )
        now = _now_utc()
        _require_aware(now, "admission process clock")
        if _utc(now) <= _utc(registration.registered_at):
            raise PaperCandidateAdmissionIntegrityError(
                "admission decision must occur strictly after frozen policy registration"
            )

        source_proof: W84AdmissionSourceProof | None = None
        if w84_finalization is not None and w84_source_package is not None:
            try:
                source_proof = verify_w84_sources_for_candidate_admission(
                    proof_id=f"{admission_id}:w84-source-reproof",
                    finalization=w84_finalization,
                    w83_resolution=w83_resolution,
                    source_package=w84_source_package,
                    verified_at=now,
                )
            except (TypeError, PaperCandidateAdmissionSourceIntegrityError) as exc:
                raise PaperCandidateAdmissionIntegrityError(
                    "W85 admission could not independently re-prove W84 durable sources"
                ) from exc

        status, reasons = _decision(
            policy=registration.policy,
            w80_assessment=w80_assessment,
            w81_resolution=w81_resolution,
            w82_resolution=w82_resolution,
            w83_resolution=w83_resolution,
            w84_finalization=w84_finalization,
            source_proof=source_proof,
        )
        valid_until = (
            now + timedelta(seconds=registration.policy.candidate_validity_seconds)
            if status is PaperCandidateAdmissionStatus.PASS
            else None
        )

        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_row = conn.execute(
                "SELECT * FROM paper_candidate_admissions WHERE admission_id = ?",
                (admission_id,),
            ).fetchone()
            if existing_row is not None:
                existing = _receipt_from_row(existing_row)
                expected_w84_hash = (
                    w84_finalization.finalization_hash
                    if w84_finalization is not None
                    else None
                )
                if (
                    existing.policy_id != policy_id
                    or existing.w80_assessment_hash != w80_assessment.assessment_hash
                    or existing.w81_resolution_hash != w81_resolution.resolution_hash
                    or existing.w82_resolution_hash != w82_resolution.resolution_hash
                    or existing.w83_resolution_hash != w83_resolution.resolution_hash
                    or existing.w84_finalization_hash != expected_w84_hash
                ):
                    raise PaperCandidateAdmissionConflict(
                        f"admission identity conflict: {admission_id}"
                    )
                conn.execute("COMMIT")
                return existing

            previous_row = conn.execute(
                """
                SELECT * FROM paper_candidate_admissions
                WHERE authority_key = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (registration.policy.authority_key,),
            ).fetchone()
            previous = _receipt_from_row(previous_row) if previous_row is not None else None
            if previous is not None:
                if _utc(now) <= _utc(previous.admitted_at):
                    raise PaperCandidateAdmissionIntegrityError(
                        "admission journal timestamps must advance monotonically"
                    )
                if (
                    previous.status is PaperCandidateAdmissionStatus.PASS
                    and previous.valid_until is not None
                    and _utc(now) <= _utc(previous.valid_until)
                ):
                    raise PaperCandidateAdmissionConflict(
                        "candidate already has an active W85 admission"
                    )
                current_w84_hash = (
                    w84_finalization.finalization_hash
                    if w84_finalization is not None
                    else None
                )
                if (
                    previous.status is status
                    and previous.w84_finalization_hash == current_w84_hash
                    and previous.policy_hash == registration.policy.policy_hash
                    and previous.status is not PaperCandidateAdmissionStatus.PASS
                ):
                    raise PaperCandidateAdmissionConflict(
                        "unchanged non-PASS admission state may not be appended under a new id"
                    )

            values = {
                "admission_id": admission_id,
                "contract_version": ADMISSION_RECEIPT_VERSION,
                "ordinal": previous.ordinal + 1 if previous is not None else 1,
                "authority_key": registration.policy.authority_key,
                "policy_id": registration.policy.policy_id,
                "policy_hash": registration.policy.policy_hash,
                "policy_registration_hash": registration.registration_hash,
                "promotion_policy_id": promotion_policy.policy_id,
                "promotion_policy_hash": promotion_policy.policy_hash,
                "threshold_policy_hash": promotion_policy.threshold_policy_hash,
                "w80_assessment_id": w80_assessment.assessment_id,
                "w80_assessment_hash": w80_assessment.assessment_hash,
                "w81_resolution_id": w81_resolution.resolution_id,
                "w81_resolution_hash": w81_resolution.resolution_hash,
                "w82_resolution_id": w82_resolution.resolution_id,
                "w82_resolution_hash": w82_resolution.resolution_hash,
                "w83_resolution_id": w83_resolution.resolution_id,
                "w83_resolution_hash": w83_resolution.resolution_hash,
                "w84_finalization_id": (
                    w84_finalization.finalization_id if w84_finalization is not None else None
                ),
                "w84_finalization_hash": (
                    w84_finalization.finalization_hash if w84_finalization is not None else None
                ),
                "w84_source_verification_hash": (
                    w84_finalization.source_verification_hash
                    if w84_finalization is not None
                    else None
                ),
                "w84_measurement_plan_hash": (
                    w84_finalization.measurement_plan_hash
                    if w84_finalization is not None
                    else None
                ),
                "w84_admission_source_proof_hash": (
                    source_proof.proof_hash if source_proof is not None else None
                ),
                "w84_admission_source_verification_hash": (
                    source_proof.admission_source_verification_hash
                    if source_proof is not None
                    else None
                ),
                "w84_admission_source_capture_at": (
                    source_proof.source_capture_at if source_proof is not None else None
                ),
                "w84_admission_source_verified_at": (
                    source_proof.verified_at if source_proof is not None else None
                ),
                "selected_trial_id": w83_resolution.selected_trial_id,
                "selected_trial_fingerprint": w83_resolution.selected_trial_fingerprint,
                "selected_strategy_id": w83_resolution.selected_strategy_id,
                "selected_strategy_version": w83_resolution.selected_strategy_version,
                "strategy_spec_hash": w83_resolution.strategy_spec_hash,
                "loaded_runtime_code_hash": w83_resolution.loaded_runtime_code_hash,
                "fee_product_economics_hash": w83_resolution.fee_product_economics_hash,
                "intent_fingerprint": w83_resolution.intent_fingerprint,
                "previous_admission_hash": (
                    previous.admission_hash if previous is not None else ZERO_ADMISSION_HASH
                ),
                "status": status,
                "reason_codes": reasons,
                "admitted_at": now,
                "valid_until": valid_until,
                "probation_notional_cap_usd": registration.policy.probation_notional_cap_usd,
                "probation_order_cap": registration.policy.probation_order_cap,
                "probation_budget_is_execution_authority": False,
                "paper_candidate_authorized": status is PaperCandidateAdmissionStatus.PASS,
                "paper_execution_authorized": False,
                "external_execution_authorized": False,
                "runtime_execution_authorized": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
            }
            receipt = PaperCandidateAdmissionReceipt(
                **values,
                admission_hash=_hash(_receipt_payload_from_values(values)),
            )
            conn.execute(
                """
                INSERT INTO paper_candidate_admissions(
                    admission_id, admission_hash, authority_key,
                    policy_id, policy_hash, previous_admission_hash,
                    status, admitted_at, valid_until, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.admission_id,
                    receipt.admission_hash,
                    receipt.authority_key,
                    receipt.policy_id,
                    receipt.policy_hash,
                    receipt.previous_admission_hash,
                    receipt.status.value,
                    _utc(receipt.admitted_at).isoformat(),
                    _utc(receipt.valid_until).isoformat()
                    if receipt.valid_until is not None
                    else None,
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

    def get_policy(
        self, policy_id: str
    ) -> PaperCandidateAdmissionPolicyRegistration | None:
        _require_id(policy_id, "policy_id")
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT * FROM paper_candidate_admission_policies WHERE policy_id = ?",
                (policy_id,),
            ).fetchone()
            return _registration_from_row(row) if row is not None else None
        finally:
            conn.close()

    def get(self, admission_id: str) -> PaperCandidateAdmissionReceipt | None:
        _require_id(admission_id, "admission_id")
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT * FROM paper_candidate_admissions WHERE admission_id = ?",
                (admission_id,),
            ).fetchone()
            return _receipt_from_row(row) if row is not None else None
        finally:
            conn.close()

    def list_for_authority(self, authority_key: str) -> tuple[PaperCandidateAdmissionReceipt, ...]:
        _require_hash(authority_key, "authority_key")
        conn = self._runtime.connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM paper_candidate_admissions
                WHERE authority_key = ?
                ORDER BY sequence
                """,
                (authority_key,),
            ).fetchall()
        finally:
            conn.close()
        receipts = tuple(_receipt_from_row(row) for row in rows)
        _validate_admission_chain(receipts)
        return receipts


def build_paper_candidate_admission_policy(
    *,
    policy_id: str,
    promotion_policy: StrategyPromotionPolicy,
    w83_resolution: PromotionStrategyVersionResolution,
    max_w84_finalization_age_seconds: int = 3_600,
    candidate_validity_seconds: int = 86_400,
    probation_notional_cap_usd: Decimal = Decimal("5"),
    probation_order_cap: int = 1,
) -> PaperCandidateAdmissionPolicy:
    """Build bounded W85 admission governance without post-outcome science knobs."""

    _require_id(policy_id, "policy_id")
    _validate_w79_policy(promotion_policy)
    _validate_w83_resolution(w83_resolution)
    if (
        promotion_policy.policy_id != w83_resolution.promotion_policy_id
        or promotion_policy.policy_hash != w83_resolution.promotion_policy_hash
        or promotion_policy.selected_trial_id != w83_resolution.selected_trial_id
        or promotion_policy.selected_trial_fingerprint
        != w83_resolution.selected_trial_fingerprint
        or promotion_policy.selected_strategy_id != w83_resolution.selected_strategy_id
        or promotion_policy.selected_strategy_version
        != w83_resolution.selected_strategy_version
    ):
        raise PaperCandidateAdmissionIntegrityError(
            "W79 policy and W83 resolution do not identify the same frozen candidate"
        )
    values = {
        "policy_id": policy_id,
        "contract_version": ADMISSION_POLICY_VERSION,
        "promotion_policy_id": promotion_policy.policy_id,
        "promotion_policy_hash": promotion_policy.policy_hash,
        "threshold_policy_hash": promotion_policy.threshold_policy_hash,
        "selected_trial_id": w83_resolution.selected_trial_id,
        "selected_trial_fingerprint": w83_resolution.selected_trial_fingerprint,
        "selected_strategy_id": w83_resolution.selected_strategy_id,
        "selected_strategy_version": w83_resolution.selected_strategy_version,
        "strategy_spec_hash": w83_resolution.strategy_spec_hash,
        "loaded_runtime_code_hash": w83_resolution.loaded_runtime_code_hash,
        "fee_product_economics_hash": w83_resolution.fee_product_economics_hash,
        "intent_fingerprint": w83_resolution.intent_fingerprint,
        "authority_key": _authority_key(
            promotion_policy_hash=promotion_policy.policy_hash,
            selected_trial_fingerprint=w83_resolution.selected_trial_fingerprint,
            strategy_spec_hash=w83_resolution.strategy_spec_hash,
            loaded_runtime_code_hash=w83_resolution.loaded_runtime_code_hash,
            fee_product_economics_hash=w83_resolution.fee_product_economics_hash,
            intent_fingerprint=w83_resolution.intent_fingerprint,
        ),
        "max_w84_finalization_age_seconds": max_w84_finalization_age_seconds,
        "candidate_validity_seconds": candidate_validity_seconds,
        "probation_notional_cap_usd": probation_notional_cap_usd,
        "probation_order_cap": probation_order_cap,
        "probation_budget_is_execution_authority": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PaperCandidateAdmissionPolicy(
        **values,
        policy_hash=_hash(_policy_payload_from_values(values)),
    )


def _decision(
    *,
    policy: PaperCandidateAdmissionPolicy,
    w80_assessment: StrategyPromotionAssessmentReceipt,
    w81_resolution: PromotionCostContinuityResolution,
    w82_resolution: PromotionFeeAccountingResolution,
    w83_resolution: PromotionStrategyVersionResolution,
    w84_finalization: PromotionShadowForwardFinalVerification | None,
    source_proof: W84AdmissionSourceProof | None,
) -> tuple[PaperCandidateAdmissionStatus, tuple[str, ...]]:
    if w84_finalization is None:
        return (
            PaperCandidateAdmissionStatus.INCOMPLETE,
            ("W84_FINAL_VERIFICATION_MISSING",),
        )
    if source_proof is None:
        return (
            PaperCandidateAdmissionStatus.INCOMPLETE,
            ("W84_ADMISSION_SOURCE_PROOF_MISSING",),
        )
    if source_proof.finalization_hash != w84_finalization.finalization_hash:
        raise PaperCandidateAdmissionIntegrityError(
            "W85 source proof does not bind exact W84 finalization"
        )

    reasons: list[str] = []
    if w80_assessment.assessment_state is not w79.PromotionAssessmentState.EVIDENCE_QUALIFIED:
        reasons.append("W80_EVIDENCE_NOT_QUALIFIED")
    if w80_assessment.evidence_complete is not True:
        reasons.append("W80_EVIDENCE_INCOMPLETE")
    if w81_resolution.status is not PromotionCostContinuityStatus.PASS:
        reasons.append("W81_COST_CONTINUITY_NOT_PASS")
    if w82_resolution.status is not PromotionFeeAccountingStatus.PASS:
        reasons.append("W82_FEE_ACCOUNTING_NOT_PASS")
    if w84_finalization.remaining_promotion_blockers:
        reasons.append("PROMOTION_BLOCKERS_REMAIN")
    if source_proof.source_truth_verified is not True:
        reasons.append("W84_DURABLE_SOURCE_NOT_VERIFIED")
    if source_proof.historical_finalization_timestamp_trusted_for_freshness is not False:
        reasons.append("W84_HISTORICAL_TIMESTAMP_TRUSTED")
    if source_proof.source_age_seconds > policy.max_w84_finalization_age_seconds:
        reasons.append("W84_DURABLE_SOURCE_STALE")
    if reasons:
        return PaperCandidateAdmissionStatus.BLOCKED, tuple(sorted(set(reasons)))
    return PaperCandidateAdmissionStatus.PASS, ()


def _validate_chain(
    *,
    policy: PaperCandidateAdmissionPolicy,
    promotion_policy: StrategyPromotionPolicy,
    w80_assessment: StrategyPromotionAssessmentReceipt,
    w81_resolution: PromotionCostContinuityResolution,
    w82_resolution: PromotionFeeAccountingResolution,
    w83_resolution: PromotionStrategyVersionResolution,
    w84_finalization: PromotionShadowForwardFinalVerification | None,
) -> None:
    _validate_w79_policy(promotion_policy)
    _validate_w80_assessment(w80_assessment)
    _validate_w81_resolution(w81_resolution)
    _validate_w82_resolution(w82_resolution)
    _validate_w83_resolution(w83_resolution)
    _require_policy_identity(
        policy=policy,
        promotion_policy=promotion_policy,
        w83_resolution=w83_resolution,
    )
    if (
        w80_assessment.policy_id != promotion_policy.policy_id
        or w80_assessment.policy_hash != promotion_policy.policy_hash
        or w80_assessment.threshold_policy_hash != promotion_policy.threshold_policy_hash
        or w80_assessment.selected_strategy_id != promotion_policy.selected_strategy_id
        or w80_assessment.selected_strategy_version != promotion_policy.selected_strategy_version
    ):
        raise PaperCandidateAdmissionIntegrityError(
            "W80 assessment does not match exact frozen W79 promotion policy"
        )
    if (
        w81_resolution.promotion_assessment_id != w80_assessment.assessment_id
        or w81_resolution.promotion_assessment_hash != w80_assessment.assessment_hash
        or w81_resolution.promotion_policy_id != promotion_policy.policy_id
        or w81_resolution.promotion_policy_hash != promotion_policy.policy_hash
        or w81_resolution.selected_strategy_id != promotion_policy.selected_strategy_id
        or w81_resolution.selected_strategy_version != promotion_policy.selected_strategy_version
    ):
        raise PaperCandidateAdmissionIntegrityError(
            "W81 resolution does not continue exact W79/W80 candidate chain"
        )
    if (
        w82_resolution.w81_resolution_id != w81_resolution.resolution_id
        or w82_resolution.w81_resolution_hash != w81_resolution.resolution_hash
        or w82_resolution.promotion_assessment_id != w80_assessment.assessment_id
        or w82_resolution.promotion_assessment_hash != w80_assessment.assessment_hash
        or w82_resolution.promotion_policy_id != promotion_policy.policy_id
        or w82_resolution.promotion_policy_hash != promotion_policy.policy_hash
    ):
        raise PaperCandidateAdmissionIntegrityError(
            "W82 resolution does not continue exact W80/W81 chain"
        )
    if (
        w83_resolution.w82_resolution_id != w82_resolution.resolution_id
        or w83_resolution.w82_resolution_hash != w82_resolution.resolution_hash
        or w83_resolution.promotion_policy_id != promotion_policy.policy_id
        or w83_resolution.promotion_policy_hash != promotion_policy.policy_hash
        or w83_resolution.selected_strategy_id != promotion_policy.selected_strategy_id
        or w83_resolution.selected_strategy_version != promotion_policy.selected_strategy_version
    ):
        raise PaperCandidateAdmissionIntegrityError(
            "W83 resolution does not continue exact W82 candidate chain"
        )
    if w84_finalization is not None:
        _validate_w84_finalization(w84_finalization)
        if w84_finalization.w83_resolution_hash != w83_resolution.resolution_hash:
            raise PaperCandidateAdmissionIntegrityError(
                "W84 finalization does not bind exact W83 resolution"
            )


def _validate_w79_policy(value: StrategyPromotionPolicy) -> None:
    if not isinstance(value, StrategyPromotionPolicy):
        raise TypeError("promotion_policy must be StrategyPromotionPolicy")
    expected = w79._hash(w79._policy_payload(value, include_hash=False))
    if value.policy_hash != expected:
        raise PaperCandidateAdmissionIntegrityError("W79 promotion policy hash mismatch")
    if value.external_execution_authorized is not False or value.live_trading != "BLOCKED":
        raise PaperCandidateAdmissionIntegrityError("W79 authority boundary is not intact")


def _validate_w80_assessment(value: StrategyPromotionAssessmentReceipt) -> None:
    if not isinstance(value, StrategyPromotionAssessmentReceipt):
        raise TypeError("w80_assessment must be StrategyPromotionAssessmentReceipt")
    expected = w80._hash(w80._receipt_payload(value, include_hash=False))
    if value.assessment_hash != expected:
        raise PaperCandidateAdmissionIntegrityError("W80 assessment hash mismatch")
    if (
        value.paper_candidate_authorized is not False
        or value.external_execution_authorized is not False
        or value.capital_authority != "NONE"
        or value.live_trading != "BLOCKED"
    ):
        raise PaperCandidateAdmissionIntegrityError("W80 authority boundary is not intact")


def _validate_w81_resolution(value: PromotionCostContinuityResolution) -> None:
    if not isinstance(value, PromotionCostContinuityResolution):
        raise TypeError("w81_resolution must be PromotionCostContinuityResolution")
    expected = w81._hash(w81._payload(value, include_hash=False))
    if value.resolution_hash != expected:
        raise PaperCandidateAdmissionIntegrityError("W81 resolution hash mismatch")
    if (
        value.paper_candidate_authorized is not False
        or value.external_execution_authorized is not False
        or value.capital_authority != "NONE"
        or value.live_trading != "BLOCKED"
    ):
        raise PaperCandidateAdmissionIntegrityError("W81 authority boundary is not intact")


def _validate_w82_resolution(value: PromotionFeeAccountingResolution) -> None:
    if not isinstance(value, PromotionFeeAccountingResolution):
        raise TypeError("w82_resolution must be PromotionFeeAccountingResolution")
    expected = w82._hash(w82._payload(value, include_hash=False))
    if value.resolution_hash != expected:
        raise PaperCandidateAdmissionIntegrityError("W82 resolution hash mismatch")
    if (
        value.broker_authoritative_fee_proven is not False
        or value.realized_profitability_authorized is not False
        or value.paper_candidate_authorized is not False
        or value.external_execution_authorized is not False
        or value.capital_authority != "NONE"
        or value.live_trading != "BLOCKED"
    ):
        raise PaperCandidateAdmissionIntegrityError(
            "W82 authority/no-claims boundary is not intact"
        )


def _validate_w83_resolution(value: PromotionStrategyVersionResolution) -> None:
    if not isinstance(value, PromotionStrategyVersionResolution):
        raise TypeError("w83_resolution must be PromotionStrategyVersionResolution")
    expected = w83._hash(w83._payload(value, include_hash=False))
    if value.resolution_hash != expected:
        raise PaperCandidateAdmissionIntegrityError("W83 resolution hash mismatch")
    if (
        value.strategy_version_execution_bound is not True
        or value.shadow_forward_promotion_bound is not False
        or value.paper_candidate_authorized is not False
        or value.external_execution_authorized is not False
        or value.runtime_execution_authorized is not False
        or value.capital_authority != "NONE"
        or value.live_trading != "BLOCKED"
    ):
        raise PaperCandidateAdmissionIntegrityError("W83 authority boundary is not intact")


def _validate_w84_finalization(value: PromotionShadowForwardFinalVerification) -> None:
    if not isinstance(value, PromotionShadowForwardFinalVerification):
        raise TypeError("w84_finalization must be PromotionShadowForwardFinalVerification")
    expected = w84._hash(w84._payload(value, include_hash=False))
    if value.finalization_hash != expected:
        raise PaperCandidateAdmissionIntegrityError("W84 finalization hash mismatch")
    if (
        value.source_truth_verified is not True
        or value.process_clock_freshness_verified is not True
        or value.strategy_version_execution_bound is not True
        or value.shadow_forward_promotion_bound is not True
        or value.paper_candidate_authorized is not False
        or value.external_execution_authorized is not False
        or value.runtime_execution_authorized is not False
        or value.capital_authority != "NONE"
        or value.live_trading != "BLOCKED"
    ):
        raise PaperCandidateAdmissionIntegrityError(
            "W84 final source/process/authority boundary is not intact"
        )


def _require_policy_identity(
    *,
    policy: PaperCandidateAdmissionPolicy,
    promotion_policy: StrategyPromotionPolicy,
    w83_resolution: PromotionStrategyVersionResolution,
) -> None:
    if (
        policy.promotion_policy_id != promotion_policy.policy_id
        or policy.promotion_policy_hash != promotion_policy.policy_hash
        or policy.threshold_policy_hash != promotion_policy.threshold_policy_hash
        or policy.selected_trial_id != w83_resolution.selected_trial_id
        or policy.selected_trial_fingerprint != w83_resolution.selected_trial_fingerprint
        or policy.selected_strategy_id != w83_resolution.selected_strategy_id
        or policy.selected_strategy_version != w83_resolution.selected_strategy_version
        or policy.strategy_spec_hash != w83_resolution.strategy_spec_hash
        or policy.loaded_runtime_code_hash != w83_resolution.loaded_runtime_code_hash
        or policy.fee_product_economics_hash != w83_resolution.fee_product_economics_hash
        or policy.intent_fingerprint != w83_resolution.intent_fingerprint
    ):
        raise PaperCandidateAdmissionIntegrityError(
            "W85 policy does not bind exact W79/W83 candidate identity"
        )


def _validate_admission_chain(receipts: tuple[PaperCandidateAdmissionReceipt, ...]) -> None:
    previous: PaperCandidateAdmissionReceipt | None = None
    for expected_ordinal, receipt in enumerate(receipts, start=1):
        if receipt.ordinal != expected_ordinal:
            raise PaperCandidateAdmissionIntegrityError(
                "admission journal ordinal discontinuity"
            )
        expected_previous = (
            previous.admission_hash if previous is not None else ZERO_ADMISSION_HASH
        )
        if receipt.previous_admission_hash != expected_previous:
            raise PaperCandidateAdmissionIntegrityError(
                "admission journal predecessor hash discontinuity"
            )
        if previous is not None:
            if receipt.authority_key != previous.authority_key:
                raise PaperCandidateAdmissionIntegrityError(
                    "admission journal changed authority key"
                )
            if _utc(receipt.admitted_at) <= _utc(previous.admitted_at):
                raise PaperCandidateAdmissionIntegrityError(
                    "admission journal timestamp regression"
                )
        previous = receipt


def _registration_from_row(row: sqlite3.Row) -> PaperCandidateAdmissionPolicyRegistration:
    raw = row["registration_json"]
    if not isinstance(raw, str):
        raise PaperCandidateAdmissionIntegrityError("policy registration JSON must be text")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PaperCandidateAdmissionIntegrityError("policy registration JSON invalid") from exc
    registration = _registration_from_dict(payload)
    expected = {
        "policy_id": registration.policy.policy_id,
        "policy_hash": registration.policy.policy_hash,
        "authority_key": registration.policy.authority_key,
        "promotion_policy_id": registration.policy.promotion_policy_id,
        "promotion_policy_hash": registration.policy.promotion_policy_hash,
        "registered_at": _utc(registration.registered_at).isoformat(),
        "registration_hash": registration.registration_hash,
        "registration_json": _canonical_json(registration.to_dict()),
    }
    for key, expected_value in expected.items():
        if str(row[key]) != expected_value:
            raise PaperCandidateAdmissionIntegrityError(
                f"W85 policy SQLite column mismatch: {key}"
            )
    return registration


def _receipt_from_row(row: sqlite3.Row) -> PaperCandidateAdmissionReceipt:
    raw = row["receipt_json"]
    if not isinstance(raw, str):
        raise PaperCandidateAdmissionIntegrityError("admission receipt JSON must be text")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PaperCandidateAdmissionIntegrityError("admission receipt JSON invalid") from exc
    receipt = _receipt_from_dict(payload)
    expected = {
        "admission_id": receipt.admission_id,
        "admission_hash": receipt.admission_hash,
        "authority_key": receipt.authority_key,
        "policy_id": receipt.policy_id,
        "policy_hash": receipt.policy_hash,
        "previous_admission_hash": receipt.previous_admission_hash,
        "status": receipt.status.value,
        "admitted_at": _utc(receipt.admitted_at).isoformat(),
        "valid_until": (
            _utc(receipt.valid_until).isoformat()
            if receipt.valid_until is not None
            else None
        ),
        "receipt_json": _canonical_json(receipt.to_dict()),
    }
    for key, expected_value in expected.items():
        actual = row[key]
        if actual is None and expected_value is None:
            continue
        if str(actual) != str(expected_value):
            raise PaperCandidateAdmissionIntegrityError(
                f"W85 admission SQLite column mismatch: {key}"
            )
    return receipt


def _registration_from_dict(value: object) -> PaperCandidateAdmissionPolicyRegistration:
    if not isinstance(value, dict):
        raise PaperCandidateAdmissionIntegrityError("policy registration must be object")
    policy_raw = value.get("policy")
    if not isinstance(policy_raw, dict):
        raise PaperCandidateAdmissionIntegrityError("registration policy must be object")
    registered_raw = _string(value, "registered_at")
    try:
        registered_at = datetime.fromisoformat(registered_raw)
    except ValueError as exc:
        raise PaperCandidateAdmissionIntegrityError("registered_at invalid") from exc
    return PaperCandidateAdmissionPolicyRegistration(
        registration_version=_string(value, "registration_version"),
        policy=_policy_from_dict(policy_raw),
        registered_at=registered_at,
        registration_hash=_string(value, "registration_hash"),
    )


def _policy_from_dict(value: dict[str, object]) -> PaperCandidateAdmissionPolicy:
    return PaperCandidateAdmissionPolicy(
        policy_id=_string(value, "policy_id"),
        contract_version=_string(value, "contract_version"),
        promotion_policy_id=_string(value, "promotion_policy_id"),
        promotion_policy_hash=_string(value, "promotion_policy_hash"),
        threshold_policy_hash=_string(value, "threshold_policy_hash"),
        selected_trial_id=_string(value, "selected_trial_id"),
        selected_trial_fingerprint=_string(value, "selected_trial_fingerprint"),
        selected_strategy_id=_string(value, "selected_strategy_id"),
        selected_strategy_version=_string(value, "selected_strategy_version"),
        strategy_spec_hash=_string(value, "strategy_spec_hash"),
        loaded_runtime_code_hash=_string(value, "loaded_runtime_code_hash"),
        fee_product_economics_hash=_string(value, "fee_product_economics_hash"),
        intent_fingerprint=_string(value, "intent_fingerprint"),
        authority_key=_string(value, "authority_key"),
        max_w84_finalization_age_seconds=_integer(
            value, "max_w84_finalization_age_seconds"
        ),
        candidate_validity_seconds=_integer(value, "candidate_validity_seconds"),
        probation_notional_cap_usd=Decimal(_string(value, "probation_notional_cap_usd")),
        probation_order_cap=_integer(value, "probation_order_cap"),
        probation_budget_is_execution_authority=_boolean(
            value, "probation_budget_is_execution_authority"
        ),
        paper_execution_authorized=_boolean(value, "paper_execution_authorized"),
        external_execution_authorized=_boolean(value, "external_execution_authorized"),
        runtime_execution_authorized=_boolean(value, "runtime_execution_authorized"),
        capital_authority=_string(value, "capital_authority"),
        live_trading=_string(value, "live_trading"),
        policy_hash=_string(value, "policy_hash"),
    )


def _receipt_from_dict(value: object) -> PaperCandidateAdmissionReceipt:
    if not isinstance(value, dict):
        raise PaperCandidateAdmissionIntegrityError("admission receipt must be object")
    try:
        status = PaperCandidateAdmissionStatus(_string(value, "status"))
    except ValueError as exc:
        raise PaperCandidateAdmissionIntegrityError("admission status invalid") from exc
    admitted_at = _datetime_or_error(value, "admitted_at")
    valid_until = _optional_datetime(value, "valid_until")
    reasons = value.get("reason_codes")
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        raise PaperCandidateAdmissionIntegrityError("reason_codes must be string list")
    return PaperCandidateAdmissionReceipt(
        admission_id=_string(value, "admission_id"),
        contract_version=_string(value, "contract_version"),
        ordinal=_integer(value, "ordinal"),
        authority_key=_string(value, "authority_key"),
        policy_id=_string(value, "policy_id"),
        policy_hash=_string(value, "policy_hash"),
        policy_registration_hash=_string(value, "policy_registration_hash"),
        promotion_policy_id=_string(value, "promotion_policy_id"),
        promotion_policy_hash=_string(value, "promotion_policy_hash"),
        threshold_policy_hash=_string(value, "threshold_policy_hash"),
        w80_assessment_id=_string(value, "w80_assessment_id"),
        w80_assessment_hash=_string(value, "w80_assessment_hash"),
        w81_resolution_id=_string(value, "w81_resolution_id"),
        w81_resolution_hash=_string(value, "w81_resolution_hash"),
        w82_resolution_id=_string(value, "w82_resolution_id"),
        w82_resolution_hash=_string(value, "w82_resolution_hash"),
        w83_resolution_id=_string(value, "w83_resolution_id"),
        w83_resolution_hash=_string(value, "w83_resolution_hash"),
        w84_finalization_id=_optional_string(value, "w84_finalization_id"),
        w84_finalization_hash=_optional_string(value, "w84_finalization_hash"),
        w84_source_verification_hash=_optional_string(
            value, "w84_source_verification_hash"
        ),
        w84_measurement_plan_hash=_optional_string(value, "w84_measurement_plan_hash"),
        w84_admission_source_proof_hash=_optional_string(
            value, "w84_admission_source_proof_hash"
        ),
        w84_admission_source_verification_hash=_optional_string(
            value, "w84_admission_source_verification_hash"
        ),
        w84_admission_source_capture_at=_optional_datetime(
            value, "w84_admission_source_capture_at"
        ),
        w84_admission_source_verified_at=_optional_datetime(
            value, "w84_admission_source_verified_at"
        ),
        selected_trial_id=_string(value, "selected_trial_id"),
        selected_trial_fingerprint=_string(value, "selected_trial_fingerprint"),
        selected_strategy_id=_string(value, "selected_strategy_id"),
        selected_strategy_version=_string(value, "selected_strategy_version"),
        strategy_spec_hash=_string(value, "strategy_spec_hash"),
        loaded_runtime_code_hash=_string(value, "loaded_runtime_code_hash"),
        fee_product_economics_hash=_string(value, "fee_product_economics_hash"),
        intent_fingerprint=_string(value, "intent_fingerprint"),
        previous_admission_hash=_string(value, "previous_admission_hash"),
        status=status,
        reason_codes=tuple(reasons),
        admitted_at=admitted_at,
        valid_until=valid_until,
        probation_notional_cap_usd=Decimal(_string(value, "probation_notional_cap_usd")),
        probation_order_cap=_integer(value, "probation_order_cap"),
        probation_budget_is_execution_authority=_boolean(
            value, "probation_budget_is_execution_authority"
        ),
        paper_candidate_authorized=_boolean(value, "paper_candidate_authorized"),
        paper_execution_authorized=_boolean(value, "paper_execution_authorized"),
        external_execution_authorized=_boolean(value, "external_execution_authorized"),
        runtime_execution_authorized=_boolean(value, "runtime_execution_authorized"),
        capital_authority=_string(value, "capital_authority"),
        live_trading=_string(value, "live_trading"),
        admission_hash=_string(value, "admission_hash"),
    )


def _policy_payload(value: PaperCandidateAdmissionPolicy, *, include_hash: bool) -> dict[str, object]:
    names = (
        "policy_id",
        "contract_version",
        "promotion_policy_id",
        "promotion_policy_hash",
        "threshold_policy_hash",
        "selected_trial_id",
        "selected_trial_fingerprint",
        "selected_strategy_id",
        "selected_strategy_version",
        "strategy_spec_hash",
        "loaded_runtime_code_hash",
        "fee_product_economics_hash",
        "intent_fingerprint",
        "authority_key",
        "max_w84_finalization_age_seconds",
        "candidate_validity_seconds",
        "probation_notional_cap_usd",
        "probation_order_cap",
        "probation_budget_is_execution_authority",
        "paper_execution_authorized",
        "external_execution_authorized",
        "runtime_execution_authorized",
        "capital_authority",
        "live_trading",
    )
    payload = _policy_payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["policy_hash"] = value.policy_hash
    return payload


def _policy_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["probation_notional_cap_usd"] = str(payload["probation_notional_cap_usd"])
    return payload


def _registration_payload(
    value: PaperCandidateAdmissionPolicyRegistration, *, include_hash: bool
) -> dict[str, object]:
    payload = _registration_payload_from_values(
        {
            "registration_version": value.registration_version,
            "policy": value.policy,
            "registered_at": value.registered_at,
        }
    )
    if include_hash:
        payload["registration_hash"] = value.registration_hash
    return payload


def _registration_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    policy = values["policy"]
    registered_at = values["registered_at"]
    if not isinstance(policy, PaperCandidateAdmissionPolicy):
        raise PaperCandidateAdmissionIntegrityError("registration policy type invalid")
    if not isinstance(registered_at, datetime):
        raise PaperCandidateAdmissionIntegrityError("registered_at type invalid")
    return {
        "registration_version": values["registration_version"],
        "policy": policy.to_dict(),
        "registered_at": _utc(registered_at).isoformat(),
    }


def _receipt_payload(value: PaperCandidateAdmissionReceipt, *, include_hash: bool) -> dict[str, object]:
    names = tuple(
        field
        for field in PaperCandidateAdmissionReceipt.__dataclass_fields__
        if field != "admission_hash"
    )
    payload = _receipt_payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["admission_hash"] = value.admission_hash
    return payload


def _receipt_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    status = payload["status"]
    if not isinstance(status, PaperCandidateAdmissionStatus):
        raise PaperCandidateAdmissionIntegrityError("receipt status type invalid")
    payload["status"] = status.value
    reasons = payload["reason_codes"]
    if not isinstance(reasons, tuple):
        raise PaperCandidateAdmissionIntegrityError("receipt reason_codes type invalid")
    payload["reason_codes"] = list(reasons)
    for key in (
        "admitted_at",
        "valid_until",
        "w84_admission_source_capture_at",
        "w84_admission_source_verified_at",
    ):
        value = payload[key]
        if value is not None:
            if not isinstance(value, datetime):
                raise PaperCandidateAdmissionIntegrityError(f"{key} type invalid")
            payload[key] = _utc(value).isoformat()
    payload["probation_notional_cap_usd"] = str(payload["probation_notional_cap_usd"])
    return payload


def _authority_key(
    *,
    promotion_policy_hash: str,
    selected_trial_fingerprint: str,
    strategy_spec_hash: str,
    loaded_runtime_code_hash: str,
    fee_product_economics_hash: str,
    intent_fingerprint: str,
) -> str:
    for label, value in (
        ("promotion_policy_hash", promotion_policy_hash),
        ("selected_trial_fingerprint", selected_trial_fingerprint),
        ("strategy_spec_hash", strategy_spec_hash),
        ("loaded_runtime_code_hash", loaded_runtime_code_hash),
        ("fee_product_economics_hash", fee_product_economics_hash),
        ("intent_fingerprint", intent_fingerprint),
    ):
        _require_hash(value, label)
    return _hash(
        {
            "promotion_policy_hash": promotion_policy_hash,
            "selected_trial_fingerprint": selected_trial_fingerprint,
            "strategy_spec_hash": strategy_spec_hash,
            "loaded_runtime_code_hash": loaded_runtime_code_hash,
            "fee_product_economics_hash": fee_product_economics_hash,
            "intent_fingerprint": intent_fingerprint,
        }
    )


def _require_no_execution_authority(
    *,
    paper_execution: bool,
    external: bool,
    runtime: bool,
    capital: str,
    live: str,
    label: str,
) -> None:
    if (
        paper_execution is not False
        or external is not False
        or runtime is not False
        or capital != "NONE"
        or live != "BLOCKED"
    ):
        raise PaperCandidateAdmissionIntegrityError(
            f"{label} may not grant PAPER execution, external/runtime execution, capital, or LIVE authority"
        )


def _require_positive_bounded_int(value: int, maximum: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise PaperCandidateAdmissionIntegrityError(
            f"{label} must be integer within [1,{maximum}]"
        )


def _require_non_negative_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise PaperCandidateAdmissionIntegrityError(
            f"{label} must be finite Decimal >=0"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperCandidateAdmissionIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperCandidateAdmissionIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperCandidateAdmissionIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _datetime_or_error(value: dict[str, object], key: str) -> datetime:
    raw = _string(value, key)
    try:
        result = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise PaperCandidateAdmissionIntegrityError(f"{key} invalid") from exc
    _require_aware(result, key)
    return result


def _optional_datetime(value: dict[str, object], key: str) -> datetime | None:
    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise PaperCandidateAdmissionIntegrityError(f"{key} must be datetime text or null")
    try:
        result = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise PaperCandidateAdmissionIntegrityError(f"{key} invalid") from exc
    _require_aware(result, key)
    return result


def _string(value: dict[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise PaperCandidateAdmissionIntegrityError(f"{key} must be string")
    return raw


def _optional_string(value: dict[str, object], key: str) -> str | None:
    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise PaperCandidateAdmissionIntegrityError(f"{key} must be string or null")
    return raw


def _integer(value: dict[str, object], key: str) -> int:
    raw = value.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise PaperCandidateAdmissionIntegrityError(f"{key} must be integer")
    return raw


def _boolean(value: dict[str, object], key: str) -> bool:
    raw = value.get(key)
    if not isinstance(raw, bool):
        raise PaperCandidateAdmissionIntegrityError(f"{key} must be boolean")
    return raw


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _resolved_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "ADMISSION_POLICY_VERSION",
    "POLICY_REGISTRATION_VERSION",
    "ADMISSION_RECEIPT_VERSION",
    "ZERO_ADMISSION_HASH",
    "MAX_FINALIZATION_AGE_SECONDS",
    "MAX_CANDIDATE_VALIDITY_SECONDS",
    "MAX_PROBATION_NOTIONAL_USD",
    "MAX_PROBATION_ORDERS",
    "PaperCandidateAdmissionConflict",
    "PaperCandidateAdmissionError",
    "PaperCandidateAdmissionIntegrityError",
    "PaperCandidateAdmissionPolicy",
    "PaperCandidateAdmissionPolicyRegistration",
    "PaperCandidateAdmissionReceipt",
    "PaperCandidateAdmissionStatus",
    "SQLitePaperCandidateAdmissionRegistry",
    "W84AdmissionSourcePackage",
    "W84AdmissionSourceProof",
    "build_paper_candidate_admission_policy",
]
