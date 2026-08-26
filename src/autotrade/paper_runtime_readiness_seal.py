from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
import re

from autotrade.paper_candidate_admission_lifecycle import PaperCandidateEligibilityState
from autotrade.paper_runtime_candidate_identity import PaperRuntimeCandidateIdentityProof
from autotrade.paper_runtime_read_only_pipeline import PaperRuntimeReadOnlyPipelineResult
from autotrade.paper_runtime_readiness_source_snapshot import W85DurableEligibilitySnapshotProof
from autotrade.paper_runtime_source_postcheck import (
    PaperRuntimeSourcePostcheckProof,
    PaperRuntimeSourcePostcheckReader,
)


PAPER_RUNTIME_READINESS_SEAL_VERSION = "W86_PAPER_RUNTIME_READINESS_SEAL_V1"
READINESS_SEAL_TTL_SECONDS = 1
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class PaperRuntimeReadinessSealIntegrityError(RuntimeError):
    pass


class PaperRuntimeReadinessSealStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class PaperRuntimeReadinessSealBlocker(StrEnum):
    UPSTREAM_RUNTIME_NOT_READY = "UPSTREAM_RUNTIME_NOT_READY"
    UPSTREAM_RUNTIME_EXPIRED = "UPSTREAM_RUNTIME_EXPIRED"
    W85_SOURCE_CHANGED = "W85_SOURCE_CHANGED"
    W85_CANDIDATE_NOT_ACTIVE = "W85_CANDIDATE_NOT_ACTIVE"
    W85_ADMISSION_EXPIRED = "W85_ADMISSION_EXPIRED"


