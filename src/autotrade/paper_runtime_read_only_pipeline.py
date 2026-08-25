from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

import autotrade.paper_runtime_candidate_identity as candidate_module
import autotrade.paper_runtime_readiness_source_snapshot as source_module
from autotrade.brokers.alpaca_paper_crypto_account_status import (
    attest_active_crypto_account,
)
from autotrade.brokers.alpaca_paper_flat_account import (
    AlpacaPaperFlatAccountGateway,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperReadTransport,
)
from autotrade.brokers.alpaca_paper_market_data import (
    AlpacaPaperMarketDataTransport,
)
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    AlpacaPaperCryptoMarketDataConfig,
)
from autotrade.paper_runtime_asset_truth import (
    PaperRuntimeAssetTruthPolicy,
    PaperRuntimeAssetTruthProof,
    read_and_bind_paper_runtime_asset_truth,
)
from autotrade.paper_runtime_broker_truth import (
    PaperRuntimeBrokerTruthPolicy,
    PaperRuntimeBrokerTruthProof,
    bind_paper_runtime_broker_truth,
)
from autotrade.paper_runtime_candidate_identity import (
    PaperRuntimeCandidateIdentityProof,
)
from autotrade.paper_runtime_final_readiness import (
    PaperRuntimeFinalReadinessPolicy,
    PaperRuntimeFinalReadinessReceipt,
    finalize_paper_runtime_readiness,
)
from autotrade.paper_runtime_funding_capacity import (
    PaperRuntimeFundingCapacityPolicy,
    PaperRuntimeFundingCapacityProof,
    bind_paper_runtime_funding_capacity,
)
from autotrade.paper_runtime_market_truth import (
    PaperRuntimeMarketTruthPolicy,
    PaperRuntimeMarketTruthProof,
    read_and_bind_paper_runtime_market_truth,
)
from autotrade.paper_runtime_readiness_source_snapshot import (
    W85DurableEligibilitySnapshotProof,
)
from autotrade.paper_runtime_safety_health_truth import (
    PaperRuntimeSafetyHealthTruthPolicy,
    PaperRuntimeSafetyHealthTruthProof,
    PaperRuntimeSafetyHealthTruthReader,
)


PAPER_RUNTIME_READ_ONLY_PIPELINE_VERSION = "W86_PAPER_RUNTIME_READ_ONLY_PIPELINE_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}$")


class PaperRuntimeReadOnlyPipelineError(RuntimeError):
    pass


class PaperRuntimeReadOnlyPipelineIntegrityError(PaperRuntimeReadOnlyPipelineError):
    pass


