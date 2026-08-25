from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

import autotrade.paper_runtime_asset_truth as asset_module
import autotrade.paper_runtime_broker_truth as broker_module
import autotrade.paper_runtime_candidate_identity as candidate_module
from autotrade.brokers.alpaca_paper_crypto_asset import normalize_crypto_pair
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    CRYPTO_LOCATION,
    LATEST_QUOTE_PATH,
    LATEST_TRADE_PATH,
    AlpacaPaperCryptoMarketAttestation,
    AlpacaPaperCryptoMarketDataConfig,
    AlpacaPaperCryptoMarketDataGateway,
    crypto_exact_query,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_market_data import (
    ALPACA_MARKET_DATA_HOST,
    AlpacaPaperMarketDataTransport,
)
from autotrade.domain import MarketSnapshot, market_fingerprint
from autotrade.paper_runtime_asset_truth import PaperRuntimeAssetTruthProof
from autotrade.paper_runtime_broker_truth import PaperRuntimeBrokerTruthProof
from autotrade.paper_runtime_candidate_identity import PaperRuntimeCandidateIdentityProof


PAPER_RUNTIME_MARKET_TRUTH_VERSION = "W86_PAPER_RUNTIME_MARKET_TRUTH_V1"
ALPACA_PAPER_CRYPTO_MODEL_VENUE = "alpaca-paper-model"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_ACCOUNT_ID_RE = re.compile(r"^[0-9a-fA-F-]{16,64}$")


class PaperRuntimeMarketTruthError(RuntimeError):
    pass


class PaperRuntimeMarketTruthIntegrityError(PaperRuntimeMarketTruthError):
    pass


@dataclass(frozen=True, slots=True)
class PaperRuntimeMarketTruthPolicy:
    max_quote_age_seconds: int = 5
    max_trade_age_seconds: int = 5
    max_market_receipt_age_seconds: int = 5
    max_asset_market_skew_seconds: int = 15

    def __post_init__(self) -> None:
        for label, value in (
            ("max_quote_age_seconds", self.max_quote_age_seconds),
            ("max_trade_age_seconds", self.max_trade_age_seconds),
            ("max_market_receipt_age_seconds", self.max_market_receipt_age_seconds),
            ("max_asset_market_skew_seconds", self.max_asset_market_skew_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 30:
                raise ValueError(f"{label} must be integer seconds in [1, 30]")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "max_quote_age_seconds": self.max_quote_age_seconds,
                "max_trade_age_seconds": self.max_trade_age_seconds,
                "max_market_receipt_age_seconds": self.max_market_receipt_age_seconds,
                "max_asset_market_skew_seconds": self.max_asset_market_skew_seconds,
            }
        )