@dataclass(frozen=True, slots=True)
class PaperRuntimeReadinessSealReceipt:
    seal_id: str
    contract_version: str
    pipeline_receipt_hash: str
    funding_capacity_hash: str
    source_snapshot_hash: str
    candidate_identity_hash: str
    post_collection_source_hash: str
    authority_key: str
    admission_hash: str
    strategy_id: str
    product_id: str
    symbol: str
    account_id: str
    upstream_runtime_ready: bool
    upstream_funding_valid_until: datetime
    source_current_state: PaperCandidateEligibilityState
    source_admission_valid_until: datetime
    status: PaperRuntimeReadinessSealStatus
    blocker_codes: tuple[PaperRuntimeReadinessSealBlocker, ...]
    observed_at: datetime
    valid_until: datetime
    upstream_pipeline_integrity_verified: bool
    post_collection_source_verified: bool
    source_unchanged_after_network: bool
    separate_execution_approval_required: bool
    broker_write_performed: bool
    capital_reserved: bool
    paper_runtime_ready: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        _id(self.seal_id, "seal_id")
        _id(self.strategy_id, "strategy_id")
        _id(self.product_id, "product_id")
        if self.contract_version != PAPER_RUNTIME_READINESS_SEAL_VERSION:
            raise PaperRuntimeReadinessSealIntegrityError(
                "W86 readiness seal version is not canonical"
            )
        for name in (
            "pipeline_receipt_hash",
            "funding_capacity_hash",
            "source_snapshot_hash",
            "candidate_identity_hash",
            "post_collection_source_hash",
            "authority_key",
            "admission_hash",
            "receipt_hash",
        ):
            _sha(getattr(self, name), name)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise PaperRuntimeReadinessSealIntegrityError("symbol is required")
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise PaperRuntimeReadinessSealIntegrityError("account_id is required")
        if not isinstance(self.upstream_runtime_ready, bool):
            raise PaperRuntimeReadinessSealIntegrityError(
                "upstream_runtime_ready must be bool"
            )
        if not isinstance(self.source_current_state, PaperCandidateEligibilityState):
            raise PaperRuntimeReadinessSealIntegrityError(
                "source_current_state must be PaperCandidateEligibilityState"
            )
        for name in (
            "upstream_funding_valid_until",
            "source_admission_valid_until",
            "observed_at",
            "valid_until",
        ):
            _aware(getattr(self, name), name)
        if not isinstance(self.source_unchanged_after_network, bool):
            raise PaperRuntimeReadinessSealIntegrityError(
                "source_unchanged_after_network must be bool"
            )
        if not isinstance(self.post_collection_source_verified, bool):
            raise PaperRuntimeReadinessSealIntegrityError(
                "post_collection_source_verified must be bool"
            )
        if not isinstance(self.blocker_codes, tuple) or any(
            not isinstance(code, PaperRuntimeReadinessSealBlocker)
            for code in self.blocker_codes
        ):
            raise PaperRuntimeReadinessSealIntegrityError(
                "blocker_codes must be canonical W86 seal blockers"
            )

        observed = _utc(self.observed_at)
        funding_valid_until = _utc(self.upstream_funding_valid_until)
        source_valid_until = _utc(self.source_admission_valid_until)
        expected_source_verified = (
            self.source_unchanged_after_network
            and self.source_current_state is PaperCandidateEligibilityState.ACTIVE
            and observed <= source_valid_until
        )
        if self.post_collection_source_verified is not expected_source_verified:
            raise PaperRuntimeReadinessSealIntegrityError(
                "post-collection source verification flag is not exact projection"
            )

        expected_blockers = _expected_blockers(
            upstream_runtime_ready=self.upstream_runtime_ready,
            upstream_funding_valid_until=funding_valid_until,
            source_unchanged=self.source_unchanged_after_network,
            source_current_state=self.source_current_state,
            source_admission_valid_until=source_valid_until,
            observed_at=observed,
        )
        if self.blocker_codes != expected_blockers:
            raise PaperRuntimeReadinessSealIntegrityError(
                "seal blockers are not the exact fail-closed projection"
            )
        expected_ready = not expected_blockers
        expected_status = (
            PaperRuntimeReadinessSealStatus.READY
            if expected_ready
            else PaperRuntimeReadinessSealStatus.BLOCKED
        )
        if self.status is not expected_status or self.paper_runtime_ready is not expected_ready:
            raise PaperRuntimeReadinessSealIntegrityError(
                "seal status/readiness disagrees with exact blocker projection"
            )

        expected_valid_until = observed
        if expected_ready:
            expected_valid_until = min(
                funding_valid_until,
                source_valid_until,
                observed + timedelta(seconds=READINESS_SEAL_TTL_SECONDS),
            )
        if _utc(self.valid_until) != expected_valid_until:
            raise PaperRuntimeReadinessSealIntegrityError(
                "seal valid_until is not the exact finite upstream/postcheck TTL"
            )
        if expected_ready and expected_valid_until < observed:
            raise PaperRuntimeReadinessSealIntegrityError("READY seal is already stale")

        if (
            self.upstream_pipeline_integrity_verified is not True
            or self.separate_execution_approval_required is not True
            or self.broker_write_performed is not False
            or self.capital_reserved is not False
            or self.paper_execution_authorized is not False
            or self.external_execution_authorized is not False
            or self.runtime_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
        ):
            raise PaperRuntimeReadinessSealIntegrityError(
                "W86 seal may not grant execution/capital/broker-write/LIVE authority"
            )
        if self.receipt_hash != _hash(_payload(self, include_hash=False)):
            raise PaperRuntimeReadinessSealIntegrityError(
                "W86 readiness seal receipt hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PaperRuntimeReadinessSealedResult:
    pipeline: PaperRuntimeReadOnlyPipelineResult
    post_collection_source: PaperRuntimeSourcePostcheckProof
    seal: PaperRuntimeReadinessSealReceipt

    def __post_init__(self) -> None:
        if self.pipeline.receipt.receipt_hash != self.seal.pipeline_receipt_hash:
            raise PaperRuntimeReadinessSealIntegrityError(
                "sealed result does not bind exact pipeline receipt"
            )
        if self.pipeline.funding_capacity.proof_hash != self.seal.funding_capacity_hash:
            raise PaperRuntimeReadinessSealIntegrityError(
                "sealed result does not bind exact funding proof"
            )
        if self.post_collection_source.proof_hash != self.seal.post_collection_source_hash:
            raise PaperRuntimeReadinessSealIntegrityError(
                "sealed result does not bind exact post-collection source proof"
            )
        if self.pipeline.receipt.source_snapshot_hash != self.seal.source_snapshot_hash:
            raise PaperRuntimeReadinessSealIntegrityError(
                "sealed result source snapshot binding mismatch"
            )
        if self.pipeline.receipt.candidate_identity_hash != self.seal.candidate_identity_hash:
            raise PaperRuntimeReadinessSealIntegrityError(
                "sealed result candidate identity binding mismatch"
            )


def seal_paper_runtime_readiness_after_collection(
    *,
    seal_id: str,
    pipeline_result: PaperRuntimeReadOnlyPipelineResult,
    source_snapshot: W85DurableEligibilitySnapshotProof,
    candidate_identity: PaperRuntimeCandidateIdentityProof,
    core_path: str | Path,
) -> PaperRuntimeReadinessSealedResult:
    """Seal W86 readiness only after a second durable W85 read.

    This is deliberately downstream of all network GETs. A valid lifecycle change
    (including SUSPEND/REVOKE/REINSTATE) invalidates the original source identity;
    immutable durable corruption raises. The seal itself never authorizes an order.
    """

    _id(seal_id, "seal_id")
    _validate_upstream(pipeline_result, source_snapshot, candidate_identity)

    post = PaperRuntimeSourcePostcheckReader(core_path).verify_after_collection(
        proof_id=f"{seal_id}:source-postcheck",
        source_snapshot=source_snapshot,
    )
    post.__post_init__()
    observed_at = _utc(post.observed_at)
    blocker_codes = _expected_blockers(
        upstream_runtime_ready=pipeline_result.receipt.paper_runtime_ready,
        upstream_funding_valid_until=_utc(pipeline_result.funding_capacity.valid_until),
        source_unchanged=post.source_unchanged,
        source_current_state=post.current_state,
        source_admission_valid_until=_utc(post.admission_valid_until),
        observed_at=observed_at,
    )
    ready = not blocker_codes
    valid_until = observed_at
    if ready:
        valid_until = min(
            _utc(pipeline_result.funding_capacity.valid_until),
            _utc(post.admission_valid_until),
            observed_at + timedelta(seconds=READINESS_SEAL_TTL_SECONDS),
        )

    values = {
        "seal_id": seal_id,
        "contract_version": PAPER_RUNTIME_READINESS_SEAL_VERSION,
        "pipeline_receipt_hash": pipeline_result.receipt.receipt_hash,
        "funding_capacity_hash": pipeline_result.funding_capacity.proof_hash,
        "source_snapshot_hash": source_snapshot.proof_hash,
        "candidate_identity_hash": candidate_identity.proof_hash,
        "post_collection_source_hash": post.proof_hash,
        "authority_key": candidate_identity.authority_key,
        "admission_hash": candidate_identity.admission_hash,
        "strategy_id": candidate_identity.selected_strategy_id,
        "product_id": candidate_identity.product_id,
        "symbol": candidate_identity.symbol,
        "account_id": pipeline_result.broker_truth.account_id,
        "upstream_runtime_ready": pipeline_result.receipt.paper_runtime_ready,
        "upstream_funding_valid_until": pipeline_result.funding_capacity.valid_until,
        "source_current_state": post.current_state,
        "source_admission_valid_until": post.admission_valid_until,
        "status": (
            PaperRuntimeReadinessSealStatus.READY
            if ready
            else PaperRuntimeReadinessSealStatus.BLOCKED
        ),
        "blocker_codes": blocker_codes,
        "observed_at": observed_at,
        "valid_until": valid_until,
        "upstream_pipeline_integrity_verified": True,
        "post_collection_source_verified": post.post_collection_source_verified,
        "source_unchanged_after_network": post.source_unchanged,
        "separate_execution_approval_required": True,
        "broker_write_performed": False,
        "capital_reserved": False,
        "paper_runtime_ready": ready,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    seal = PaperRuntimeReadinessSealReceipt(
        **values,
        receipt_hash=_hash(_payload_values(values)),
    )
    return PaperRuntimeReadinessSealedResult(
        pipeline=pipeline_result,
        post_collection_source=post,
        seal=seal,
    )


def _expected_blockers(
    *,
    upstream_runtime_ready: bool,
    upstream_funding_valid_until: datetime,
    source_unchanged: bool,
    source_current_state: PaperCandidateEligibilityState,
    source_admission_valid_until: datetime,
    observed_at: datetime,
) -> tuple[PaperRuntimeReadinessSealBlocker, ...]:
    blockers: list[PaperRuntimeReadinessSealBlocker] = []
    if upstream_runtime_ready is not True:
        blockers.append(PaperRuntimeReadinessSealBlocker.UPSTREAM_RUNTIME_NOT_READY)
    if observed_at > upstream_funding_valid_until:
        blockers.append(PaperRuntimeReadinessSealBlocker.UPSTREAM_RUNTIME_EXPIRED)
    if source_unchanged is not True:
        blockers.append(PaperRuntimeReadinessSealBlocker.W85_SOURCE_CHANGED)
    if source_current_state is not PaperCandidateEligibilityState.ACTIVE:
        blockers.append(PaperRuntimeReadinessSealBlocker.W85_CANDIDATE_NOT_ACTIVE)
    if observed_at > source_admission_valid_until:
        blockers.append(PaperRuntimeReadinessSealBlocker.W85_ADMISSION_EXPIRED)
    return tuple(blockers)


def _validate_upstream(
    pipeline_result: PaperRuntimeReadOnlyPipelineResult,
    source_snapshot: W85DurableEligibilitySnapshotProof,
    candidate_identity: PaperRuntimeCandidateIdentityProof,
) -> None:
    if not isinstance(pipeline_result, PaperRuntimeReadOnlyPipelineResult):
        raise TypeError("pipeline_result must be PaperRuntimeReadOnlyPipelineResult")
    if not isinstance(source_snapshot, W85DurableEligibilitySnapshotProof):
        raise TypeError("source_snapshot must be W85DurableEligibilitySnapshotProof")
    if not isinstance(candidate_identity, PaperRuntimeCandidateIdentityProof):
        raise TypeError("candidate_identity must be PaperRuntimeCandidateIdentityProof")

    source_snapshot.__post_init__()
    candidate_identity.__post_init__()
    pipeline_result.account_attestation.__post_init__()
    pipeline_result.broker_truth.__post_init__()
    pipeline_result.asset_truth.__post_init__()
    pipeline_result.market_truth.__post_init__()
    pipeline_result.safety_health_truth.__post_init__()
    pipeline_result.final_readiness.__post_init__()
    pipeline_result.funding_capacity.__post_init__()
    pipeline_result.receipt.__post_init__()
    pipeline_result.__post_init__()

    if pipeline_result.receipt.source_snapshot_hash != source_snapshot.proof_hash:
        raise PaperRuntimeReadinessSealIntegrityError(
            "pipeline receipt is not bound to supplied W85 snapshot"
        )
    if pipeline_result.receipt.candidate_identity_hash != candidate_identity.proof_hash:
        raise PaperRuntimeReadinessSealIntegrityError(
            "pipeline receipt is not bound to supplied candidate identity"
        )
    if candidate_identity.w85_source_snapshot_hash != source_snapshot.proof_hash:
        raise PaperRuntimeReadinessSealIntegrityError(
            "candidate identity is not bound to supplied W85 snapshot"
        )
    if (
        candidate_identity.authority_key != source_snapshot.authority_key
        or candidate_identity.admission_hash != source_snapshot.admission_hash
    ):
        raise PaperRuntimeReadinessSealIntegrityError(
            "candidate/source durable authority identity mismatch"
        )
    for value in (
        pipeline_result.receipt,
        pipeline_result.final_readiness,
        pipeline_result.funding_capacity,
        source_snapshot,
        candidate_identity,
    ):
        if (
            value.paper_execution_authorized is not False
            or value.external_execution_authorized is not False
            or value.runtime_execution_authorized is not False
            or value.capital_authority != "NONE"
            or value.live_trading != "BLOCKED"
        ):
            raise PaperRuntimeReadinessSealIntegrityError(
                "upstream W86 evidence contains execution/capital/LIVE escalation"
            )


def _payload(
    value: PaperRuntimeReadinessSealReceipt, *, include_hash: bool
) -> dict[str, object]:
    names = (
        name
        for name in PaperRuntimeReadinessSealReceipt.__dataclass_fields__
        if name != "receipt_hash"
    )
    payload = _payload_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["receipt_hash"] = value.receipt_hash
    return payload


def _payload_values(values: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            payload[key] = _utc(value).isoformat()
        elif isinstance(value, StrEnum):
            payload[key] = value.value
        elif isinstance(value, tuple):
            payload[key] = [item.value if isinstance(item, StrEnum) else item for item in value]
        else:
            payload[key] = value
    return payload


def _id(value: str, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperRuntimeReadinessSealIntegrityError(
            f"{name} must be canonical identifier"
        )


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeReadinessSealIntegrityError(
            f"{name} must be lowercase sha256"
        )


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperRuntimeReadinessSealIntegrityError(
            f"{name} must be timezone-aware"
        )


def _utc(value: datetime) -> datetime:
    _aware(value, "datetime")
    return value.astimezone(timezone.utc)


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
    "PAPER_RUNTIME_READINESS_SEAL_VERSION",
    "READINESS_SEAL_TTL_SECONDS",
    "PaperRuntimeReadinessSealIntegrityError",
    "PaperRuntimeReadinessSealStatus",
    "PaperRuntimeReadinessSealBlocker",
    "PaperRuntimeReadinessSealReceipt",
    "PaperRuntimeReadinessSealedResult",
    "seal_paper_runtime_readiness_after_collection",
]
