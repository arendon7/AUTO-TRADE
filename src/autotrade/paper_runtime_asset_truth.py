from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

import autotrade.paper_runtime_broker_truth as broker_module
import autotrade.paper_runtime_candidate_identity as candidate_module
from autotrade.brokers.alpaca_paper_crypto_asset import (
    AlpacaPaperCryptoAssetAttestation,
    AlpacaPaperCryptoAssetGateway,
    crypto_asset_path,
    normalize_crypto_pair,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperReadTransport,
)
from autotrade.paper_runtime_broker_truth import PaperRuntimeBrokerTruthProof
from autotrade.paper_runtime_candidate_identity import PaperRuntimeCandidateIdentityProof


PAPER_RUNTIME_ASSET_TRUTH_VERSION = "W86_PAPER_RUNTIME_ASSET_TRUTH_V1"
ALPACA_PAPER_CRYPTO_MODEL_VENUE = "alpaca-paper-model"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")


class PaperRuntimeAssetTruthError(RuntimeError):
    pass


class PaperRuntimeAssetTruthIntegrityError(PaperRuntimeAssetTruthError):
    pass


@dataclass(frozen=True, slots=True)
class PaperRuntimeAssetTruthPolicy:
    max_asset_age_seconds: int = 30
    max_broker_asset_skew_seconds: int = 15

    def __post_init__(self) -> None:
        for label, value in (
            ("max_asset_age_seconds", self.max_asset_age_seconds),
            ("max_broker_asset_skew_seconds", self.max_broker_asset_skew_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 300:
                raise ValueError(f"{label} must be integer seconds in [1, 300]")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "max_asset_age_seconds": self.max_asset_age_seconds,
                "max_broker_asset_skew_seconds": self.max_broker_asset_skew_seconds,
            }
        )


