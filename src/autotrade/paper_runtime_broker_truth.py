from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

import autotrade.paper_runtime_candidate_identity as candidate_module
from autotrade.brokers.alpaca_paper_crypto_account_status import (
    AlpacaPaperCryptoAccountStatusAttestation,
    attest_active_crypto_account,
)
from autotrade.brokers.alpaca_paper_flat_account import (
    ORDERS_PATH,
    ORDERS_QUERY,
    POSITIONS_PATH,
    AlpacaPaperFlatAccountGateway,
    PaperFlatAccountAttestation,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperReadTransport,
    AlpacaPaperAccountGateway,
)
from autotrade.paper_runtime_candidate_identity import PaperRuntimeCandidateIdentityProof


PAPER_RUNTIME_BROKER_TRUTH_VERSION = "W86_PAPER_RUNTIME_BROKER_TRUTH_V1"
ALPACA_PAPER_CRYPTO_MODEL_VENUE = "alpaca-paper-model"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_ACCOUNT_ID_RE = re.compile(r"^[0-9a-fA-F-]{16,64}$")
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")


class PaperRuntimeBrokerTruthError(RuntimeError):
    pass


class PaperRuntimeBrokerTruthIntegrityError(PaperRuntimeBrokerTruthError):
    pass


@dataclass(frozen=True, slots=True)
class PaperRuntimeBrokerTruthPolicy:
    max_account_age_seconds: int = 30
    max_crypto_status_age_seconds: int = 30
    max_portfolio_age_seconds: int = 30
    max_cross_read_skew_seconds: int = 15

    def __post_init__(self) -> None:
        for label, value in (
            ("max_account_age_seconds", self.max_account_age_seconds),
            ("max_crypto_status_age_seconds", self.max_crypto_status_age_seconds),
            ("max_portfolio_age_seconds", self.max_portfolio_age_seconds),
            ("max_cross_read_skew_seconds", self.max_cross_read_skew_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 300:
                raise ValueError(f"{label} must be integer seconds in [1, 300]")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "max_account_age_seconds": self.max_account_age_seconds,
                "max_crypto_status_age_seconds": self.max_crypto_status_age_seconds,
                "max_portfolio_age_seconds": self.max_portfolio_age_seconds,
                "max_cross_read_skew_seconds": self.max_cross_read_skew_seconds,
            }
        )


