from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

from autotrade.domain import (
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
    ProductCapabilities,
    ProtectionModel,
    TimeInForce,
)

from .alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from .alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleState,
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from .alpaca_paper_crypto_market_data import AlpacaPaperCryptoMarketAttestation
from .alpaca_paper_crypto_order import (
    AlpacaPaperCryptoOrderRequest,
    CryptoOrderRole,
    build_crypto_long_protection_order,
    deterministic_crypto_client_order_id,
)
from .alpaca_paper_crypto_reconciliation import CryptoBrokerReconciliation
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
PROTECTION_PREPARATION_EVIDENCE_TTL = timedelta(seconds=30)


class CryptoProtectionCoordinatorError(RuntimeError):
    pass


class CryptoProtectionPreparationBlocked(CryptoProtectionCoordinatorError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedCryptoProtectionPackage:
    lifecycle_id: str
    order_id: str
    client_order_id: str
    symbol: str
    entry_client_order_id: str
    entry_broker_order_id: str
    entry_reconciliation_fingerprint: str
    confirmed_entry_filled_quantity: Decimal
    confirmed_net_long_quantity: Decimal
    intent_fingerprint: str
    risk_decision_id: str
    risk_decision_fingerprint: str
    risk_decision_safety_state_version: int
    risk_decision_valid_until: datetime
    market_fingerprint: str
    market_attestation_fingerprint: str
    account_attestation_fingerprint: str
    account_reference: str
    credential_reference: str
    asset_attestation_fingerprint: str
    product_profile_fingerprint: str
    crypto_order_fingerprint: str
    crypto_order_payload_hash: str
    lifecycle_binding_hash: str
    lifecycle_control_hash: str
    lifecycle_event_head_hash: str
    quantity: Decimal
    stop_price: Decimal
    limit_price: Decimal
    prepared_at: datetime
    execution_deadline: datetime
    order_status: str
    broker_order_type: str
    time_in_force: str
    risk_reducing: bool
    network_write_authorized: bool
    next_action: str
    package_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("lifecycle_id", self.lifecycle_id),
            ("order_id", self.order_id),
            ("client_order_id", self.client_order_id),
            ("entry_client_order_id", self.entry_client_order_id),
            ("entry_broker_order_id", self.entry_broker_order_id),
            ("risk_decision_id", self.risk_decision_id),
        ):
            _require_id(value, label)
        if self.symbol.count("/") != 1 or self.symbol != self.symbol.upper():
            raise ValueError("prepared protection symbol must be canonical BASE/QUOTE")
        for label, value in (
            ("entry_reconciliation_fingerprint", self.entry_reconciliation_fingerprint),
            ("intent_fingerprint", self.intent_fingerprint),
            ("risk_decision_fingerprint", self.risk_decision_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("market_attestation_fingerprint", self.market_attestation_fingerprint),
            ("account_attestation_fingerprint", self.account_attestation_fingerprint),
            ("account_reference", self.account_reference),
            ("credential_reference", self.credential_reference),
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
            ("confirmed_entry_filled_quantity", self.confirmed_entry_filled_quantity),
            ("confirmed_net_long_quantity", self.confirmed_net_long_quantity),
            ("quantity", self.quantity),
            ("stop_price", self.stop_price),
            ("limit_price", self.limit_price),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{label} must be finite and positive")
        if self.confirmed_net_long_quantity > self.confirmed_entry_filled_quantity:
            raise ValueError("confirmed net long cannot exceed confirmed entry fill")
        if self.quantity != self.confirmed_net_long_quantity:
            raise ValueError("protective quantity must equal confirmed net long exactly")
        if self.limit_price > self.stop_price:
            raise ValueError("long stop-limit protection requires limit <= stop")
        if self.prepared_at >= self.execution_deadline:
            raise ValueError("prepared protection package is already expired")
        if self.execution_deadline > self.risk_decision_valid_until:
            raise ValueError("protection execution deadline may not outlive RiskDecision")
        if self.order_status != OrderStatus.VALIDATED.value:
            raise ValueError("protection preparation must leave OMS VALIDATED")
        if self.broker_order_type != BrokerOrderType.STOP_LIMIT.value:
            raise ValueError("crypto protection must use STOP_LIMIT")
        if self.time_in_force != TimeInForce.GTC.value:
            raise ValueError("crypto protection must use GTC")
        if self.risk_reducing is not True:
            raise ValueError("crypto protection must be Safety-classified risk reducing")
        if self.network_write_authorized is not False:
            raise ValueError("prepared protection package cannot authorize broker write")
        if self.next_action != "OPERATOR_DECISION_REQUIRED":
            raise ValueError("prepared protection package must require explicit operator decision")
        if self.package_hash != _hash_json(_package_payload(self, include_hash=False)):
            raise ValueError("prepared protection package hash mismatch")

    def canonical_payload(self) -> dict[str, object]:
        return _package_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class CryptoProtectionPreparationResult:
    package: PreparedCryptoProtectionPackage
    order: OrderRecord
    broker_order: AlpacaPaperCryptoOrderRequest
    lifecycle_state: CryptoLifecycleState


class CryptoPaperProtectionCoordinator:
    """Offline-only protection preparation after confirmed terminal entry fill.

    The coordinator consumes no credentials and performs no broker I/O. It
    requires reconciliation evidence for the exact entry client_order_id and
    exact account position, builds a SELL STOP_LIMIT for exactly the confirmed
    net long quantity, obtains a separate OMS VALIDATED protection order, and
    moves the lifecycle only to PROTECTION_PREPARED.
    """

    def __init__(self, *, oms: OrderManagementSystem) -> None:
        if not isinstance(oms, OrderManagementSystem):
            raise TypeError("crypto protection coordinator requires authoritative OrderManagementSystem")
        self._oms = oms

    def prepare_protection(
        self,
        *,
        lifecycle: SQLiteCryptoPaperLifecycle,
        lifecycle_id: str,
        entry_order: AlpacaPaperCryptoOrderRequest,
        entry_reconciliation: CryptoBrokerReconciliation,
        intent: OrderIntent,
        decision: RiskDecision,
        market_attestation: AlpacaPaperCryptoMarketAttestation,
        account_attestation: AlpacaPaperAccountAttestation,
        asset_attestation: AlpacaPaperCryptoAssetAttestation,
        product_profile: ProductCapabilities,
        stop_price: Decimal,
        limit_price: Decimal,
        now: datetime,
    ) -> CryptoProtectionPreparationResult:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        if not isinstance(lifecycle, SQLiteCryptoPaperLifecycle):
            raise CryptoProtectionPreparationBlocked("authoritative crypto lifecycle is required")
        if not isinstance(entry_order, AlpacaPaperCryptoOrderRequest) or entry_order.role is not CryptoOrderRole.ENTRY:
            raise CryptoProtectionPreparationBlocked("exact ENTRY broker request is required")
        if not isinstance(entry_reconciliation, CryptoBrokerReconciliation):
            raise CryptoProtectionPreparationBlocked("exact entry reconciliation evidence is required")

        snapshot = lifecycle.snapshot(lifecycle_id)
        binding = snapshot.binding
        state = snapshot.state
        self._validate_entry_reconciliation(
            binding=binding,
            state=state,
            entry_order=entry_order,
            reconciliation=entry_reconciliation,
            now=instant,
        )
        self._validate_product_evidence(
            binding=binding,
            intent=intent,
            decision=decision,
            market_attestation=market_attestation,
            account_attestation=account_attestation,
            asset_attestation=asset_attestation,
            product_profile=product_profile,
            now=instant,
        )
        if entry_reconciliation.position.credential_reference != account_attestation.credential_reference:
            raise CryptoProtectionPreparationBlocked(
                "entry reconciliation position credential differs from protection PAPER account"
            )

        confirmed_entry_fill = entry_reconciliation.order.filled_quantity
        confirmed_net_long = entry_reconciliation.position.quantity
        if intent.quantity != confirmed_net_long:
            raise CryptoProtectionPreparationBlocked("protection intent quantity must equal confirmed net long exactly")
        if intent.limit_price != limit_price:
            raise CryptoProtectionPreparationBlocked("protection intent limit price must equal requested broker limit")

        order = self._oms.validate_for_external_submission(
            intent=intent,
            decision=decision,
            market=market_attestation.market,
            now=instant,
        )
        if order.status is not OrderStatus.VALIDATED:
            raise CryptoProtectionPreparationBlocked("crypto protection requires OMS VALIDATED state")

        client_order_id = deterministic_crypto_client_order_id(
            lifecycle_id=lifecycle_id,
            role=CryptoOrderRole.PROTECTION,
        )
        broker_order = build_crypto_long_protection_order(
            symbol=binding.symbol,
            confirmed_entry_filled_quantity=confirmed_entry_fill,
            confirmed_net_long_quantity=confirmed_net_long,
            requested_protection_quantity=confirmed_net_long,
            stop_price=stop_price,
            limit_price=limit_price,
            client_order_id=client_order_id,
            product_profile=product_profile,
            asset_attestation=asset_attestation,
        )
        if broker_order.quantity != confirmed_net_long:
            raise CryptoProtectionPreparationBlocked("broker normalization may not reduce exact protection quantity")
        if broker_order.stop_price != stop_price or broker_order.limit_price != limit_price:
            raise CryptoProtectionPreparationBlocked("protective prices must already satisfy exact broker increments")

        lifecycle_state = lifecycle.prepare_protection(
            lifecycle_id,
            order=broker_order,
            at=instant,
        )
        if lifecycle_state.status is not CryptoLifecycleStatus.PROTECTION_PREPARED:
            raise CryptoProtectionPreparationBlocked("crypto lifecycle is not PROTECTION_PREPARED")
        if lifecycle_state.protection_attempt_count != 0:
            raise CryptoProtectionPreparationBlocked("crypto protection already has a submission attempt")
        if lifecycle_state.protection_quantity != confirmed_net_long:
            raise CryptoProtectionPreparationBlocked("lifecycle protection quantity differs from confirmed net long")

        execution_deadline = min(
            decision.valid_until.astimezone(timezone.utc),
            instant + PROTECTION_PREPARATION_EVIDENCE_TTL,
            market_attestation.received_at.astimezone(timezone.utc) + PROTECTION_PREPARATION_EVIDENCE_TTL,
        )
        if instant >= execution_deadline:
            raise CryptoProtectionPreparationBlocked("crypto protection evidence is already stale")

        package_values = {
            "lifecycle_id": lifecycle_id,
            "order_id": order.order_id,
            "client_order_id": broker_order.client_order_id,
            "symbol": binding.symbol,
            "entry_client_order_id": entry_order.client_order_id,
            "entry_broker_order_id": entry_reconciliation.order.broker_order_id,
            "entry_reconciliation_fingerprint": entry_reconciliation.fingerprint,
            "confirmed_entry_filled_quantity": confirmed_entry_fill,
            "confirmed_net_long_quantity": confirmed_net_long,
            "intent_fingerprint": intent_fingerprint(intent),
            "risk_decision_id": decision.decision_id,
            "risk_decision_fingerprint": risk_decision_fingerprint(decision),
            "risk_decision_safety_state_version": decision.safety_state_version,
            "risk_decision_valid_until": decision.valid_until,
            "market_fingerprint": market_fingerprint(market_attestation.market),
            "market_attestation_fingerprint": market_attestation.fingerprint,
            "account_attestation_fingerprint": account_attestation.fingerprint,
            "account_reference": account_attestation.account_reference,
            "credential_reference": account_attestation.credential_reference,
            "asset_attestation_fingerprint": asset_attestation.fingerprint,
            "product_profile_fingerprint": product_profile.fingerprint,
            "crypto_order_fingerprint": broker_order.fingerprint,
            "crypto_order_payload_hash": broker_order.payload_hash,
            "lifecycle_binding_hash": binding.fingerprint,
            "lifecycle_control_hash": lifecycle_state.control_hash,
            "lifecycle_event_head_hash": lifecycle_state.event_head_hash,
            "quantity": broker_order.quantity,
            "stop_price": broker_order.stop_price,
            "limit_price": broker_order.limit_price,
            "prepared_at": instant,
            "execution_deadline": execution_deadline,
            "order_status": order.status.value,
            "broker_order_type": broker_order.order_type.value,
            "time_in_force": broker_order.time_in_force.value,
            "risk_reducing": decision.risk_reducing,
            "network_write_authorized": False,
            "next_action": "OPERATOR_DECISION_REQUIRED",
        }
        package = PreparedCryptoProtectionPackage(
            **package_values,
            package_hash=_hash_json(_package_payload_from_values(package_values)),
        )
        return CryptoProtectionPreparationResult(
            package=package,
            order=order,
            broker_order=broker_order,
            lifecycle_state=lifecycle_state,
        )

    @staticmethod
    def _validate_entry_reconciliation(
        *,
        binding,
        state: CryptoLifecycleState,
        entry_order: AlpacaPaperCryptoOrderRequest,
        reconciliation: CryptoBrokerReconciliation,
        now: datetime,
    ) -> None:
        if state.status is not CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED:
            raise CryptoProtectionPreparationBlocked("protection requires terminal reconciled entry exposure")
        if not state.entry_terminal or state.entry_filled_quantity <= 0 or state.confirmed_net_long_quantity <= 0:
            raise CryptoProtectionPreparationBlocked("entry lifecycle has no protectable confirmed long exposure")
        if entry_order.client_order_id != binding.entry_client_order_id:
            raise CryptoProtectionPreparationBlocked("entry request client_order_id differs from lifecycle")
        if entry_order.fingerprint != binding.entry_order_fingerprint:
            raise CryptoProtectionPreparationBlocked("entry request fingerprint differs from lifecycle")
        broker = reconciliation.order
        position = reconciliation.position
        if not broker.terminal or broker.filled_quantity <= 0:
            raise CryptoProtectionPreparationBlocked("entry reconciliation must be terminal with positive fill")
        if broker.client_order_id != entry_order.client_order_id:
            raise CryptoProtectionPreparationBlocked("entry reconciliation client_order_id mismatch")
        if broker.symbol != binding.symbol or position.symbol != binding.symbol:
            raise CryptoProtectionPreparationBlocked("entry reconciliation symbol mismatch")
        if broker.quantity != entry_order.quantity:
            raise CryptoProtectionPreparationBlocked("entry reconciliation requested quantity mismatch")
        if broker.filled_quantity != state.entry_filled_quantity:
            raise CryptoProtectionPreparationBlocked("entry reconciled fill differs from lifecycle")
        if broker.broker_order_id != state.entry_broker_order_id:
            raise CryptoProtectionPreparationBlocked("entry broker order id differs from lifecycle")
        if broker.status != state.entry_broker_status:
            raise CryptoProtectionPreparationBlocked("entry broker status differs from lifecycle")
        if position.absent or position.quantity <= 0:
            raise CryptoProtectionPreparationBlocked("entry reconciliation must confirm existing long position")
        if position.quantity != state.confirmed_net_long_quantity:
            raise CryptoProtectionPreparationBlocked("broker position differs from lifecycle confirmed net long")
        if position.quantity > broker.filled_quantity:
            raise CryptoProtectionPreparationBlocked("confirmed long position exceeds reconciled entry fill")
        if reconciliation.observed_at > now + timedelta(seconds=3):
            raise CryptoProtectionPreparationBlocked("entry reconciliation is future-dated")
        age = now - reconciliation.observed_at.astimezone(timezone.utc)
        if age < timedelta(seconds=-3) or age >= PROTECTION_PREPARATION_EVIDENCE_TTL:
            raise CryptoProtectionPreparationBlocked("entry reconciliation is stale")

    @staticmethod
    def _validate_product_evidence(
        *,
        binding,
        intent: OrderIntent,
        decision: RiskDecision,
        market_attestation: AlpacaPaperCryptoMarketAttestation,
        account_attestation: AlpacaPaperAccountAttestation,
        asset_attestation: AlpacaPaperCryptoAssetAttestation,
        product_profile: ProductCapabilities,
        now: datetime,
    ) -> None:
        if intent.side is not Side.SELL or intent.order_type is not OrderType.LIMIT:
            raise CryptoProtectionPreparationBlocked("crypto protection Safety intent must be SELL LIMIT")
        if intent.symbol != binding.symbol or market_attestation.market.symbol != binding.symbol:
            raise CryptoProtectionPreparationBlocked("protection intent/market symbol differs from lifecycle")
        if intent.limit_price is None or intent.limit_price <= 0:
            raise CryptoProtectionPreparationBlocked("protection Safety intent requires positive limit price")
        if decision.status is not RiskDecisionStatus.APPROVED or decision.risk_reducing is not True:
            raise CryptoProtectionPreparationBlocked("crypto protection requires APPROVED risk-reducing decision")
        if decision.intent_id != intent.intent_id or decision.intent_fingerprint != intent_fingerprint(intent):
            raise CryptoProtectionPreparationBlocked("protection RiskDecision is not bound to exact intent")
        if decision.market_fingerprint != market_fingerprint(market_attestation.market):
            raise CryptoProtectionPreparationBlocked("protection RiskDecision is not bound to exact market")
        if now > decision.valid_until.astimezone(timezone.utc):
            raise CryptoProtectionPreparationBlocked("protection RiskDecision is expired")
        expected_notional = intent.quantity * intent.limit_price
        if decision.approved_notional != expected_notional:
            raise CryptoProtectionPreparationBlocked("protection approved notional differs from exact SELL intent")
        if account_attestation.status != "ACTIVE" or account_attestation.currency != "USD":
            raise CryptoProtectionPreparationBlocked("crypto protection requires active USD PAPER account")
        if asset_attestation.account_attestation_fingerprint != account_attestation.fingerprint:
            raise CryptoProtectionPreparationBlocked("protection asset/account evidence binding mismatch")
        if asset_attestation.credential_reference != account_attestation.credential_reference:
            raise CryptoProtectionPreparationBlocked("protection asset/account credential references differ")
        if asset_attestation.fingerprint != binding.asset_attestation_fingerprint:
            raise CryptoProtectionPreparationBlocked("protection must use lifecycle-bound asset evidence")
        if product_profile.fingerprint != binding.product_profile_fingerprint:
            raise CryptoProtectionPreparationBlocked("protection must use lifecycle-bound product profile")
        if product_profile.asset_class is not AssetClass.CRYPTO:
            raise CryptoProtectionPreparationBlocked("crypto protection requires CRYPTO ProductCapabilities")
        if product_profile.protection_model is not ProtectionModel.CRYPTO_STOP_LIMIT:
            raise CryptoProtectionPreparationBlocked("crypto protection requires CRYPTO_STOP_LIMIT model")
        if product_profile.marginable or product_profile.shortable:
            raise CryptoProtectionPreparationBlocked("crypto protection forbids margin/short capability")
        product_profile.require_order(order_type=BrokerOrderType.STOP_LIMIT, time_in_force=TimeInForce.GTC)
        product_profile.require_margin(uses_margin=False)
        product_profile.require_opening_short(opening_short=False)
        for label, observed in (
            ("account", account_attestation.attested_at),
            ("asset", asset_attestation.observed_at),
            ("product profile", product_profile.observed_at),
            ("market", market_attestation.received_at),
        ):
            age = now - observed.astimezone(timezone.utc)
            if age < timedelta(seconds=-3) or age >= PROTECTION_PREPARATION_EVIDENCE_TTL:
                raise CryptoProtectionPreparationBlocked(f"crypto protection {label} evidence is stale or future-dated")


def _package_payload(value: PreparedCryptoProtectionPackage, *, include_hash: bool) -> dict[str, object]:
    payload = _package_payload_from_values(
        {
            "lifecycle_id": value.lifecycle_id,
            "order_id": value.order_id,
            "client_order_id": value.client_order_id,
            "symbol": value.symbol,
            "entry_client_order_id": value.entry_client_order_id,
            "entry_broker_order_id": value.entry_broker_order_id,
            "entry_reconciliation_fingerprint": value.entry_reconciliation_fingerprint,
            "confirmed_entry_filled_quantity": value.confirmed_entry_filled_quantity,
            "confirmed_net_long_quantity": value.confirmed_net_long_quantity,
            "intent_fingerprint": value.intent_fingerprint,
            "risk_decision_id": value.risk_decision_id,
            "risk_decision_fingerprint": value.risk_decision_fingerprint,
            "risk_decision_safety_state_version": value.risk_decision_safety_state_version,
            "risk_decision_valid_until": value.risk_decision_valid_until,
            "market_fingerprint": value.market_fingerprint,
            "market_attestation_fingerprint": value.market_attestation_fingerprint,
            "account_attestation_fingerprint": value.account_attestation_fingerprint,
            "account_reference": value.account_reference,
            "credential_reference": value.credential_reference,
            "asset_attestation_fingerprint": value.asset_attestation_fingerprint,
            "product_profile_fingerprint": value.product_profile_fingerprint,
            "crypto_order_fingerprint": value.crypto_order_fingerprint,
            "crypto_order_payload_hash": value.crypto_order_payload_hash,
            "lifecycle_binding_hash": value.lifecycle_binding_hash,
            "lifecycle_control_hash": value.lifecycle_control_hash,
            "lifecycle_event_head_hash": value.lifecycle_event_head_hash,
            "quantity": value.quantity,
            "stop_price": value.stop_price,
            "limit_price": value.limit_price,
            "prepared_at": value.prepared_at,
            "execution_deadline": value.execution_deadline,
            "order_status": value.order_status,
            "broker_order_type": value.broker_order_type,
            "time_in_force": value.time_in_force,
            "risk_reducing": value.risk_reducing,
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
    "CryptoPaperProtectionCoordinator",
    "CryptoProtectionCoordinatorError",
    "CryptoProtectionPreparationBlocked",
    "CryptoProtectionPreparationResult",
    "PROTECTION_PREPARATION_EVIDENCE_TTL",
    "PreparedCryptoProtectionPackage",
]