@dataclass(frozen=True, slots=True)
class PaperRuntimeAssetTruthProof:
    proof_id: str
    contract_version: str
    candidate_identity_hash: str
    broker_truth_hash: str
    policy_hash: str
    authority_key: str
    admission_hash: str
    product_id: str
    candidate_symbol: str
    base_currency: str
    quote_currency: str
    canonical_broker_pair: str
    symbol_mapping_verified: bool
    account_id: str
    account_attestation_fingerprint: str
    credential_reference: str
    asset_attestation_fingerprint: str
    asset_contract_fingerprint: str
    asset_response_sha256: str
    asset_request_id: str
    asset_id: str
    asset_class: str
    exchange: str
    status: str
    tradable: bool
    fractionable: bool
    marginable: bool
    shortable: bool
    min_order_size: Decimal
    min_trade_increment: Decimal
    price_increment: Decimal
    source_host: str
    source_path: str
    broker_observed_at: datetime
    asset_observed_at: datetime
    observed_at: datetime
    asset_truth_valid_until: datetime
    asset_metadata_verified: bool
    read_only_asset_truth: bool
    network_write_performed: bool
    paper_runtime_ready: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    proof_hash: str

    def __post_init__(self) -> None:
        _require_id(self.proof_id, "proof_id")
        if self.contract_version != PAPER_RUNTIME_ASSET_TRUTH_VERSION:
            raise PaperRuntimeAssetTruthIntegrityError(
                "W86 asset truth version is not canonical"
            )
        for label, value in (
            ("candidate_identity_hash", self.candidate_identity_hash),
            ("broker_truth_hash", self.broker_truth_hash),
            ("policy_hash", self.policy_hash),
            ("authority_key", self.authority_key),
            ("admission_hash", self.admission_hash),
            ("account_attestation_fingerprint", self.account_attestation_fingerprint),
            ("credential_reference", self.credential_reference),
            ("asset_attestation_fingerprint", self.asset_attestation_fingerprint),
            ("asset_contract_fingerprint", self.asset_contract_fingerprint),
            ("asset_response_sha256", self.asset_response_sha256),
            ("proof_hash", self.proof_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("product_id", self.product_id),
            ("asset_id", self.asset_id),
            ("asset_class", self.asset_class),
            ("exchange", self.exchange),
            ("status", self.status),
        ):
            _require_id(value, label)
        if not isinstance(self.candidate_symbol, str) or not self.candidate_symbol:
            raise PaperRuntimeAssetTruthIntegrityError("candidate_symbol is required")
        expected_candidate_symbol = f"{self.base_currency}-{self.quote_currency}"
        if self.candidate_symbol != expected_candidate_symbol:
            raise PaperRuntimeAssetTruthIntegrityError(
                "candidate symbol is not exact BASE-QUOTE identity"
            )
        expected_pair = normalize_crypto_pair(
            f"{self.base_currency}/{self.quote_currency}"
        )
        if self.canonical_broker_pair != expected_pair:
            raise PaperRuntimeAssetTruthIntegrityError(
                "canonical broker pair does not match frozen candidate currencies"
            )
        if self.symbol_mapping_verified is not True:
            raise PaperRuntimeAssetTruthIntegrityError(
                "candidate-to-broker symbol mapping is not verified"
            )
        if self.asset_class != "crypto" or self.exchange != "CRYPTO":
            raise PaperRuntimeAssetTruthIntegrityError(
                "W86 asset truth requires canonical Alpaca crypto metadata"
            )
        if (
            self.status != "active"
            or self.tradable is not True
            or self.fractionable is not True
            or self.marginable is not False
            or self.shortable is not False
        ):
            raise PaperRuntimeAssetTruthIntegrityError(
                "crypto asset metadata is not eligible for long-only PAPER runtime use"
            )
        for label, value in (
            ("min_order_size", self.min_order_size),
            ("min_trade_increment", self.min_trade_increment),
            ("price_increment", self.price_increment),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise PaperRuntimeAssetTruthIntegrityError(
                    f"{label} must be finite positive Decimal"
                )
        if not _REQUEST_ID_RE.fullmatch(self.asset_request_id):
            raise PaperRuntimeAssetTruthIntegrityError("asset request id is invalid")
        if self.source_host != ALPACA_PAPER_TRADING_HOST:
            raise PaperRuntimeAssetTruthIntegrityError(
                "asset truth source host is not exact Alpaca PAPER host"
            )
        if self.source_path != crypto_asset_path(self.canonical_broker_pair):
            raise PaperRuntimeAssetTruthIntegrityError(
                "asset truth source path is not exact canonical pair endpoint"
            )
        for label, value in (
            ("broker_observed_at", self.broker_observed_at),
            ("asset_observed_at", self.asset_observed_at),
            ("observed_at", self.observed_at),
            ("asset_truth_valid_until", self.asset_truth_valid_until),
        ):
            _require_aware(value, label)
        if self.asset_truth_valid_until < self.observed_at:
            raise PaperRuntimeAssetTruthIntegrityError(
                "asset truth proof is already stale at observation time"
            )
        if self.asset_metadata_verified is not True or self.read_only_asset_truth is not True:
            raise PaperRuntimeAssetTruthIntegrityError(
                "W86 asset truth verification flags are incomplete"
            )
        _require_no_authority(
            network_write=self.network_write_performed,
            paper_runtime_ready=self.paper_runtime_ready,
            paper_execution=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
        )
        if self.proof_hash != _hash(_proof_payload(self, include_hash=False)):
            raise PaperRuntimeAssetTruthIntegrityError("W86 asset truth proof hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _proof_payload(self, include_hash=True)


def derive_alpaca_crypto_pair(
    candidate_identity: PaperRuntimeCandidateIdentityProof,
) -> str:
    """Derive broker pair only from the frozen W86 candidate; never from caller input."""

    _validate_candidate(candidate_identity)
    expected_symbol = f"{candidate_identity.base_currency}-{candidate_identity.quote_currency}"
    if candidate_identity.symbol != expected_symbol:
        raise PaperRuntimeAssetTruthIntegrityError(
            "candidate symbol is not exact BASE-QUOTE identity"
        )
    return normalize_crypto_pair(
        f"{candidate_identity.base_currency}/{candidate_identity.quote_currency}"
    )


def read_and_bind_paper_runtime_asset_truth(
    *,
    proof_id: str,
    candidate_identity: PaperRuntimeCandidateIdentityProof,
    broker_truth: PaperRuntimeBrokerTruthProof,
    credentials: AlpacaPaperCredentials,
    config: AlpacaPaperGatewayConfig,
    observed_at: datetime,
    policy: PaperRuntimeAssetTruthPolicy | None = None,
    transport: AlpacaPaperReadTransport | None = None,
) -> PaperRuntimeAssetTruthProof:
    """Perform one exact GET-only asset read and bind it to broker/candidate truth."""

    _require_id(proof_id, "proof_id")
    _validate_candidate(candidate_identity)
    _validate_broker_truth(broker_truth, candidate_identity, observed_at)
    if not isinstance(credentials, AlpacaPaperCredentials):
        raise TypeError("credentials must be AlpacaPaperCredentials")
    if not isinstance(config, AlpacaPaperGatewayConfig):
        raise TypeError("config must be AlpacaPaperGatewayConfig")
    if config.enabled is not True:
        raise PaperRuntimeAssetTruthIntegrityError(
            "W86 asset read requires explicitly enabled PAPER read config"
        )
    if config.base_url != f"https://{ALPACA_PAPER_TRADING_HOST}":
        raise PaperRuntimeAssetTruthIntegrityError(
            "W86 asset read requires exact Alpaca PAPER base URL"
        )
    if credentials.credential_reference != broker_truth.credential_reference:
        raise PaperRuntimeAssetTruthIntegrityError(
            "PAPER credentials differ from frozen broker-truth credential reference"
        )
    pair = derive_alpaca_crypto_pair(candidate_identity)
    asset = AlpacaPaperCryptoAssetGateway(
        config=config,
        transport=transport,
    ).attest_asset(
        credentials=credentials,
        account_attestation_fingerprint=broker_truth.account_attestation_fingerprint,
        expected_credential_reference=broker_truth.credential_reference,
        now=observed_at,
        symbol=pair,
    )
    return bind_paper_runtime_asset_truth(
        proof_id=proof_id,
        candidate_identity=candidate_identity,
        broker_truth=broker_truth,
        asset=asset,
        observed_at=observed_at,
        policy=policy,
    )


def bind_paper_runtime_asset_truth(
    *,
    proof_id: str,
    candidate_identity: PaperRuntimeCandidateIdentityProof,
    broker_truth: PaperRuntimeBrokerTruthProof,
    asset: AlpacaPaperCryptoAssetAttestation,
    observed_at: datetime,
    policy: PaperRuntimeAssetTruthPolicy | None = None,
) -> PaperRuntimeAssetTruthProof:
    """Bind exact Alpaca crypto metadata without minting runtime/execution authority."""

    _require_id(proof_id, "proof_id")
    _validate_candidate(candidate_identity)
    _require_aware(observed_at, "observed_at")
    _validate_broker_truth(broker_truth, candidate_identity, observed_at)
    if not isinstance(asset, AlpacaPaperCryptoAssetAttestation):
        raise TypeError("asset must be AlpacaPaperCryptoAssetAttestation")
    effective_policy = policy or PaperRuntimeAssetTruthPolicy()
    pair = derive_alpaca_crypto_pair(candidate_identity)
    _validate_asset(asset, pair, broker_truth)

    broker_time = broker_truth.observed_at.astimezone(timezone.utc)
    asset_time = asset.observed_at.astimezone(timezone.utc)
    process_time = observed_at.astimezone(timezone.utc)
    if asset_time > process_time:
        raise PaperRuntimeAssetTruthIntegrityError(
            "PAPER asset timestamp is in the process future"
        )
    if process_time - asset_time > timedelta(seconds=effective_policy.max_asset_age_seconds):
        raise PaperRuntimeAssetTruthIntegrityError("PAPER asset evidence is stale")
    if asset_time < broker_time:
        raise PaperRuntimeAssetTruthIntegrityError(
            "PAPER asset evidence predates frozen broker truth"
        )
    if asset_time - broker_time > timedelta(
        seconds=effective_policy.max_broker_asset_skew_seconds
    ):
        raise PaperRuntimeAssetTruthIntegrityError(
            "PAPER broker-to-asset reads exceed skew budget"
        )

    valid_until = min(
        broker_truth.broker_truth_valid_until,
        asset.observed_at + timedelta(seconds=effective_policy.max_asset_age_seconds),
    ).astimezone(timezone.utc)
    if valid_until < process_time:
        raise PaperRuntimeAssetTruthIntegrityError(
            "combined PAPER broker/asset evidence is stale"
        )

    values = {
        "proof_id": proof_id,
        "contract_version": PAPER_RUNTIME_ASSET_TRUTH_VERSION,
        "candidate_identity_hash": candidate_identity.proof_hash,
        "broker_truth_hash": broker_truth.proof_hash,
        "policy_hash": effective_policy.fingerprint,
        "authority_key": candidate_identity.authority_key,
        "admission_hash": candidate_identity.admission_hash,
        "product_id": candidate_identity.product_id,
        "candidate_symbol": candidate_identity.symbol,
        "base_currency": candidate_identity.base_currency,
        "quote_currency": candidate_identity.quote_currency,
        "canonical_broker_pair": pair,
        "symbol_mapping_verified": True,
        "account_id": broker_truth.account_id,
        "account_attestation_fingerprint": broker_truth.account_attestation_fingerprint,
        "credential_reference": broker_truth.credential_reference,
        "asset_attestation_fingerprint": asset.fingerprint,
        "asset_contract_fingerprint": asset.contract_fingerprint,
        "asset_response_sha256": asset.response_sha256,
        "asset_request_id": asset.request_id,
        "asset_id": asset.asset_id,
        "asset_class": asset.asset_class,
        "exchange": asset.exchange,
        "status": asset.status,
        "tradable": asset.tradable,
        "fractionable": asset.fractionable,
        "marginable": asset.marginable,
        "shortable": asset.shortable,
        "min_order_size": asset.min_order_size,
        "min_trade_increment": asset.min_trade_increment,
        "price_increment": asset.price_increment,
        "source_host": asset.source_host,
        "source_path": asset.source_path,
        "broker_observed_at": broker_time,
        "asset_observed_at": asset_time,
        "observed_at": process_time,
        "asset_truth_valid_until": valid_until,
        "asset_metadata_verified": True,
        "read_only_asset_truth": True,
        "network_write_performed": False,
        "paper_runtime_ready": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PaperRuntimeAssetTruthProof(
        **values,
        proof_hash=_hash(_payload_from_values(values)),
    )


def _validate_candidate(value: PaperRuntimeCandidateIdentityProof) -> None:
    if not isinstance(value, PaperRuntimeCandidateIdentityProof):
        raise TypeError("candidate_identity must be PaperRuntimeCandidateIdentityProof")
    expected = candidate_module._hash(candidate_module._payload(value, include_hash=False))
    if value.proof_hash != expected:
        raise PaperRuntimeAssetTruthIntegrityError(
            "W86 candidate identity proof hash mismatch"
        )
    if (
        value.asset_class != "crypto"
        or value.venue != ALPACA_PAPER_CRYPTO_MODEL_VENUE
        or value.quote_currency != "USD"
        or value.product_identity_verified is not True
        or value.strategy_runtime_identity_verified is not True
    ):
        raise PaperRuntimeAssetTruthIntegrityError(
            "candidate is not exact verified Alpaca PAPER crypto/USD product"
        )
    _require_no_authority(
        network_write=False,
        paper_runtime_ready=False,
        paper_execution=value.paper_execution_authorized,
        external=value.external_execution_authorized,
        runtime=value.runtime_execution_authorized,
        capital=value.capital_authority,
        live=value.live_trading,
    )


def _validate_broker_truth(
    value: PaperRuntimeBrokerTruthProof,
    candidate: PaperRuntimeCandidateIdentityProof,
    observed_at: datetime,
) -> None:
    if not isinstance(value, PaperRuntimeBrokerTruthProof):
        raise TypeError("broker_truth must be PaperRuntimeBrokerTruthProof")
    expected = broker_module._hash(broker_module._proof_payload(value, include_hash=False))
    if value.proof_hash != expected:
        raise PaperRuntimeAssetTruthIntegrityError("W86 broker truth proof hash mismatch")
    bindings = (
        (value.candidate_identity_hash, candidate.proof_hash, "candidate identity"),
        (value.authority_key, candidate.authority_key, "authority key"),
        (value.admission_hash, candidate.admission_hash, "admission"),
        (value.product_id, candidate.product_id, "product"),
        (value.asset_class, candidate.asset_class, "asset class"),
        (value.venue, candidate.venue, "venue"),
        (value.symbol, candidate.symbol, "symbol"),
        (value.quote_currency, candidate.quote_currency, "quote currency"),
    )
    for actual, expected_value, label in bindings:
        if actual != expected_value:
            raise PaperRuntimeAssetTruthIntegrityError(
                f"broker truth differs from candidate {label}"
            )
    if (
        value.account_environment_verified is not True
        or value.crypto_entitlement_verified is not True
        or value.portfolio_truth_verified is not True
        or value.read_only_broker_truth is not True
    ):
        raise PaperRuntimeAssetTruthIntegrityError(
            "broker truth is not fully verified read-only evidence"
        )
    _require_no_authority(
        network_write=value.network_write_performed,
        paper_runtime_ready=value.paper_runtime_ready,
        paper_execution=value.paper_execution_authorized,
        external=value.external_execution_authorized,
        runtime=value.runtime_execution_authorized,
        capital=value.capital_authority,
        live=value.live_trading,
    )
    _require_aware(observed_at, "observed_at")
    process_time = observed_at.astimezone(timezone.utc)
    broker_time = value.observed_at.astimezone(timezone.utc)
    if broker_time > process_time:
        raise PaperRuntimeAssetTruthIntegrityError(
            "broker truth observation is in the process future"
        )
    if value.broker_truth_valid_until.astimezone(timezone.utc) < process_time:
        raise PaperRuntimeAssetTruthIntegrityError("broker truth is stale")


def _validate_asset(
    value: AlpacaPaperCryptoAssetAttestation,
    pair: str,
    broker_truth: PaperRuntimeBrokerTruthProof,
) -> None:
    if value.symbol != pair:
        raise PaperRuntimeAssetTruthIntegrityError(
            "PAPER asset symbol differs from derived canonical pair"
        )
    if value.account_attestation_fingerprint != broker_truth.account_attestation_fingerprint:
        raise PaperRuntimeAssetTruthIntegrityError(
            "PAPER asset is bound to a different account attestation"
        )
    if value.credential_reference != broker_truth.credential_reference:
        raise PaperRuntimeAssetTruthIntegrityError(
            "PAPER asset is bound to a different credential reference"
        )
    if value.source_host != ALPACA_PAPER_TRADING_HOST:
        raise PaperRuntimeAssetTruthIntegrityError(
            "PAPER asset source host is not canonical"
        )
    if value.source_path != crypto_asset_path(pair):
        raise PaperRuntimeAssetTruthIntegrityError(
            "PAPER asset source path is not canonical"
        )
    if (
        value.asset_class != "crypto"
        or value.exchange != "CRYPTO"
        or value.status != "active"
        or value.tradable is not True
        or value.fractionable is not True
        or value.marginable is not False
        or value.shortable is not False
    ):
        raise PaperRuntimeAssetTruthIntegrityError(
            "PAPER crypto metadata is not active/tradable long-only"
        )
    for label, decimal_value in (
        ("min_order_size", value.min_order_size),
        ("min_trade_increment", value.min_trade_increment),
        ("price_increment", value.price_increment),
    ):
        if (
            not isinstance(decimal_value, Decimal)
            or not decimal_value.is_finite()
            or decimal_value <= 0
        ):
            raise PaperRuntimeAssetTruthIntegrityError(
                f"PAPER asset {label} must be finite and positive"
            )
    _require_hash(value.response_sha256, "asset response hash")
    _require_hash(value.fingerprint, "asset attestation fingerprint")
    _require_hash(value.contract_fingerprint, "asset contract fingerprint")
    if not _REQUEST_ID_RE.fullmatch(value.request_id):
        raise PaperRuntimeAssetTruthIntegrityError("asset request id is invalid")
    _require_aware(value.observed_at, "asset.observed_at")


def _proof_payload(
    value: PaperRuntimeAssetTruthProof, *, include_hash: bool
) -> dict[str, object]:
    names = tuple(
        name
        for name in PaperRuntimeAssetTruthProof.__dataclass_fields__
        if name != "proof_hash"
    )
    payload = _payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["proof_hash"] = value.proof_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            payload[key] = value.astimezone(timezone.utc).isoformat()
        elif isinstance(value, Decimal):
            payload[key] = str(value)
        else:
            payload[key] = value
    return payload


def _hash(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperRuntimeAssetTruthIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeAssetTruthIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperRuntimeAssetTruthIntegrityError(
            f"{label} must be timezone-aware"
        )


def _require_no_authority(
    *,
    network_write: bool,
    paper_runtime_ready: bool,
    paper_execution: bool,
    external: bool,
    runtime: bool,
    capital: str,
    live: str,
) -> None:
    if (
        network_write is not False
        or paper_runtime_ready is not False
        or paper_execution is not False
        or external is not False
        or runtime is not False
        or capital != "NONE"
        or live != "BLOCKED"
    ):
        raise PaperRuntimeAssetTruthIntegrityError(
            "W86 asset truth may not grant readiness, execution, capital or LIVE authority"
        )


__all__ = [
    "PAPER_RUNTIME_ASSET_TRUTH_VERSION",
    "PaperRuntimeAssetTruthError",
    "PaperRuntimeAssetTruthIntegrityError",
    "PaperRuntimeAssetTruthPolicy",
    "PaperRuntimeAssetTruthProof",
    "bind_paper_runtime_asset_truth",
    "derive_alpaca_crypto_pair",
    "read_and_bind_paper_runtime_asset_truth",
]