@dataclass(frozen=True, slots=True)
class PaperRuntimeBrokerTruthProof:
    proof_id: str
    contract_version: str
    candidate_identity_hash: str
    policy_hash: str
    authority_key: str
    admission_hash: str
    product_id: str
    asset_class: str
    venue: str
    symbol: str
    quote_currency: str
    account_id: str
    account_reference: str
    credential_reference: str
    account_attestation_fingerprint: str
    account_request_id: str
    account_attested_at: datetime
    crypto_status_fingerprint: str
    crypto_status_request_id: str
    crypto_status_response_sha256: str
    crypto_status_observed_at: datetime
    flat_account_fingerprint: str
    position_count: int
    open_order_count: int
    positions_response_hash: str
    orders_response_hash: str
    positions_request_id: str
    orders_request_id: str
    portfolio_attested_at: datetime
    source_host: str
    account_path: str
    positions_path: str
    orders_path: str
    observed_at: datetime
    broker_truth_valid_until: datetime
    account_environment_verified: bool
    crypto_entitlement_verified: bool
    portfolio_truth_verified: bool
    clean_for_candidate_start: bool
    read_only_broker_truth: bool
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
        if self.contract_version != PAPER_RUNTIME_BROKER_TRUTH_VERSION:
            raise PaperRuntimeBrokerTruthIntegrityError(
                "W86 broker truth version is not canonical"
            )
        for label, value in (
            ("candidate_identity_hash", self.candidate_identity_hash),
            ("policy_hash", self.policy_hash),
            ("authority_key", self.authority_key),
            ("admission_hash", self.admission_hash),
            ("account_reference", self.account_reference),
            ("credential_reference", self.credential_reference),
            ("account_attestation_fingerprint", self.account_attestation_fingerprint),
            ("crypto_status_fingerprint", self.crypto_status_fingerprint),
            ("crypto_status_response_sha256", self.crypto_status_response_sha256),
            ("flat_account_fingerprint", self.flat_account_fingerprint),
            ("positions_response_hash", self.positions_response_hash),
            ("orders_response_hash", self.orders_response_hash),
            ("proof_hash", self.proof_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("product_id", self.product_id),
            ("asset_class", self.asset_class),
            ("venue", self.venue),
        ):
            _require_id(value, label)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise PaperRuntimeBrokerTruthIntegrityError("symbol is required")
        if self.asset_class != "crypto" or self.venue != ALPACA_PAPER_CRYPTO_MODEL_VENUE:
            raise PaperRuntimeBrokerTruthIntegrityError(
                "W86 broker truth supports only the frozen Alpaca PAPER crypto model"
            )
        if self.quote_currency != "USD":
            raise PaperRuntimeBrokerTruthIntegrityError(
                "W86 Alpaca PAPER broker truth requires USD quote/account currency"
            )
        if not _ACCOUNT_ID_RE.fullmatch(self.account_id):
            raise PaperRuntimeBrokerTruthIntegrityError("account_id is invalid")
        for request_id in (
            self.account_request_id,
            self.crypto_status_request_id,
            self.positions_request_id,
            self.orders_request_id,
        ):
            if not _REQUEST_ID_RE.fullmatch(request_id):
                raise PaperRuntimeBrokerTruthIntegrityError("broker request id is invalid")
        if (
            isinstance(self.position_count, bool)
            or isinstance(self.open_order_count, bool)
            or not isinstance(self.position_count, int)
            or not isinstance(self.open_order_count, int)
            or self.position_count < 0
            or self.open_order_count < 0
        ):
            raise PaperRuntimeBrokerTruthIntegrityError(
                "portfolio counts must be non-negative integers"
            )
        for label, value in (
            ("account_attested_at", self.account_attested_at),
            ("crypto_status_observed_at", self.crypto_status_observed_at),
            ("portfolio_attested_at", self.portfolio_attested_at),
            ("observed_at", self.observed_at),
            ("broker_truth_valid_until", self.broker_truth_valid_until),
        ):
            _require_aware(value, label)
        if self.broker_truth_valid_until < self.observed_at:
            raise PaperRuntimeBrokerTruthIntegrityError(
                "broker truth proof is already stale at observation time"
            )
        if self.source_host != ALPACA_PAPER_TRADING_HOST:
            raise PaperRuntimeBrokerTruthIntegrityError(
                "broker truth source host is not exact PAPER host"
            )
        if self.account_path != ALPACA_PAPER_ACCOUNT_PATH:
            raise PaperRuntimeBrokerTruthIntegrityError(
                "broker truth account path is not canonical"
            )
        if self.positions_path != POSITIONS_PATH:
            raise PaperRuntimeBrokerTruthIntegrityError(
                "broker truth positions path is not canonical"
            )
        if self.orders_path != f"{ORDERS_PATH}?{ORDERS_QUERY}":
            raise PaperRuntimeBrokerTruthIntegrityError(
                "broker truth open-orders path is not canonical"
            )
        if (
            self.account_environment_verified is not True
            or self.crypto_entitlement_verified is not True
            or self.portfolio_truth_verified is not True
            or self.read_only_broker_truth is not True
        ):
            raise PaperRuntimeBrokerTruthIntegrityError(
                "W86 broker truth verification flags are incomplete"
            )
        expected_clean = self.position_count == 0 and self.open_order_count == 0
        if self.clean_for_candidate_start is not expected_clean:
            raise PaperRuntimeBrokerTruthIntegrityError(
                "clean candidate-start flag does not match broker portfolio truth"
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
            raise PaperRuntimeBrokerTruthIntegrityError(
                "W86 broker truth proof hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _proof_payload(self, include_hash=True)


def read_and_bind_paper_runtime_broker_truth(
    *,
    proof_id: str,
    candidate_identity: PaperRuntimeCandidateIdentityProof,
    credentials: AlpacaPaperCredentials,
    expected_account_id: str,
    config: AlpacaPaperGatewayConfig,
    observed_at: datetime,
    policy: PaperRuntimeBrokerTruthPolicy | None = None,
    account_transport: AlpacaPaperReadTransport | None = None,
    crypto_status_transport: AlpacaPaperReadTransport | None = None,
    flat_account_transport: AlpacaPaperReadTransport | None = None,
) -> PaperRuntimeBrokerTruthProof:
    """Collect exact PAPER account/crypto/portfolio truth using existing GET-only readers."""

    _validate_candidate(candidate_identity)
    if not isinstance(credentials, AlpacaPaperCredentials):
        raise TypeError("credentials must be AlpacaPaperCredentials")
    if not isinstance(config, AlpacaPaperGatewayConfig):
        raise TypeError("config must be AlpacaPaperGatewayConfig")
    if config.enabled is not True:
        raise PaperRuntimeBrokerTruthIntegrityError(
            "W86 broker reads require explicitly enabled PAPER read config"
        )
    if config.base_url != f"https://{ALPACA_PAPER_TRADING_HOST}":
        raise PaperRuntimeBrokerTruthIntegrityError(
            "W86 broker reads require exact Alpaca PAPER base URL"
        )
    _require_aware(observed_at, "observed_at")
    effective_policy = policy or PaperRuntimeBrokerTruthPolicy()

    account = AlpacaPaperAccountGateway(
        config=config,
        transport=account_transport,
    ).attest_account(
        credentials=credentials,
        expected_account_id=expected_account_id,
        now=observed_at,
    )
    crypto_status = attest_active_crypto_account(
        credentials=credentials,
        expected_account_id=expected_account_id,
        now=observed_at,
        config=config,
        transport=crypto_status_transport,
    )
    flat_account = AlpacaPaperFlatAccountGateway(
        config=config,
        transport=flat_account_transport,
    ).attest_flatness(
        credentials=credentials,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=account.credential_reference,
        now=observed_at,
    )
    return bind_paper_runtime_broker_truth(
        proof_id=proof_id,
        candidate_identity=candidate_identity,
        account=account,
        crypto_status=crypto_status,
        flat_account=flat_account,
        observed_at=observed_at,
        policy=effective_policy,
    )


def bind_paper_runtime_broker_truth(
    *,
    proof_id: str,
    candidate_identity: PaperRuntimeCandidateIdentityProof,
    account: AlpacaPaperAccountAttestation,
    crypto_status: AlpacaPaperCryptoAccountStatusAttestation,
    flat_account: PaperFlatAccountAttestation,
    observed_at: datetime,
    policy: PaperRuntimeBrokerTruthPolicy | None = None,
) -> PaperRuntimeBrokerTruthProof:
    """Bind read-only broker receipts to the exact ACTIVE W86 candidate identity."""

    _require_id(proof_id, "proof_id")
    _validate_candidate(candidate_identity)
    if not isinstance(account, AlpacaPaperAccountAttestation):
        raise TypeError("account must be AlpacaPaperAccountAttestation")
    if not isinstance(crypto_status, AlpacaPaperCryptoAccountStatusAttestation):
        raise TypeError(
            "crypto_status must be AlpacaPaperCryptoAccountStatusAttestation"
        )
    if not isinstance(flat_account, PaperFlatAccountAttestation):
        raise TypeError("flat_account must be PaperFlatAccountAttestation")
    _require_aware(observed_at, "observed_at")
    effective_policy = policy or PaperRuntimeBrokerTruthPolicy()

    _validate_account(account, candidate_identity)
    _validate_crypto_status(crypto_status, account)
    _validate_flat_account(flat_account, account)

    _require_fresh(
        account.attested_at,
        observed_at,
        effective_policy.max_account_age_seconds,
        "PAPER account",
    )
    _require_fresh(
        crypto_status.observed_at,
        observed_at,
        effective_policy.max_crypto_status_age_seconds,
        "PAPER crypto status",
    )
    _require_fresh(
        flat_account.attested_at,
        observed_at,
        effective_policy.max_portfolio_age_seconds,
        "PAPER portfolio",
    )
    evidence_times = (
        account.attested_at.astimezone(timezone.utc),
        crypto_status.observed_at.astimezone(timezone.utc),
        flat_account.attested_at.astimezone(timezone.utc),
    )
    if max(evidence_times) - min(evidence_times) > timedelta(
        seconds=effective_policy.max_cross_read_skew_seconds
    ):
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER broker reads exceed cross-read skew budget"
        )

    valid_until = min(
        account.attested_at
        + timedelta(seconds=effective_policy.max_account_age_seconds),
        crypto_status.observed_at
        + timedelta(seconds=effective_policy.max_crypto_status_age_seconds),
        flat_account.attested_at
        + timedelta(seconds=effective_policy.max_portfolio_age_seconds),
    ).astimezone(timezone.utc)
    values = {
        "proof_id": proof_id,
        "contract_version": PAPER_RUNTIME_BROKER_TRUTH_VERSION,
        "candidate_identity_hash": candidate_identity.proof_hash,
        "policy_hash": effective_policy.fingerprint,
        "authority_key": candidate_identity.authority_key,
        "admission_hash": candidate_identity.admission_hash,
        "product_id": candidate_identity.product_id,
        "asset_class": candidate_identity.asset_class,
        "venue": candidate_identity.venue,
        "symbol": candidate_identity.symbol,
        "quote_currency": candidate_identity.quote_currency,
        "account_id": account.account_id,
        "account_reference": account.account_reference,
        "credential_reference": account.credential_reference,
        "account_attestation_fingerprint": account.fingerprint,
        "account_request_id": account.request_id,
        "account_attested_at": account.attested_at.astimezone(timezone.utc),
        "crypto_status_fingerprint": crypto_status.fingerprint,
        "crypto_status_request_id": crypto_status.request_id,
        "crypto_status_response_sha256": crypto_status.response_sha256,
        "crypto_status_observed_at": crypto_status.observed_at.astimezone(timezone.utc),
        "flat_account_fingerprint": flat_account.fingerprint,
        "position_count": flat_account.position_count,
        "open_order_count": flat_account.open_order_count,
        "positions_response_hash": flat_account.positions_response_hash,
        "orders_response_hash": flat_account.orders_response_hash,
        "positions_request_id": flat_account.positions_request_id,
        "orders_request_id": flat_account.orders_request_id,
        "portfolio_attested_at": flat_account.attested_at.astimezone(timezone.utc),
        "source_host": ALPACA_PAPER_TRADING_HOST,
        "account_path": ALPACA_PAPER_ACCOUNT_PATH,
        "positions_path": POSITIONS_PATH,
        "orders_path": f"{ORDERS_PATH}?{ORDERS_QUERY}",
        "observed_at": observed_at.astimezone(timezone.utc),
        "broker_truth_valid_until": valid_until,
        "account_environment_verified": True,
        "crypto_entitlement_verified": True,
        "portfolio_truth_verified": True,
        "clean_for_candidate_start": flat_account.clean_for_first_canary,
        "read_only_broker_truth": True,
        "network_write_performed": False,
        "paper_runtime_ready": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PaperRuntimeBrokerTruthProof(
        **values,
        proof_hash=_hash(_payload_from_values(values)),
    )


def _validate_candidate(value: PaperRuntimeCandidateIdentityProof) -> None:
    if not isinstance(value, PaperRuntimeCandidateIdentityProof):
        raise TypeError(
            "candidate_identity must be PaperRuntimeCandidateIdentityProof"
        )
    expected = candidate_module._hash(
        candidate_module._payload(value, include_hash=False)
    )
    if value.proof_hash != expected:
        raise PaperRuntimeBrokerTruthIntegrityError(
            "W86 candidate identity proof hash mismatch"
        )
    if (
        value.asset_class != "crypto"
        or value.venue != ALPACA_PAPER_CRYPTO_MODEL_VENUE
        or value.quote_currency != "USD"
    ):
        raise PaperRuntimeBrokerTruthIntegrityError(
            "candidate is not the supported Alpaca PAPER crypto/USD product"
        )
    if (
        value.product_identity_verified is not True
        or value.strategy_runtime_identity_verified is not True
    ):
        raise PaperRuntimeBrokerTruthIntegrityError(
            "candidate identity is not fully verified"
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


def _validate_account(
    value: AlpacaPaperAccountAttestation,
    candidate: PaperRuntimeCandidateIdentityProof,
) -> None:
    if not _ACCOUNT_ID_RE.fullmatch(value.account_id):
        raise PaperRuntimeBrokerTruthIntegrityError("PAPER account_id is invalid")
    _require_hash(value.account_reference, "account_reference")
    _require_hash(value.credential_reference, "credential_reference")
    if value.status != "ACTIVE":
        raise PaperRuntimeBrokerTruthIntegrityError("PAPER account must be ACTIVE")
    if value.currency != "USD" or value.currency != candidate.quote_currency:
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER account currency differs from candidate quote currency"
        )
    if (
        not isinstance(value.buying_power, Decimal)
        or not value.buying_power.is_finite()
        or value.buying_power < 0
        or not isinstance(value.portfolio_value, Decimal)
        or not value.portfolio_value.is_finite()
        or value.portfolio_value < 0
    ):
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER account monetary fields are invalid"
        )
    if not isinstance(value.shorting_enabled, bool):
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER account shorting flag is invalid"
        )
    _require_aware(value.attested_at, "account.attested_at")
    if not _REQUEST_ID_RE.fullmatch(value.request_id):
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER account request id is invalid"
        )
    if (
        value.source_host != ALPACA_PAPER_TRADING_HOST
        or value.source_path != ALPACA_PAPER_ACCOUNT_PATH
    ):
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER account evidence source is not canonical"
        )
    _require_hash(value.fingerprint, "account fingerprint")