@dataclass(frozen=True, slots=True)
class PaperRuntimeReadOnlyPipelineReceipt:
    collection_id: str
    contract_version: str
    source_snapshot_hash: str
    candidate_identity_hash: str
    account_attestation_fingerprint: str
    broker_truth_hash: str
    asset_truth_hash: str
    market_truth_hash: str
    safety_health_truth_hash: str
    final_readiness_hash: str
    funding_capacity_hash: str
    started_at: datetime
    completed_at: datetime
    internal_process_clock: bool
    read_only_collection: bool
    network_reads_performed: bool
    network_write_performed: bool
    paper_runtime_ready: bool
    separate_execution_approval_required: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        _id(self.collection_id, "collection_id")
        if self.contract_version != PAPER_RUNTIME_READ_ONLY_PIPELINE_VERSION:
            raise PaperRuntimeReadOnlyPipelineIntegrityError(
                "W86 read-only pipeline version is not canonical"
            )
        for name in (
            "source_snapshot_hash",
            "candidate_identity_hash",
            "account_attestation_fingerprint",
            "broker_truth_hash",
            "asset_truth_hash",
            "market_truth_hash",
            "safety_health_truth_hash",
            "final_readiness_hash",
            "funding_capacity_hash",
            "receipt_hash",
        ):
            _sha(getattr(self, name), name)
        _aware(self.started_at, "started_at")
        _aware(self.completed_at, "completed_at")
        if _utc(self.completed_at) < _utc(self.started_at):
            raise PaperRuntimeReadOnlyPipelineIntegrityError(
                "W86 read-only pipeline clock moved backward"
            )
        if (
            self.internal_process_clock is not True
            or self.read_only_collection is not True
            or self.network_reads_performed is not True
            or self.network_write_performed is not False
            or self.separate_execution_approval_required is not True
            or self.paper_execution_authorized is not False
            or self.external_execution_authorized is not False
            or self.runtime_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
        ):
            raise PaperRuntimeReadOnlyPipelineIntegrityError(
                "W86 read-only pipeline may not grant execution/capital/LIVE authority"
            )
        if self.receipt_hash != _hash(_receipt_payload(self, include_hash=False)):
            raise PaperRuntimeReadOnlyPipelineIntegrityError(
                "W86 read-only pipeline receipt hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _receipt_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PaperRuntimeReadOnlyPipelineResult:
    account_attestation: AlpacaPaperAccountAttestation
    broker_truth: PaperRuntimeBrokerTruthProof
    asset_truth: PaperRuntimeAssetTruthProof
    market_truth: PaperRuntimeMarketTruthProof
    safety_health_truth: PaperRuntimeSafetyHealthTruthProof
    final_readiness: PaperRuntimeFinalReadinessReceipt
    funding_capacity: PaperRuntimeFundingCapacityProof
    receipt: PaperRuntimeReadOnlyPipelineReceipt

    def __post_init__(self) -> None:
        if self.account_attestation.fingerprint != self.receipt.account_attestation_fingerprint:
            raise PaperRuntimeReadOnlyPipelineIntegrityError(
                "pipeline account receipt differs from collection receipt"
            )
        exact_hashes = (
            (self.broker_truth.proof_hash, self.receipt.broker_truth_hash),
            (self.asset_truth.proof_hash, self.receipt.asset_truth_hash),
            (self.market_truth.proof_hash, self.receipt.market_truth_hash),
            (self.safety_health_truth.proof_hash, self.receipt.safety_health_truth_hash),
            (self.final_readiness.receipt_hash, self.receipt.final_readiness_hash),
            (self.funding_capacity.proof_hash, self.receipt.funding_capacity_hash),
        )
        if any(actual != expected for actual, expected in exact_hashes):
            raise PaperRuntimeReadOnlyPipelineIntegrityError(
                "pipeline result component hash differs from collection receipt"
            )
        if self.receipt.paper_runtime_ready is not self.funding_capacity.paper_runtime_ready:
            raise PaperRuntimeReadOnlyPipelineIntegrityError(
                "pipeline readiness must be the funding-capacity readiness"
            )


def collect_paper_runtime_readiness(
    *,
    collection_id: str,
    source_snapshot: W85DurableEligibilitySnapshotProof,
    candidate_identity: PaperRuntimeCandidateIdentityProof,
    credentials: AlpacaPaperCredentials,
    expected_account_id: str,
    trading_config: AlpacaPaperGatewayConfig,
    core_path: str | Path,
    market_data_config: AlpacaPaperCryptoMarketDataConfig | None = None,
    broker_policy: PaperRuntimeBrokerTruthPolicy | None = None,
    asset_policy: PaperRuntimeAssetTruthPolicy | None = None,
    market_policy: PaperRuntimeMarketTruthPolicy | None = None,
    safety_health_policy: PaperRuntimeSafetyHealthTruthPolicy | None = None,
    final_policy: PaperRuntimeFinalReadinessPolicy | None = None,
    funding_policy: PaperRuntimeFundingCapacityPolicy | None = None,
    account_transport: AlpacaPaperReadTransport | None = None,
    crypto_status_transport: AlpacaPaperReadTransport | None = None,
    flat_account_transport: AlpacaPaperReadTransport | None = None,
    asset_transport: AlpacaPaperReadTransport | None = None,
    market_transport: AlpacaPaperMarketDataTransport | None = None,
) -> PaperRuntimeReadOnlyPipelineResult:
    """Collect the complete W86 readiness chain using GET/read-only boundaries only.

    The production process clock is internal. Caller-supplied timestamps are not
    accepted. The exact account attestation is retained so funding capacity uses
    the same hash-bound buying-power observation that broker truth consumed.
    """

    _id(collection_id, "collection_id")
    _preflight_source_candidate(source_snapshot, candidate_identity)
    if not isinstance(credentials, AlpacaPaperCredentials):
        raise TypeError("credentials must be AlpacaPaperCredentials")
    if not isinstance(trading_config, AlpacaPaperGatewayConfig):
        raise TypeError("trading_config must be AlpacaPaperGatewayConfig")
    if trading_config.enabled is not True:
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
            "W86 runtime pipeline requires explicitly enabled PAPER reads"
        )
    if trading_config.base_url != f"https://{ALPACA_PAPER_TRADING_HOST}":
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
            "W86 runtime pipeline requires exact Alpaca PAPER trading host"
        )
    effective_market_config = market_data_config or AlpacaPaperCryptoMarketDataConfig(
        enabled=True
    )
    if not isinstance(effective_market_config, AlpacaPaperCryptoMarketDataConfig):
        raise TypeError("market_data_config must be AlpacaPaperCryptoMarketDataConfig")
    if effective_market_config.enabled is not True:
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
            "W86 runtime pipeline requires explicitly enabled market-data reads"
        )

    started_at = _clock(None)
    broker_at = _clock(started_at)
    account = AlpacaPaperAccountGateway(
        config=trading_config,
        transport=account_transport,
    ).attest_account(
        credentials=credentials,
        expected_account_id=expected_account_id,
        now=broker_at,
    )
    crypto_status = attest_active_crypto_account(
        credentials=credentials,
        expected_account_id=expected_account_id,
        now=broker_at,
        config=trading_config,
        transport=crypto_status_transport,
    )
    flat_account = AlpacaPaperFlatAccountGateway(
        config=trading_config,
        transport=flat_account_transport,
    ).attest_flatness(
        credentials=credentials,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=account.credential_reference,
        now=broker_at,
    )
    broker_truth = bind_paper_runtime_broker_truth(
        proof_id=f"{collection_id}:broker",
        candidate_identity=candidate_identity,
        account=account,
        crypto_status=crypto_status,
        flat_account=flat_account,
        observed_at=broker_at,
        policy=broker_policy,
    )

    asset_at = _clock(broker_at)
    asset_truth = read_and_bind_paper_runtime_asset_truth(
        proof_id=f"{collection_id}:asset",
        candidate_identity=candidate_identity,
        broker_truth=broker_truth,
        credentials=credentials,
        config=trading_config,
        observed_at=asset_at,
        policy=asset_policy,
        transport=asset_transport,
    )

    market_at = _clock(asset_at)
    market_truth = read_and_bind_paper_runtime_market_truth(
        proof_id=f"{collection_id}:market",
        candidate_identity=candidate_identity,
        broker_truth=broker_truth,
        asset_truth=asset_truth,
        credentials=credentials,
        observed_at=market_at,
        policy=market_policy,
        gateway_config=effective_market_config,
        transport=market_transport,
    )

    safety_at = _clock(market_at)
    safety_health_truth = PaperRuntimeSafetyHealthTruthReader(core_path).verify_current(
        proof_id=f"{collection_id}:safety-health",
        candidate_identity=candidate_identity,
        observed_at=safety_at,
        policy=safety_health_policy,
    )

    final_readiness = finalize_paper_runtime_readiness(
        receipt_id=f"{collection_id}:final",
        source_snapshot=source_snapshot,
        candidate_identity=candidate_identity,
        broker_truth=broker_truth,
        asset_truth=asset_truth,
        market_truth=market_truth,
        safety_health_truth=safety_health_truth,
        policy=final_policy,
    )
    funding_capacity = bind_paper_runtime_funding_capacity(
        proof_id=f"{collection_id}:funding",
        final_readiness=final_readiness,
        broker_truth=broker_truth,
        account_attestation=account,
        policy=funding_policy,
    )
    completed_at = _clock(safety_at)
    if _utc(final_readiness.observed_at) > completed_at:
        completed_at = _utc(final_readiness.observed_at)
    if _utc(funding_capacity.observed_at) > completed_at:
        completed_at = _utc(funding_capacity.observed_at)

    values = {
        "collection_id": collection_id,
        "contract_version": PAPER_RUNTIME_READ_ONLY_PIPELINE_VERSION,
        "source_snapshot_hash": source_snapshot.proof_hash,
        "candidate_identity_hash": candidate_identity.proof_hash,
        "account_attestation_fingerprint": account.fingerprint,
        "broker_truth_hash": broker_truth.proof_hash,
        "asset_truth_hash": asset_truth.proof_hash,
        "market_truth_hash": market_truth.proof_hash,
        "safety_health_truth_hash": safety_health_truth.proof_hash,
        "final_readiness_hash": final_readiness.receipt_hash,
        "funding_capacity_hash": funding_capacity.proof_hash,
        "started_at": started_at,
        "completed_at": completed_at,
        "internal_process_clock": True,
        "read_only_collection": True,
        "network_reads_performed": True,
        "network_write_performed": False,
        "paper_runtime_ready": funding_capacity.paper_runtime_ready,
        "separate_execution_approval_required": True,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    receipt = PaperRuntimeReadOnlyPipelineReceipt(
        **values,
        receipt_hash=_hash(_payload_values(values)),
    )
    return PaperRuntimeReadOnlyPipelineResult(
        account_attestation=account,
        broker_truth=broker_truth,
        asset_truth=asset_truth,
        market_truth=market_truth,
        safety_health_truth=safety_health_truth,
        final_readiness=final_readiness,
        funding_capacity=funding_capacity,
        receipt=receipt,
    )


def _preflight_source_candidate(
    source_snapshot: W85DurableEligibilitySnapshotProof,
    candidate_identity: PaperRuntimeCandidateIdentityProof,
) -> None:
    if not isinstance(source_snapshot, W85DurableEligibilitySnapshotProof):
        raise TypeError("source_snapshot must be W85DurableEligibilitySnapshotProof")
    if not isinstance(candidate_identity, PaperRuntimeCandidateIdentityProof):
        raise TypeError("candidate_identity must be PaperRuntimeCandidateIdentityProof")
    expected_source_hash = source_module._hash(
        source_module._proof_payload(source_snapshot, include_hash=False)
    )
    if source_snapshot.proof_hash != expected_source_hash:
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
            "W86 source snapshot hash mismatch before network read"
        )
    expected_candidate_hash = candidate_module._hash(
        candidate_module._payload(candidate_identity, include_hash=False)
    )
    if candidate_identity.proof_hash != expected_candidate_hash:
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
            "W86 candidate identity hash mismatch before network read"
        )
    if candidate_identity.w85_source_snapshot_hash != source_snapshot.proof_hash:
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
            "W86 candidate is not bound to supplied durable W85 snapshot"
        )
    if (
        candidate_identity.authority_key != source_snapshot.authority_key
        or candidate_identity.admission_hash != source_snapshot.admission_hash
        or candidate_identity.final_admission_verification_hash
        != source_snapshot.final_admission_verification_hash
        or candidate_identity.selected_trial_fingerprint
        != source_snapshot.selected_trial_fingerprint
        or candidate_identity.strategy_spec_hash != source_snapshot.strategy_spec_hash
        or candidate_identity.loaded_runtime_code_hash
        != source_snapshot.loaded_runtime_code_hash
        or candidate_identity.fee_product_economics_hash
        != source_snapshot.fee_product_economics_hash
        or candidate_identity.intent_fingerprint != source_snapshot.intent_fingerprint
    ):
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
            "W86 candidate/source provenance mismatch before network read"
        )
    if (
        source_snapshot.candidate_currently_eligible is not True
        or source_snapshot.durable_admission_verified is not True
        or source_snapshot.durable_lifecycle_verified is not True
        or source_snapshot.sqlite_read_only is not True
        or source_snapshot.sqlite_snapshot_consistent is not True
        or source_snapshot.concurrent_durable_change_detected is not False
    ):
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
            "W86 source snapshot is not currently eligible/read-only/consistent"
        )
    for value in (source_snapshot, candidate_identity):
        if (
            value.paper_execution_authorized is not False
            or value.external_execution_authorized is not False
            or value.runtime_execution_authorized is not False
            or value.capital_authority != "NONE"
            or value.live_trading != "BLOCKED"
        ):
            raise PaperRuntimeReadOnlyPipelineIntegrityError(
                "W86 pre-network input contains execution/capital/LIVE escalation"
            )


def _clock(previous: datetime | None) -> datetime:
    current = _utc(_now_utc())
    if previous is not None and current < _utc(previous):
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
            "W86 internal process clock moved backward"
        )
    return current


def _receipt_payload(
    value: PaperRuntimeReadOnlyPipelineReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    names = (
        name
        for name in PaperRuntimeReadOnlyPipelineReceipt.__dataclass_fields__
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
        else:
            payload[key] = value
    return payload


def _id(value: str, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
            f"{name} must be canonical identifier <=96 chars"
        )


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
            f"{name} must be lowercase sha256"
        )


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperRuntimeReadOnlyPipelineIntegrityError(
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
    "PAPER_RUNTIME_READ_ONLY_PIPELINE_VERSION",
    "PaperRuntimeReadOnlyPipelineError",
    "PaperRuntimeReadOnlyPipelineIntegrityError",
    "PaperRuntimeReadOnlyPipelineReceipt",
    "PaperRuntimeReadOnlyPipelineResult",
    "collect_paper_runtime_readiness",
]