@dataclass(frozen=True, slots=True)
class PaperRuntimeMarketTruthProof:
    proof_id: str
    contract_version: str
    candidate_identity_hash: str
    broker_truth_hash: str
    asset_truth_hash: str
    policy_hash: str
    authority_key: str
    admission_hash: str
    product_id: str
    venue: str
    candidate_symbol: str
    base_currency: str
    quote_currency: str
    canonical_broker_pair: str
    account_id: str
    credential_reference: str
    asset_attestation_fingerprint: str
    market_attestation_fingerprint: str
    market_snapshot_fingerprint: str
    source_host: str
    location: str
    quote_path: str
    trade_path: str
    exact_query: str
    quote_response_sha256: str
    trade_response_sha256: str
    bid_price: Decimal
    ask_price: Decimal
    trade_price: Decimal
    quote_observed_at: datetime
    trade_observed_at: datetime
    market_received_at: datetime
    asset_observed_at: datetime
    observed_at: datetime
    quote_age_seconds: Decimal
    trade_age_seconds: Decimal
    market_receipt_age_seconds: Decimal
    market_truth_valid_until: datetime
    quote_fresh: bool
    trade_fresh: bool
    both_sides_fresh: bool
    market_truth_verified: bool
    read_only_market_truth: bool
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
        if self.contract_version != PAPER_RUNTIME_MARKET_TRUTH_VERSION:
            raise PaperRuntimeMarketTruthIntegrityError(
                "W86 market truth version is not canonical"
            )
        for label, value in (
            ("candidate_identity_hash", self.candidate_identity_hash),
            ("broker_truth_hash", self.broker_truth_hash),
            ("asset_truth_hash", self.asset_truth_hash),
            ("policy_hash", self.policy_hash),
            ("authority_key", self.authority_key),
            ("admission_hash", self.admission_hash),
            ("credential_reference", self.credential_reference),
            ("asset_attestation_fingerprint", self.asset_attestation_fingerprint),
            ("market_attestation_fingerprint", self.market_attestation_fingerprint),
            ("market_snapshot_fingerprint", self.market_snapshot_fingerprint),
            ("quote_response_sha256", self.quote_response_sha256),
            ("trade_response_sha256", self.trade_response_sha256),
            ("proof_hash", self.proof_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("product_id", self.product_id),
            ("venue", self.venue),
        ):
            _require_id(value, label)
        if not _ACCOUNT_ID_RE.fullmatch(self.account_id):
            raise PaperRuntimeMarketTruthIntegrityError("account_id is invalid")
        if self.venue != ALPACA_PAPER_CRYPTO_MODEL_VENUE or self.quote_currency != "USD":
            raise PaperRuntimeMarketTruthIntegrityError(
                "W86 market truth supports only frozen Alpaca PAPER crypto/USD product"
            )
        expected_symbol = f"{self.base_currency}-{self.quote_currency}"
        if self.candidate_symbol != expected_symbol:
            raise PaperRuntimeMarketTruthIntegrityError(
                "candidate symbol is not exact BASE-QUOTE identity"
            )
        expected_pair = normalize_crypto_pair(
            f"{self.base_currency}/{self.quote_currency}"
        )
        if self.canonical_broker_pair != expected_pair:
            raise PaperRuntimeMarketTruthIntegrityError(
                "market pair differs from frozen candidate currencies"
            )
        if self.source_host != ALPACA_MARKET_DATA_HOST:
            raise PaperRuntimeMarketTruthIntegrityError(
                "market truth source host is not canonical Alpaca data host"
            )
        if self.location != CRYPTO_LOCATION:
            raise PaperRuntimeMarketTruthIntegrityError(
                "market truth location is not canonical Alpaca crypto location"
            )
        if (
            self.quote_path != LATEST_QUOTE_PATH
            or self.trade_path != LATEST_TRADE_PATH
            or self.exact_query != crypto_exact_query(self.canonical_broker_pair)
        ):
            raise PaperRuntimeMarketTruthIntegrityError(
                "market truth endpoint provenance is not exact canonical GET pair"
            )
        for label, value in (
            ("bid_price", self.bid_price),
            ("ask_price", self.ask_price),
            ("trade_price", self.trade_price),
        ):
            _require_positive_decimal(value, label)
        if self.bid_price > self.ask_price:
            raise PaperRuntimeMarketTruthIntegrityError("market bid exceeds ask")
        for label, value in (
            ("quote_observed_at", self.quote_observed_at),
            ("trade_observed_at", self.trade_observed_at),
            ("market_received_at", self.market_received_at),
            ("asset_observed_at", self.asset_observed_at),
            ("observed_at", self.observed_at),
            ("market_truth_valid_until", self.market_truth_valid_until),
        ):
            _require_aware(value, label)
        expected_quote_age = _age_seconds(self.market_received_at, self.quote_observed_at)
        expected_trade_age = _age_seconds(self.market_received_at, self.trade_observed_at)
        expected_receipt_age = _age_seconds(self.observed_at, self.market_received_at)
        if self.quote_age_seconds != expected_quote_age:
            raise PaperRuntimeMarketTruthIntegrityError("quote age is inconsistent")
        if self.trade_age_seconds != expected_trade_age:
            raise PaperRuntimeMarketTruthIntegrityError("trade age is inconsistent")
        if self.market_receipt_age_seconds != expected_receipt_age:
            raise PaperRuntimeMarketTruthIntegrityError("market receipt age is inconsistent")
        for label, value in (
            ("quote_age_seconds", self.quote_age_seconds),
            ("trade_age_seconds", self.trade_age_seconds),
            ("market_receipt_age_seconds", self.market_receipt_age_seconds),
        ):
            if not value.is_finite() or value < 0:
                raise PaperRuntimeMarketTruthIntegrityError(
                    f"{label} must be finite and non-negative"
                )
        if self.market_received_at < self.asset_observed_at:
            raise PaperRuntimeMarketTruthIntegrityError(
                "market receipt predates frozen asset truth"
            )
        if self.market_received_at > self.observed_at:
            raise PaperRuntimeMarketTruthIntegrityError(
                "market receipt is in process future"
            )
        if self.quote_observed_at > self.market_received_at:
            raise PaperRuntimeMarketTruthIntegrityError(
                "market quote timestamp is in receipt future"
            )
        if self.trade_observed_at > self.market_received_at:
            raise PaperRuntimeMarketTruthIntegrityError(
                "market trade timestamp is in receipt future"
            )
        if self.market_truth_valid_until < self.observed_at:
            raise PaperRuntimeMarketTruthIntegrityError(
                "market truth proof is already stale at observation time"
            )
        snapshot = MarketSnapshot(
            symbol=self.canonical_broker_pair,
            bid=self.bid_price,
            ask=self.ask_price,
            last=self.trade_price,
            observed_at=self.market_received_at,
        )
        if self.market_snapshot_fingerprint != market_fingerprint(snapshot):
            raise PaperRuntimeMarketTruthIntegrityError(
                "market snapshot fingerprint is inconsistent"
            )
        attestation_payload = {
            "market_fingerprint": self.market_snapshot_fingerprint,
            "location": self.location,
            "quote_observed_at": self.quote_observed_at.astimezone(timezone.utc).isoformat(),
            "trade_observed_at": self.trade_observed_at.astimezone(timezone.utc).isoformat(),
            "received_at": self.market_received_at.astimezone(timezone.utc).isoformat(),
            "quote_response_sha256": self.quote_response_sha256,
            "trade_response_sha256": self.trade_response_sha256,
            "source_host": self.source_host,
        }
        if self.market_attestation_fingerprint != _hash(attestation_payload):
            raise PaperRuntimeMarketTruthIntegrityError(
                "market attestation fingerprint is inconsistent"
            )
        if (
            self.quote_fresh is not True
            or self.trade_fresh is not True
            or self.both_sides_fresh is not True
            or self.market_truth_verified is not True
            or self.read_only_market_truth is not True
        ):
            raise PaperRuntimeMarketTruthIntegrityError(
                "W86 market truth requires both quote and trade fresh"
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
            raise PaperRuntimeMarketTruthIntegrityError(
                "W86 market truth proof hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _proof_payload(self, include_hash=True)


def read_and_bind_paper_runtime_market_truth(
    *,
    proof_id: str,
    candidate_identity: PaperRuntimeCandidateIdentityProof,
    broker_truth: PaperRuntimeBrokerTruthProof,
    asset_truth: PaperRuntimeAssetTruthProof,
    credentials: AlpacaPaperCredentials,
    observed_at: datetime,
    policy: PaperRuntimeMarketTruthPolicy | None = None,
    gateway_config: AlpacaPaperCryptoMarketDataConfig | None = None,
    transport: AlpacaPaperMarketDataTransport | None = None,
) -> PaperRuntimeMarketTruthProof:
    """Read exact latest quote+trade for frozen pair; never mint execution authority."""

    _require_id(proof_id, "proof_id")
    _require_aware(observed_at, "observed_at")
    _validate_chain(candidate_identity, broker_truth, asset_truth, observed_at)
    if not isinstance(credentials, AlpacaPaperCredentials):
        raise TypeError("credentials must be AlpacaPaperCredentials")
    if credentials.credential_reference != asset_truth.credential_reference:
        raise PaperRuntimeMarketTruthIntegrityError(
            "PAPER credentials differ from frozen asset-truth credential reference"
        )
    config = gateway_config or AlpacaPaperCryptoMarketDataConfig(enabled=True)
    if not isinstance(config, AlpacaPaperCryptoMarketDataConfig):
        raise TypeError("gateway_config must be AlpacaPaperCryptoMarketDataConfig")
    if config.enabled is not True:
        raise PaperRuntimeMarketTruthIntegrityError(
            "W86 market read requires explicitly enabled read-only config"
        )
    pair = _derive_pair(candidate_identity, asset_truth)
    market = AlpacaPaperCryptoMarketDataGateway(
        config=config,
        transport=transport,
    ).attest_snapshot(
        credentials=credentials,
        now=observed_at,
        symbol=pair,
    )
    return bind_paper_runtime_market_truth(
        proof_id=proof_id,
        candidate_identity=candidate_identity,
        broker_truth=broker_truth,
        asset_truth=asset_truth,
        market=market,
        observed_at=observed_at,
        policy=policy,
    )


def bind_paper_runtime_market_truth(
    *,
    proof_id: str,
    candidate_identity: PaperRuntimeCandidateIdentityProof,
    broker_truth: PaperRuntimeBrokerTruthProof,
    asset_truth: PaperRuntimeAssetTruthProof,
    market: AlpacaPaperCryptoMarketAttestation,
    observed_at: datetime,
    policy: PaperRuntimeMarketTruthPolicy | None = None,
) -> PaperRuntimeMarketTruthProof:
    """Bind market bytes to exact W86 chain and require both quote and trade fresh."""

    _require_id(proof_id, "proof_id")
    _require_aware(observed_at, "observed_at")
    _validate_chain(candidate_identity, broker_truth, asset_truth, observed_at)
    if not isinstance(market, AlpacaPaperCryptoMarketAttestation):
        raise TypeError("market must be AlpacaPaperCryptoMarketAttestation")
    effective_policy = policy or PaperRuntimeMarketTruthPolicy()
    pair = _derive_pair(candidate_identity, asset_truth)
    _validate_market_attestation(market, pair)

    process_time = observed_at.astimezone(timezone.utc)
    asset_time = asset_truth.observed_at.astimezone(timezone.utc)
    receipt_time = market.received_at.astimezone(timezone.utc)
    quote_time = market.quote_observed_at.astimezone(timezone.utc)
    trade_time = market.trade_observed_at.astimezone(timezone.utc)
    if receipt_time < asset_time:
        raise PaperRuntimeMarketTruthIntegrityError(
            "PAPER market receipt predates frozen asset truth"
        )
    if receipt_time - asset_time > timedelta(
        seconds=effective_policy.max_asset_market_skew_seconds
    ):
        raise PaperRuntimeMarketTruthIntegrityError(
            "PAPER asset-to-market reads exceed skew budget"
        )
    if receipt_time > process_time:
        raise PaperRuntimeMarketTruthIntegrityError(
            "PAPER market receipt is in process future"
        )
    receipt_age = _age_seconds(process_time, receipt_time)
    if receipt_age > Decimal(effective_policy.max_market_receipt_age_seconds):
        raise PaperRuntimeMarketTruthIntegrityError(
            "PAPER market receipt is stale"
        )
    quote_age = _age_seconds(receipt_time, quote_time)
    trade_age = _age_seconds(receipt_time, trade_time)
    if quote_age < 0:
        raise PaperRuntimeMarketTruthIntegrityError(
            "PAPER quote timestamp is in receipt future"
        )
    if trade_age < 0:
        raise PaperRuntimeMarketTruthIntegrityError(
            "PAPER trade timestamp is in receipt future"
        )
    if quote_age > Decimal(effective_policy.max_quote_age_seconds):
        raise PaperRuntimeMarketTruthIntegrityError(
            "PAPER quote is stale for W86 readiness evidence"
        )
    if trade_age > Decimal(effective_policy.max_trade_age_seconds):
        raise PaperRuntimeMarketTruthIntegrityError(
            "PAPER trade is stale for W86 readiness evidence"
        )

    valid_until = min(
        asset_truth.asset_truth_valid_until.astimezone(timezone.utc),
        quote_time + timedelta(seconds=effective_policy.max_quote_age_seconds),
        trade_time + timedelta(seconds=effective_policy.max_trade_age_seconds),
        receipt_time + timedelta(seconds=effective_policy.max_market_receipt_age_seconds),
    )
    if valid_until < process_time:
        raise PaperRuntimeMarketTruthIntegrityError(
            "combined PAPER asset/market evidence is stale"
        )

    snapshot_fingerprint = market_fingerprint(market.market)
    values = {
        "proof_id": proof_id,
        "contract_version": PAPER_RUNTIME_MARKET_TRUTH_VERSION,
        "candidate_identity_hash": candidate_identity.proof_hash,
        "broker_truth_hash": broker_truth.proof_hash,
        "asset_truth_hash": asset_truth.proof_hash,
        "policy_hash": effective_policy.fingerprint,
        "authority_key": candidate_identity.authority_key,
        "admission_hash": candidate_identity.admission_hash,
        "product_id": candidate_identity.product_id,
        "venue": candidate_identity.venue,
        "candidate_symbol": candidate_identity.symbol,
        "base_currency": candidate_identity.base_currency,
        "quote_currency": candidate_identity.quote_currency,
        "canonical_broker_pair": pair,
        "account_id": broker_truth.account_id,
        "credential_reference": asset_truth.credential_reference,
        "asset_attestation_fingerprint": asset_truth.asset_attestation_fingerprint,
        "market_attestation_fingerprint": market.fingerprint,
        "market_snapshot_fingerprint": snapshot_fingerprint,
        "source_host": market.source_host,
        "location": market.location,
        "quote_path": LATEST_QUOTE_PATH,
        "trade_path": LATEST_TRADE_PATH,
        "exact_query": crypto_exact_query(pair),
        "quote_response_sha256": market.quote_response_sha256,
        "trade_response_sha256": market.trade_response_sha256,
        "bid_price": market.market.bid,
        "ask_price": market.market.ask,
        "trade_price": market.market.last,
        "quote_observed_at": quote_time,
        "trade_observed_at": trade_time,
        "market_received_at": receipt_time,
        "asset_observed_at": asset_time,
        "observed_at": process_time,
        "quote_age_seconds": quote_age,
        "trade_age_seconds": trade_age,
        "market_receipt_age_seconds": receipt_age,
        "market_truth_valid_until": valid_until,
        "quote_fresh": True,
        "trade_fresh": True,
        "both_sides_fresh": True,
        "market_truth_verified": True,
        "read_only_market_truth": True,
        "network_write_performed": False,
        "paper_runtime_ready": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PaperRuntimeMarketTruthProof(
        **values,
        proof_hash=_hash(_payload_from_values(values)),
    )


def _validate_chain(
    candidate: PaperRuntimeCandidateIdentityProof,
    broker: PaperRuntimeBrokerTruthProof,
    asset: PaperRuntimeAssetTruthProof,
    observed_at: datetime,
) -> None:
    if not isinstance(candidate, PaperRuntimeCandidateIdentityProof):
        raise TypeError("candidate_identity must be PaperRuntimeCandidateIdentityProof")
    candidate_expected = candidate_module._hash(
        candidate_module._payload(candidate, include_hash=False)
    )
    if candidate.proof_hash != candidate_expected:
        raise PaperRuntimeMarketTruthIntegrityError(
            "W86 candidate identity proof hash mismatch"
        )
    if not isinstance(broker, PaperRuntimeBrokerTruthProof):
        raise TypeError("broker_truth must be PaperRuntimeBrokerTruthProof")
    broker_expected = broker_module._hash(
        broker_module._proof_payload(broker, include_hash=False)
    )
    if broker.proof_hash != broker_expected:
        raise PaperRuntimeMarketTruthIntegrityError("W86 broker truth proof hash mismatch")
    if not isinstance(asset, PaperRuntimeAssetTruthProof):
        raise TypeError("asset_truth must be PaperRuntimeAssetTruthProof")
    asset_expected = asset_module._hash(
        asset_module._proof_payload(asset, include_hash=False)
    )
    if asset.proof_hash != asset_expected:
        raise PaperRuntimeMarketTruthIntegrityError("W86 asset truth proof hash mismatch")

    if (
        candidate.asset_class != "crypto"
        or candidate.venue != ALPACA_PAPER_CRYPTO_MODEL_VENUE
        or candidate.quote_currency != "USD"
        or candidate.product_identity_verified is not True
        or candidate.strategy_runtime_identity_verified is not True
    ):
        raise PaperRuntimeMarketTruthIntegrityError(
            "candidate is not exact verified Alpaca PAPER crypto/USD product"
        )
    chain = (
        (broker.candidate_identity_hash, candidate.proof_hash, "broker candidate hash"),
        (asset.candidate_identity_hash, candidate.proof_hash, "asset candidate hash"),
        (asset.broker_truth_hash, broker.proof_hash, "asset broker hash"),
        (broker.authority_key, candidate.authority_key, "broker authority key"),
        (asset.authority_key, candidate.authority_key, "asset authority key"),
        (broker.admission_hash, candidate.admission_hash, "broker admission"),
        (asset.admission_hash, candidate.admission_hash, "asset admission"),
        (broker.product_id, candidate.product_id, "broker product"),
        (asset.product_id, candidate.product_id, "asset product"),
        (broker.venue, candidate.venue, "broker venue"),
        (asset.venue, candidate.venue, "asset venue"),
        (broker.symbol, candidate.symbol, "broker symbol"),
        (asset.candidate_symbol, candidate.symbol, "asset symbol"),
        (broker.quote_currency, candidate.quote_currency, "broker quote currency"),
        (asset.quote_currency, candidate.quote_currency, "asset quote currency"),
        (asset.account_id, broker.account_id, "asset account"),
        (
            asset.account_attestation_fingerprint,
            broker.account_attestation_fingerprint,
            "asset account attestation",
        ),
        (asset.credential_reference, broker.credential_reference, "asset credential"),
    )
    for actual, expected, label in chain:
        if actual != expected:
            raise PaperRuntimeMarketTruthIntegrityError(
                f"W86 market chain mismatch: {label}"
            )
    if (
        broker.read_only_broker_truth is not True
        or broker.account_environment_verified is not True
        or broker.crypto_entitlement_verified is not True
        or broker.portfolio_truth_verified is not True
        or asset.read_only_asset_truth is not True
        or asset.asset_metadata_verified is not True
        or asset.symbol_mapping_verified is not True
    ):
        raise PaperRuntimeMarketTruthIntegrityError(
            "upstream W86 broker/asset evidence is not fully verified read-only truth"
        )
    _require_no_authority(
        network_write=broker.network_write_performed or asset.network_write_performed,
        paper_runtime_ready=broker.paper_runtime_ready or asset.paper_runtime_ready,
        paper_execution=(
            candidate.paper_execution_authorized
            or broker.paper_execution_authorized
            or asset.paper_execution_authorized
        ),
        external=(
            candidate.external_execution_authorized
            or broker.external_execution_authorized
            or asset.external_execution_authorized
        ),
        runtime=(
            candidate.runtime_execution_authorized
            or broker.runtime_execution_authorized
            or asset.runtime_execution_authorized
        ),
        capital=(
            candidate.capital_authority
            if candidate.capital_authority != "NONE"
            else broker.capital_authority
            if broker.capital_authority != "NONE"
            else asset.capital_authority
        ),
        live=(
            candidate.live_trading
            if candidate.live_trading != "BLOCKED"
            else broker.live_trading
            if broker.live_trading != "BLOCKED"
            else asset.live_trading
        ),
    )
    process_time = observed_at.astimezone(timezone.utc)
    if asset.asset_truth_valid_until.astimezone(timezone.utc) < process_time:
        raise PaperRuntimeMarketTruthIntegrityError("W86 asset truth is stale")


def _derive_pair(
    candidate: PaperRuntimeCandidateIdentityProof,
    asset: PaperRuntimeAssetTruthProof,
) -> str:
    expected_symbol = f"{candidate.base_currency}-{candidate.quote_currency}"
    if candidate.symbol != expected_symbol:
        raise PaperRuntimeMarketTruthIntegrityError(
            "candidate symbol is not exact BASE-QUOTE identity"
        )
    pair = normalize_crypto_pair(f"{candidate.base_currency}/{candidate.quote_currency}")
    if asset.canonical_broker_pair != pair:
        raise PaperRuntimeMarketTruthIntegrityError(
            "asset truth canonical pair differs from frozen candidate"
        )
    return pair


def _validate_market_attestation(
    market: AlpacaPaperCryptoMarketAttestation,
    pair: str,
) -> None:
    if market.market.symbol != pair:
        raise PaperRuntimeMarketTruthIntegrityError(
            "PAPER market symbol differs from frozen canonical pair"
        )
    if market.location != CRYPTO_LOCATION or market.source_host != ALPACA_MARKET_DATA_HOST:
        raise PaperRuntimeMarketTruthIntegrityError(
            "PAPER market source is not canonical Alpaca crypto data"
        )
    if market.market.observed_at.astimezone(timezone.utc) != market.received_at.astimezone(
        timezone.utc
    ):
        raise PaperRuntimeMarketTruthIntegrityError(
            "W86 requires market snapshot observed_at to equal REST receipt time"
        )
    for label, value in (
        ("bid", market.market.bid),
        ("ask", market.market.ask),
        ("last", market.market.last),
    ):
        _require_positive_decimal(value, label)
    if market.market.bid > market.market.ask:
        raise PaperRuntimeMarketTruthIntegrityError("PAPER market bid exceeds ask")
    for label, value in (
        ("quote_response_sha256", market.quote_response_sha256),
        ("trade_response_sha256", market.trade_response_sha256),
        ("market_attestation_fingerprint", market.fingerprint),
    ):
        _require_hash(value, label)
    for label, value in (
        ("quote_observed_at", market.quote_observed_at),
        ("trade_observed_at", market.trade_observed_at),
        ("received_at", market.received_at),
        ("market.observed_at", market.market.observed_at),
    ):
        _require_aware(value, label)


def _proof_payload(
    value: PaperRuntimeMarketTruthProof,
    *,
    include_hash: bool,
) -> dict[str, object]:
    names = tuple(
        name
        for name in PaperRuntimeMarketTruthProof.__dataclass_fields__
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


def _age_seconds(later: datetime, earlier: datetime) -> Decimal:
    _require_aware(later, "later")
    _require_aware(earlier, "earlier")
    delta = later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)
    micros = (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )
    return Decimal(micros) / Decimal(1_000_000)


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperRuntimeMarketTruthIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeMarketTruthIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperRuntimeMarketTruthIntegrityError(
            f"{label} must be timezone-aware"
        )


def _require_positive_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise PaperRuntimeMarketTruthIntegrityError(
            f"{label} must be finite positive Decimal"
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
        raise PaperRuntimeMarketTruthIntegrityError(
            "W86 market truth may not grant readiness, execution, capital or LIVE authority"
        )


__all__ = [
    "PAPER_RUNTIME_MARKET_TRUTH_VERSION",
    "PaperRuntimeMarketTruthError",
    "PaperRuntimeMarketTruthIntegrityError",
    "PaperRuntimeMarketTruthPolicy",
    "PaperRuntimeMarketTruthProof",
    "bind_paper_runtime_market_truth",
    "read_and_bind_paper_runtime_market_truth",
]