def _validate_crypto_status(
    value: AlpacaPaperCryptoAccountStatusAttestation,
    account: AlpacaPaperAccountAttestation,
) -> None:
    if value.account_id != account.account_id:
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER crypto status account differs from account attestation"
        )
    if value.crypto_status != "ACTIVE" or value.crypto_ready is not True:
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER crypto entitlement is not ACTIVE"
        )
    _require_aware(value.observed_at, "crypto_status.observed_at")
    if not _REQUEST_ID_RE.fullmatch(value.request_id):
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER crypto status request id is invalid"
        )
    _require_hash(value.response_sha256, "crypto status response hash")
    _require_hash(value.fingerprint, "crypto status fingerprint")


def _validate_flat_account(
    value: PaperFlatAccountAttestation,
    account: AlpacaPaperAccountAttestation,
) -> None:
    if value.account_attestation_fingerprint != account.fingerprint:
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER portfolio truth is bound to a different account attestation"
        )
    if value.credential_reference != account.credential_reference:
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER portfolio truth is bound to a different credential reference"
        )
    if (
        value.source_host != ALPACA_PAPER_TRADING_HOST
        or value.positions_path != POSITIONS_PATH
        or value.orders_path != f"{ORDERS_PATH}?{ORDERS_QUERY}"
    ):
        raise PaperRuntimeBrokerTruthIntegrityError(
            "PAPER portfolio truth source path is not canonical"
        )
    _require_aware(value.attested_at, "flat_account.attested_at")
    _require_hash(value.positions_response_hash, "positions response hash")
    _require_hash(value.orders_response_hash, "orders response hash")
    _require_hash(value.fingerprint, "flat account fingerprint")


