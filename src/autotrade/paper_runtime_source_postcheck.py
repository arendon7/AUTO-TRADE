from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.paper_runtime_readiness_source as source_v1
import autotrade.paper_runtime_readiness_source_snapshot as snapshot_module
from autotrade.paper_candidate_admission_lifecycle import PaperCandidateEligibilityState
from autotrade.paper_runtime_readiness_source_snapshot import (
    W85DurableEligibilitySnapshotProof,
)


PAPER_RUNTIME_SOURCE_POSTCHECK_VERSION = "W86_PAPER_RUNTIME_SOURCE_POSTCHECK_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class PaperRuntimeSourcePostcheckError(RuntimeError):
    pass


class PaperRuntimeSourcePostcheckIntegrityError(PaperRuntimeSourcePostcheckError):
    pass


@dataclass(frozen=True, slots=True)
class PaperRuntimeSourcePostcheckProof:
    proof_id: str
    contract_version: str
    source_snapshot_hash: str
    authority_key: str
    admission_id: str
    admission_hash: str
    policy_id: str
    policy_hash: str
    policy_registration_hash: str
    initial_lifecycle_head_hash: str
    initial_lifecycle_events_count: int
    current_lifecycle_head_hash: str
    current_lifecycle_events_count: int
    current_state: PaperCandidateEligibilityState
    admission_valid_until: datetime
    observed_at: datetime
    source_unchanged: bool
    candidate_currently_eligible: bool
    post_collection_source_verified: bool
    sqlite_read_only: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    proof_hash: str

    def __post_init__(self) -> None:
        _id(self.proof_id, "proof_id")
        _id(self.admission_id, "admission_id")
        _id(self.policy_id, "policy_id")
        if self.contract_version != PAPER_RUNTIME_SOURCE_POSTCHECK_VERSION:
            raise PaperRuntimeSourcePostcheckIntegrityError(
                "W86 post-collection source proof version is not canonical"
            )
        for name in (
            "source_snapshot_hash",
            "authority_key",
            "admission_hash",
            "policy_hash",
            "policy_registration_hash",
            "initial_lifecycle_head_hash",
            "current_lifecycle_head_hash",
            "proof_hash",
        ):
            _sha(getattr(self, name), name)
        for name in ("initial_lifecycle_events_count", "current_lifecycle_events_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PaperRuntimeSourcePostcheckIntegrityError(
                    f"{name} must be integer >=0"
                )
        if not isinstance(self.current_state, PaperCandidateEligibilityState):
            raise PaperRuntimeSourcePostcheckIntegrityError(
                "current_state must be PaperCandidateEligibilityState"
            )
        _aware(self.admission_valid_until, "admission_valid_until")
        _aware(self.observed_at, "observed_at")
        expected_unchanged = (
            self.initial_lifecycle_head_hash == self.current_lifecycle_head_hash
            and self.initial_lifecycle_events_count == self.current_lifecycle_events_count
        )
        if self.source_unchanged is not expected_unchanged:
            raise PaperRuntimeSourcePostcheckIntegrityError(
                "source_unchanged disagrees with exact lifecycle identity"
            )
        expected_eligible = (
            self.current_state is PaperCandidateEligibilityState.ACTIVE
            and _utc(self.observed_at) <= _utc(self.admission_valid_until)
        )
        if self.candidate_currently_eligible is not expected_eligible:
            raise PaperRuntimeSourcePostcheckIntegrityError(
                "post-collection eligibility disagrees with current durable W85 state"
            )
        expected_verified = expected_unchanged and expected_eligible
        if self.post_collection_source_verified is not expected_verified:
            raise PaperRuntimeSourcePostcheckIntegrityError(
                "post-collection source verification flag is inconsistent"
            )
        if self.sqlite_read_only is not True:
            raise PaperRuntimeSourcePostcheckIntegrityError(
                "post-collection W85 revalidation must be SQLite read-only"
            )
        _no_authority(
            paper=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
        )
        if self.proof_hash != _hash(_payload(self, include_hash=False)):
            raise PaperRuntimeSourcePostcheckIntegrityError(
                "post-collection source proof hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


class PaperRuntimeSourcePostcheckReader:
    """Revalidate exact W85 source after network collection without write authority.

    A legitimate lifecycle change is represented as a non-passing proof instead of
    being hidden. Durable admission/policy/hash-chain corruption is an integrity
    error. This distinction lets callers fail closed while retaining evidence of
    why a previously valid source can no longer support readiness.
    """

    def __init__(self, core_path: str | Path) -> None:
        raw = Path(core_path).expanduser()
        if raw.is_symlink():
            raise PaperRuntimeSourcePostcheckIntegrityError(
                "W86 postcheck refuses symlinked authoritative core database"
            )
        resolved = raw.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise PaperRuntimeSourcePostcheckIntegrityError(
                "W86 postcheck requires an existing authoritative core database"
            )
        self._core_path = resolved

    def verify_after_collection(
        self,
        *,
        proof_id: str,
        source_snapshot: W85DurableEligibilitySnapshotProof,
    ) -> PaperRuntimeSourcePostcheckProof:
        _id(proof_id, "proof_id")
        _validate_snapshot(source_snapshot)
        now = _utc(_now_utc())

        conn = self._connect_read_only()
        try:
            try:
                source_v1._require_schema(conn)
                registration = source_v1._read_registration(
                    conn, source_snapshot.policy_id
                )
                receipt = source_v1._read_receipt(
                    conn, source_snapshot.admission_id
                )
                source_v1._validate_registration_and_receipt(registration, receipt)
                source_v1._validate_full_admission_chain(conn, receipt)
                events = source_v1._read_and_validate_lifecycle(conn, receipt)
                current_state = source_v1._current_state(receipt, events, now)
            except source_v1.PaperRuntimeReadinessSourceIntegrityError as exc:
                raise PaperRuntimeSourcePostcheckIntegrityError(
                    "durable W85 source failed post-collection integrity validation"
                ) from exc
        finally:
            conn.close()

        policy = registration.policy
        immutable_checks = (
            (policy.policy_id, source_snapshot.policy_id, "policy id"),
            (policy.policy_hash, source_snapshot.policy_hash, "policy hash"),
            (policy.authority_key, source_snapshot.authority_key, "policy authority"),
            (
                registration.registration_hash,
                source_snapshot.policy_registration_hash,
                "policy registration hash",
            ),
            (receipt.admission_id, source_snapshot.admission_id, "admission id"),
            (receipt.admission_hash, source_snapshot.admission_hash, "admission hash"),
            (receipt.authority_key, source_snapshot.authority_key, "authority key"),
            (receipt.policy_id, source_snapshot.policy_id, "receipt policy id"),
            (receipt.policy_hash, source_snapshot.policy_hash, "receipt policy hash"),
            (
                receipt.policy_registration_hash,
                source_snapshot.policy_registration_hash,
                "receipt policy registration hash",
            ),
            (
                receipt.w84_admission_source_proof_hash,
                source_snapshot.w84_admission_source_proof_hash,
                "W84 source proof",
            ),
            (
                receipt.selected_trial_fingerprint,
                source_snapshot.selected_trial_fingerprint,
                "selected trial",
            ),
            (
                receipt.strategy_spec_hash,
                source_snapshot.strategy_spec_hash,
                "strategy spec",
            ),
            (
                receipt.loaded_runtime_code_hash,
                source_snapshot.loaded_runtime_code_hash,
                "loaded runtime",
            ),
            (
                receipt.fee_product_economics_hash,
                source_snapshot.fee_product_economics_hash,
                "fee economics",
            ),
            (
                receipt.intent_fingerprint,
                source_snapshot.intent_fingerprint,
                "intent fingerprint",
            ),
            (
                receipt.valid_until,
                source_snapshot.admission_valid_until,
                "admission validity",
            ),
            (
                receipt.probation_notional_cap_usd,
                source_snapshot.probation_notional_cap_usd,
                "probation notional cap",
            ),
            (
                receipt.probation_order_cap,
                source_snapshot.probation_order_cap,
                "probation order cap",
            ),
        )
        for durable, initial, label in immutable_checks:
            if durable != initial:
                raise PaperRuntimeSourcePostcheckIntegrityError(
                    f"durable W85 {label} changed after initial W86 snapshot"
                )

        current_head = (
            events[-1].event_hash if events else lifecycle.ZERO_EVENT_HASH
        )
        current_count = len(events)
        source_unchanged = (
            current_head == source_snapshot.lifecycle_head_hash
            and current_count == source_snapshot.lifecycle_events_count
        )
        candidate_currently_eligible = (
            current_state is PaperCandidateEligibilityState.ACTIVE
            and now <= _utc(source_snapshot.admission_valid_until)
        )
        verified = source_unchanged and candidate_currently_eligible
        values = {
            "proof_id": proof_id,
            "contract_version": PAPER_RUNTIME_SOURCE_POSTCHECK_VERSION,
            "source_snapshot_hash": source_snapshot.proof_hash,
            "authority_key": source_snapshot.authority_key,
            "admission_id": source_snapshot.admission_id,
            "admission_hash": source_snapshot.admission_hash,
            "policy_id": source_snapshot.policy_id,
            "policy_hash": source_snapshot.policy_hash,
            "policy_registration_hash": source_snapshot.policy_registration_hash,
            "initial_lifecycle_head_hash": source_snapshot.lifecycle_head_hash,
            "initial_lifecycle_events_count": source_snapshot.lifecycle_events_count,
            "current_lifecycle_head_hash": current_head,
            "current_lifecycle_events_count": current_count,
            "current_state": current_state,
            "admission_valid_until": source_snapshot.admission_valid_until,
            "observed_at": now,
            "source_unchanged": source_unchanged,
            "candidate_currently_eligible": candidate_currently_eligible,
            "post_collection_source_verified": verified,
            "sqlite_read_only": True,
            "paper_execution_authorized": False,
            "external_execution_authorized": False,
            "runtime_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        return PaperRuntimeSourcePostcheckProof(
            **values,
            proof_hash=_hash(_payload_values(values)),
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
            raise PaperRuntimeSourcePostcheckIntegrityError(
                "W86 postcheck could not enforce SQLite query_only mode"
            )
        return conn


def _validate_snapshot(source_snapshot: W85DurableEligibilitySnapshotProof) -> None:
    if not isinstance(source_snapshot, W85DurableEligibilitySnapshotProof):
        raise TypeError("source_snapshot must be W85DurableEligibilitySnapshotProof")
    expected = snapshot_module._hash(
        snapshot_module._proof_payload(source_snapshot, include_hash=False)
    )
    if source_snapshot.proof_hash != expected:
        raise PaperRuntimeSourcePostcheckIntegrityError(
            "initial W85 source snapshot hash mismatch"
        )
    if (
        source_snapshot.sqlite_read_only is not True
        or source_snapshot.sqlite_snapshot_consistent is not True
        or source_snapshot.concurrent_durable_change_detected is not False
        or source_snapshot.durable_admission_verified is not True
        or source_snapshot.durable_lifecycle_verified is not True
    ):
        raise PaperRuntimeSourcePostcheckIntegrityError(
            "initial W85 source snapshot is not durable/read-only/consistent"
        )
    _no_authority(
        paper=source_snapshot.paper_execution_authorized,
        external=source_snapshot.external_execution_authorized,
        runtime=source_snapshot.runtime_execution_authorized,
        capital=source_snapshot.capital_authority,
        live=source_snapshot.live_trading,
    )


def _payload(
    value: PaperRuntimeSourcePostcheckProof, *, include_hash: bool
) -> dict[str, object]:
    names = (
        name
        for name in PaperRuntimeSourcePostcheckProof.__dataclass_fields__
        if name != "proof_hash"
    )
    payload = _payload_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["proof_hash"] = value.proof_hash
    return payload


def _payload_values(values: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            payload[key] = _utc(value).isoformat()
        elif isinstance(value, PaperCandidateEligibilityState):
            payload[key] = value.value
        else:
            payload[key] = value
    return payload


def _no_authority(*, paper: bool, external: bool, runtime: bool, capital: str, live: str) -> None:
    if (
        paper is not False
        or external is not False
        or runtime is not False
        or capital != "NONE"
        or live != "BLOCKED"
    ):
        raise PaperRuntimeSourcePostcheckIntegrityError(
            "post-collection source proof may not grant execution/capital/LIVE authority"
        )


def _id(value: str, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperRuntimeSourcePostcheckIntegrityError(
            f"{name} must be canonical identifier"
        )


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeSourcePostcheckIntegrityError(
            f"{name} must be lowercase sha256"
        )


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperRuntimeSourcePostcheckIntegrityError(
            f"{name} must be timezone-aware"
        )


def _utc(value: datetime) -> datetime:
    _aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "PAPER_RUNTIME_SOURCE_POSTCHECK_VERSION",
    "PaperRuntimeSourcePostcheckError",
    "PaperRuntimeSourcePostcheckIntegrityError",
    "PaperRuntimeSourcePostcheckProof",
    "PaperRuntimeSourcePostcheckReader",
]
