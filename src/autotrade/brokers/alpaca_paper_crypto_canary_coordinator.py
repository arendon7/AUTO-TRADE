from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    OrderType,
    RiskDecision,
    RiskDecisionStatus,
    Side,
    intent_fingerprint,
    market_fingerprint,
    risk_decision_fingerprint,
)
from autotrade.oms import OrderManagementSystem
from autotrade.product_profile import (
    AssetClass,
    BrokerOrderType,
    MarketHoursModel,
    ProductCapabilities,
    ProtectionModel,
    TimeInForce,
)

from .alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from .alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleBinding,
    CryptoLifecycleState,
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from .alpaca_paper_crypto_market_data import AlpacaPaperCryptoMarketAttestation
from .alpaca_paper_crypto_order import (
    AlpacaPaperCryptoOrderRequest,
    CryptoOrderRole,
    build_crypto_entry_order,
    deterministic_crypto_client_order_id,
)
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
FIRST_CANARY_MAX_NOTIONAL = Decimal("25")
FIRST_CANARY_MAX_ACCOUNT_FRACTION = Decimal("0.001")
PREPARATION_EVIDENCE_TTL = timedelta(seconds=30)
_REQUIRED_TRACKS = ("R0", "R1", "R2", "R3", "R4", "R5")


class CryptoCanaryCoordinatorError(RuntimeError):
    pass