def _require_fresh(
    value: datetime,
    observed_at: datetime,
    max_age_seconds: int,
    label: str,
) -> None:
    value_utc = value.astimezone(timezone.utc)
    observed_utc = observed_at.astimezone(timezone.utc)
    if value_utc > observed_utc:
        raise PaperRuntimeBrokerTruthIntegrityError(
            f"{label} timestamp is in the process future"
        )
    if observed_utc - value_utc > timedelta(seconds=max_age_seconds):
        raise PaperRuntimeBrokerTruthIntegrityError(f"{label} evidence is stale")


def _proof_payload(
    value: PaperRuntimeBrokerTruthProof, *, include_hash: bool
) -> dict[str, object]:
    names = tuple(
        name
        for name in PaperRuntimeBrokerTruthProof.__dataclass_fields__
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
        raise PaperRuntimeBrokerTruthIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeBrokerTruthIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PaperRuntimeBrokerTruthIntegrityError(
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
        raise PaperRuntimeBrokerTruthIntegrityError(
            "W86 broker truth may not grant readiness, execution, capital or LIVE authority"
        )


__all__ = [
    "PAPER_RUNTIME_BROKER_TRUTH_VERSION",
    "PaperRuntimeBrokerTruthError",
    "PaperRuntimeBrokerTruthIntegrityError",
    "PaperRuntimeBrokerTruthPolicy",
    "PaperRuntimeBrokerTruthProof",
    "bind_paper_runtime_broker_truth",
    "read_and_bind_paper_runtime_broker_truth",
]