class CryptoCanaryPreparationBlocked(CryptoCanaryCoordinatorError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedCryptoPaperCanaryPackage:
    lifecycle_id: str
    order_id: str
    client_order_id: str
    symbol: str
    intent_fingerprint: str
    risk_decision_id: str
    risk_decision_fingerprint: str
    risk_decision_safety_state_version: int
    risk_decision_valid_until: datetime
    market_fingerprint: str
    market_attestation_fingerprint: str
    account_attestation_fingerprint: str
    asset_attestation_fingerprint: str
    product_profile_fingerprint: str
    crypto_order_fingerprint: str
    crypto_order_payload_hash: str
    lifecycle_binding_hash: str
    lifecycle_control_hash: str
    lifecycle_event_head_hash: str
    quantity: Decimal
    limit_price: Decimal
    notional: Decimal
    effective_notional_cap: Decimal
    prepared_at: datetime
    execution_deadline: datetime
    order_status: str
    broker_order_type: str
    time_in_force: str
    opening_short: bool
    uses_margin: bool
    network_write_authorized: bool
    next_action: str
    package_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("lifecycle_id", self.lifecycle_id),
            ("order_id", self.order_id),
            ("client_order_id", self.client_order_id),
            ("risk_decision_id", self.risk_decision_id),
        ):
            _require_id(value, label)
        if self.symbol.count("/") != 1 or self.symbol != self.symbol.upper():
            raise ValueError("prepared crypto symbol must be canonical BASE/QUOTE")
        for label, value in (
            ("intent_fingerprint", self.intent_fingerprint),
            ("risk_decision_fingerprint", self.risk_decision_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("market_attestation_fingerprint", self.market_attestation_fingerprint),
            ("account_attestation_fingerprint", self.account_attestation_fingerprint),
            ("asset_attestation_fingerprint", self.asset_attestation_fingerprint),
            ("product_profile_fingerprint", self.product_profile_fingerprint),
            ("crypto_order_fingerprint", self.crypto_order_fingerprint),
            ("crypto_order_payload_hash", self.crypto_order_payload_hash),
            ("lifecycle_binding_hash", self.lifecycle_binding_hash),
            ("lifecycle_control_hash", self.lifecycle_control_hash),
            ("lifecycle_event_head_hash", self.lifecycle_event_head_hash),
            ("package_hash", self.package_hash),
        ):
            _require_hash(value, label)
        if (
            isinstance(self.risk_decision_safety_state_version, bool)
            or not isinstance(self.risk_decision_safety_state_version, int)
            or self.risk_decision_safety_state_version < 0
        ):
            raise ValueError("risk_decision_safety_state_version must be non-negative integer")
        for label, value in (
            ("risk_decision_valid_until", self.risk_decision_valid_until),
            ("prepared_at", self.prepared_at),
            ("execution_deadline", self.execution_deadline),
        ):
            _require_aware(value, label)
        for label, value in (
            ("quantity", self.quantity),
            ("limit_price", self.limit_price),
            ("notional", self.notional),
            ("effective_notional_cap", self.effective_notional_cap),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{label} must be finite and positive")
        if self.notional != self.quantity * self.limit_price:
            raise ValueError("prepared crypto notional must equal quantity * limit_price")
        if self.notional > self.effective_notional_cap:
            raise ValueError("prepared crypto notional exceeds effective first-canary cap")
        if self.prepared_at >= self.execution_deadline:
            raise ValueError("prepared crypto package is already expired")
        if self.execution_deadline > self.risk_decision_valid_until:
            raise ValueError("execution deadline may not outlive RiskDecision")
        if self.order_status != OrderStatus.VALIDATED.value:
            raise ValueError("crypto preparation must leave OMS VALIDATED")
        if self.broker_order_type != BrokerOrderType.LIMIT.value:
            raise ValueError("first crypto canary must use LIMIT entry")
        if self.time_in_force != TimeInForce.IOC.value:
            raise ValueError("first crypto canary must use IOC entry")
        if self.opening_short is not False or self.uses_margin is not False:
            raise ValueError("first crypto canary is long-only and non-margin")
        if self.network_write_authorized is not False:
            raise ValueError("prepared crypto package cannot authorize broker write")
        if self.next_action != "OPERATOR_DECISION_REQUIRED":
            raise ValueError("prepared crypto package must require explicit operator decision")
        expected_hash = _hash_json(_package_payload(self, include_hash=False))
        if self.package_hash != expected_hash:
            raise ValueError("prepared crypto package hash mismatch")

    def canonical_payload(self) -> dict[str, object]:
        return _package_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class CryptoCanaryPreparationResult:
    package: PreparedCryptoPaperCanaryPackage
    order: OrderRecord
    broker_order: AlpacaPaperCryptoOrderRequest
    lifecycle_binding: CryptoLifecycleBinding
    lifecycle_state: CryptoLifecycleState


class CryptoPaperCanaryCoordinator:
    """Offline-only first crypto canary preparation.

    This component can validate Safety/OMS state, bind exact broker-observed
    product evidence, build a LIMIT IOC entry request and persist the crypto
    lifecycle in ENTRY_PREPARED. It intentionally has no credentials, network,
    writer or operator-decision issuance API.
    """

    def __init__(self, *, oms: OrderManagementSystem) -> None:
        if not isinstance(oms, OrderManagementSystem):
            raise TypeError("crypto coordinator requires authoritative OrderManagementSystem")
        self._oms = oms

    def prepare_entry(
        self,
        *,
        intent: OrderIntent,
        decision: RiskDecision,
        market_attestation: AlpacaPaperCryptoMarketAttestation,
        account_attestation: AlpacaPaperAccountAttestation,
        asset_attestation: AlpacaPaperCryptoAssetAttestation,
        product_profile: ProductCapabilities,
        lifecycle: SQLiteCryptoPaperLifecycle,
        now: datetime,
        certified_tracks: tuple[str, ...],
        reconciliation_clean: bool,
        unresolved_unknown_orders: int,
        relevant_open_orders: int,
        confirmed_pair_position_quantity: Decimal,
    ) -> CryptoCanaryPreparationResult:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        if certified_tracks != _REQUIRED_TRACKS:
            raise CryptoCanaryPreparationBlocked("certified track set must be exactly R0-R5")
        for label, value in (
            ("unresolved_unknown_orders", unresolved_unknown_orders),
            ("relevant_open_orders", relevant_open_orders),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CryptoCanaryPreparationBlocked(f"{label} must be integer >= 0")
        if reconciliation_clean is not True:
            raise CryptoCanaryPreparationBlocked("crypto canary requires clean reconciliation")
        if unresolved_unknown_orders != 0:
            raise CryptoCanaryPreparationBlocked("crypto canary forbids unresolved UNKNOWN orders")
        if relevant_open_orders != 0:
            raise CryptoCanaryPreparationBlocked("crypto canary requires zero relevant open orders")
        if (
            not isinstance(confirmed_pair_position_quantity, Decimal)
            or not confirmed_pair_position_quantity.is_finite()
            or confirmed_pair_position_quantity != 0
        ):
            raise CryptoCanaryPreparationBlocked("first crypto canary requires zero confirmed pair position")

        self._validate_product_evidence(
            intent=intent,
            decision=decision,
            market_attestation=market_attestation,
            account_attestation=account_attestation,
            asset_attestation=asset_attestation,
            product_profile=product_profile,
            now=instant,
        )
        order = self._oms.validate_for_external_submission(
            intent=intent,
            decision=decision,
            market=market_attestation.market,
            now=instant,
        )
        if order.status is not OrderStatus.VALIDATED:
            raise CryptoCanaryPreparationBlocked("crypto coordinator requires OMS VALIDATED state")

        lifecycle_id = _lifecycle_id(
            order_id=order.order_id,
            account_fingerprint=account_attestation.fingerprint,
            asset_fingerprint=asset_attestation.fingerprint,
            product_profile_fingerprint=product_profile.fingerprint,
        )
        client_order_id = deterministic_crypto_client_order_id(
            lifecycle_id=lifecycle_id,
            role=CryptoOrderRole.ENTRY,
        )
        broker_order = build_crypto_entry_order(
            symbol=intent.symbol,
            quantity=intent.quantity,
            order_type=BrokerOrderType.LIMIT,
            time_in_force=TimeInForce.IOC,
            client_order_id=client_order_id,
            product_profile=product_profile,
            asset_attestation=asset_attestation,
            limit_price=intent.limit_price,
        )
        if broker_order.quantity != intent.quantity:
            raise CryptoCanaryPreparationBlocked(
                "first crypto canary quantity must already satisfy exact broker increment"
            )
        if broker_order.limit_price != intent.limit_price:
            raise CryptoCanaryPreparationBlocked(
                "first crypto canary limit price must already satisfy exact broker increment"
            )
        assert broker_order.limit_price is not None
        notional = broker_order.quantity * broker_order.limit_price
        effective_cap = min(
            FIRST_CANARY_MAX_NOTIONAL,
            account_attestation.portfolio_value * FIRST_CANARY_MAX_ACCOUNT_FRACTION,
            account_attestation.buying_power,
        )
        if effective_cap <= 0 or notional > effective_cap:
            raise CryptoCanaryPreparationBlocked("crypto first-canary notional exceeds conservative cap")
        if decision.approved_notional is None or notional > decision.approved_notional:
            raise CryptoCanaryPreparationBlocked("crypto broker request exceeds Safety-approved notional")

        binding = CryptoLifecycleBinding(
            lifecycle_id=lifecycle_id,
            account_attestation_fingerprint=account_attestation.fingerprint,
            asset_attestation_fingerprint=asset_attestation.fingerprint,
            product_profile_fingerprint=product_profile.fingerprint,
            symbol=intent.symbol,
            entry_order_fingerprint=broker_order.fingerprint,
            entry_client_order_id=broker_order.client_order_id,
            entry_quantity=broker_order.quantity,
            created_at=order.created_at,
        )
        lifecycle_state = lifecycle.prepare(binding)
        if lifecycle_state.status is not CryptoLifecycleStatus.ENTRY_PREPARED:
            raise CryptoCanaryPreparationBlocked("crypto lifecycle is not ENTRY_PREPARED")
        if lifecycle_state.entry_attempt_count != 0:
            raise CryptoCanaryPreparationBlocked("crypto lifecycle already has an entry attempt")

        execution_deadline = min(
            decision.valid_until.astimezone(timezone.utc),
            account_attestation.attested_at.astimezone(timezone.utc) + PREPARATION_EVIDENCE_TTL,
            asset_attestation.observed_at.astimezone(timezone.utc) + PREPARATION_EVIDENCE_TTL,
            market_attestation.received_at.astimezone(timezone.utc) + PREPARATION_EVIDENCE_TTL,
        )
        if instant >= execution_deadline:
            raise CryptoCanaryPreparationBlocked("crypto preparation evidence is already stale")

        package_values = {
            "lifecycle_id": lifecycle_id,
            "order_id": order.order_id,
            "client_order_id": broker_order.client_order_id,
            "symbol": intent.symbol,
            "intent_fingerprint": intent_fingerprint(intent),
            "risk_decision_id": decision.decision_id,
            "risk_decision_fingerprint": risk_decision_fingerprint(decision),
            "risk_decision_safety_state_version": decision.safety_state_version,
            "risk_decision_valid_until": decision.valid_until,
            "market_fingerprint": market_fingerprint(market_attestation.market),
            "market_attestation_fingerprint": market_attestation.fingerprint,
            "account_attestation_fingerprint": account_attestation.fingerprint,
            "asset_attestation_fingerprint": asset_attestation.fingerprint,
            "product_profile_fingerprint": product_profile.fingerprint,
            "crypto_order_fingerprint": broker_order.fingerprint,
            "crypto_order_payload_hash": broker_order.payload_hash,
            "lifecycle_binding_hash": binding.fingerprint,
            "lifecycle_control_hash": lifecycle_state.control_hash,
            "lifecycle_event_head_hash": lifecycle_state.event_head_hash,
            "quantity": broker_order.quantity,
            "limit_price": broker_order.limit_price,
            "notional": notional,
            "effective_notional_cap": effective_cap,
            "prepared_at": instant,
            "execution_deadline": execution_deadline,
            "order_status": order.status.value,
            "broker_order_type": broker_order.order_type.value,
            "time_in_force": broker_order.time_in_force.value,
            "opening_short": False,
            "uses_margin": False,
            "network_write_authorized": False,
            "next_action": "OPERATOR_DECISION_REQUIRED",
        }
        package = PreparedCryptoPaperCanaryPackage(
            **package_values,
            package_hash=_hash_json(_package_payload_from_values(package_values)),
        )
        return CryptoCanaryPreparationResult(
            package=package,
            order=order,
            broker_order=broker_order,
            lifecycle_binding=binding,
            lifecycle_state=lifecycle_state,
        )

    @staticmethod
    def _validate_product_evidence(
        *,
        intent: OrderIntent,
        decision: RiskDecision,
        market_attestation: AlpacaPaperCryptoMarketAttestation,
        account_attestation: AlpacaPaperAccountAttestation,
        asset_attestation: AlpacaPaperCryptoAssetAttestation,
        product_profile: ProductCapabilities,
        now: datetime,
    ) -> None:
        if intent.side is not Side.BUY or intent.order_type is not OrderType.LIMIT:
            raise CryptoCanaryPreparationBlocked("first crypto canary requires BUY LIMIT intent")
        if intent.limit_price is None or intent.limit_price <= 0:
            raise CryptoCanaryPreparationBlocked("first crypto canary requires positive limit price")
        if decision.status is not RiskDecisionStatus.APPROVED:
            raise CryptoCanaryPreparationBlocked("crypto canary requires APPROVED RiskDecision")
        if decision.intent_id != intent.intent_id or decision.intent_fingerprint != intent_fingerprint(intent):
            raise CryptoCanaryPreparationBlocked("RiskDecision is not bound to exact crypto intent")
        if decision.market_fingerprint != market_fingerprint(market_attestation.market):
            raise CryptoCanaryPreparationBlocked("RiskDecision is not bound to exact crypto market snapshot")
        if now >= decision.valid_until.astimezone(timezone.utc):
            raise CryptoCanaryPreparationBlocked("RiskDecision is expired")
        if account_attestation.status != "ACTIVE" or account_attestation.currency != "USD":
            raise CryptoCanaryPreparationBlocked("crypto canary requires active USD PAPER account")
        if product_profile.asset_class is not AssetClass.CRYPTO:
            raise CryptoCanaryPreparationBlocked("crypto canary requires CRYPTO ProductCapabilities")
        if product_profile.market_hours_model is not MarketHoursModel.CONTINUOUS_24_7:
            raise CryptoCanaryPreparationBlocked("crypto canary requires CONTINUOUS_24_7 profile")
        if product_profile.protection_model is not ProtectionModel.CRYPTO_STOP_LIMIT:
            raise CryptoCanaryPreparationBlocked("crypto canary requires CRYPTO_STOP_LIMIT protection model")
        if product_profile.marginable or product_profile.shortable:
            raise CryptoCanaryPreparationBlocked("crypto first canary forbids margin/short capability")
        product_profile.require_order(order_type=BrokerOrderType.LIMIT, time_in_force=TimeInForce.IOC)
        product_profile.require_margin(uses_margin=False)
        product_profile.require_opening_short(opening_short=False)
        if product_profile.source_fingerprint != asset_attestation.fingerprint:
            raise CryptoCanaryPreparationBlocked("ProductCapabilities is not bound to exact asset evidence")
        if asset_attestation.symbol != intent.symbol or market_attestation.market.symbol != intent.symbol:
            raise CryptoCanaryPreparationBlocked("crypto intent/asset/market symbol identity mismatch")
        if asset_attestation.account_attestation_fingerprint != account_attestation.fingerprint:
            raise CryptoCanaryPreparationBlocked("crypto asset evidence is not bound to exact account attestation")
        if asset_attestation.credential_reference != account_attestation.credential_reference:
            raise CryptoCanaryPreparationBlocked("crypto account/asset credential references differ")
        if market_attestation.received_at > now + timedelta(seconds=3):
            raise CryptoCanaryPreparationBlocked("crypto market receipt timestamp is in the future")
        for label, observed in (
            ("account", account_attestation.attested_at),
            ("asset", asset_attestation.observed_at),
            ("product profile", product_profile.observed_at),
            ("market", market_attestation.received_at),
        ):
            age = now - observed.astimezone(timezone.utc)
            if age < timedelta(seconds=-3) or age >= PREPARATION_EVIDENCE_TTL:
                raise CryptoCanaryPreparationBlocked(f"crypto {label} evidence is stale or future-dated")


def _lifecycle_id(
    *,
    order_id: str,
    account_fingerprint: str,
    asset_fingerprint: str,
    product_profile_fingerprint: str,
) -> str:
    raw = ":".join((order_id, account_fingerprint, asset_fingerprint, product_profile_fingerprint))
    return "r6c-entry-" + sha256(raw.encode("utf-8")).hexdigest()[:40]


def _package_payload(value: PreparedCryptoPaperCanaryPackage, *, include_hash: bool) -> dict[str, object]:
    payload = _package_payload_from_values(
        {
            "lifecycle_id": value.lifecycle_id,
            "order_id": value.order_id,
            "client_order_id": value.client_order_id,
            "symbol": value.symbol,
            "intent_fingerprint": value.intent_fingerprint,
            "risk_decision_id": value.risk_decision_id,
            "risk_decision_fingerprint": value.risk_decision_fingerprint,
            "risk_decision_safety_state_version": value.risk_decision_safety_state_version,
            "risk_decision_valid_until": value.risk_decision_valid_until,
            "market_fingerprint": value.market_fingerprint,
            "market_attestation_fingerprint": value.market_attestation_fingerprint,
            "account_attestation_fingerprint": value.account_attestation_fingerprint,
            "asset_attestation_fingerprint": value.asset_attestation_fingerprint,
            "product_profile_fingerprint": value.product_profile_fingerprint,
            "crypto_order_fingerprint": value.crypto_order_fingerprint,
            "crypto_order_payload_hash": value.crypto_order_payload_hash,
            "lifecycle_binding_hash": value.lifecycle_binding_hash,
            "lifecycle_control_hash": value.lifecycle_control_hash,
            "lifecycle_event_head_hash": value.lifecycle_event_head_hash,
            "quantity": value.quantity,
            "limit_price": value.limit_price,
            "notional": value.notional,
            "effective_notional_cap": value.effective_notional_cap,
            "prepared_at": value.prepared_at,
            "execution_deadline": value.execution_deadline,
            "order_status": value.order_status,
            "broker_order_type": value.broker_order_type,
            "time_in_force": value.time_in_force,
            "opening_short": value.opening_short,
            "uses_margin": value.uses_margin,
            "network_write_authorized": value.network_write_authorized,
            "next_action": value.next_action,
        }
    )
    if include_hash:
        payload["package_hash"] = value.package_hash
    return payload


def _package_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            payload[key] = value.astimezone(timezone.utc).isoformat()
        elif isinstance(value, Decimal):
            payload[key] = format(value, "f")
        else:
            payload[key] = value
    return payload


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _hash_json(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CryptoCanaryCoordinatorError",
    "CryptoCanaryPreparationBlocked",
    "CryptoCanaryPreparationResult",
    "CryptoPaperCanaryCoordinator",
    "FIRST_CANARY_MAX_ACCOUNT_FRACTION",
    "FIRST_CANARY_MAX_NOTIONAL",
    "PREPARATION_EVIDENCE_TTL",
    "PreparedCryptoPaperCanaryPackage",
]
