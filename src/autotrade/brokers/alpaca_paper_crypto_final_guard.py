from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import OrderStatus, intent_fingerprint
from autotrade.health_bridge import HealthBridgeControlProvider, HealthBridgeError, HealthRiskMode
from autotrade.product_profile import AssetClass, BrokerOrderType, ProductCapabilities, TimeInForce
from autotrade.state import OrderStore, PortfolioStore, SafetyStateStore

from .alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from .alpaca_paper_crypto_canary_coordinator import (
    FIRST_CANARY_MAX_ACCOUNT_FRACTION,
    FIRST_CANARY_MAX_NOTIONAL,
    PreparedCryptoPaperCanaryPackage,
)
from .alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from .alpaca_paper_crypto_market_data import AlpacaPaperCryptoMarketAttestation
from .alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecision,
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
)
from .alpaca_paper_crypto_order import AlpacaPaperCryptoOrderRequest, CryptoOrderRole
from .alpaca_paper_flat_account import PaperFlatAccountAttestation
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_FINAL_EVIDENCE_TTL = timedelta(seconds=5)
_FUTURE_TOLERANCE = timedelta(seconds=2)
_ZERO = Decimal("0")
_ONE = Decimal("1")


class CryptoFinalWriteError(RuntimeError):
    pass


class CryptoFinalWriteBlocked(CryptoFinalWriteError):
    def __init__(self, reasons: list[str] | tuple[str, ...]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


class CryptoFinalWritePhase(StrEnum):
    PRE_CONSUME = "PRE_CONSUME"
    PRE_IO = "PRE_IO"


@dataclass(frozen=True, slots=True)
class CryptoFinalWriteAttestation:
    phase: CryptoFinalWritePhase
    package_hash: str
    preparation_hash: str
    operator_decision_hash: str
    operator_status: CryptoOperatorDecisionStatus
    attempt_id: str
    order_id: str
    client_order_id: str
    intent_fingerprint: str
    risk_decision_id: str
    safety_state_version: int
    portfolio_version: int
    portfolio_snapshot_id: str
    strategy_health_fingerprint: str
    portfolio_health_fingerprint: str
    account_reference: str
    credential_reference: str
    fresh_account_fingerprint: str
    prepared_asset_fingerprint: str
    fresh_asset_fingerprint: str
    asset_contract_fingerprint: str
    prepared_product_profile_fingerprint: str
    fresh_product_profile_fingerprint: str
    product_contract_fingerprint: str
    fresh_market_attestation_fingerprint: str
    flat_account_fingerprint: str
    lifecycle_binding_hash: str
    lifecycle_control_hash: str
    lifecycle_event_head_hash: str
    lifecycle_status: CryptoLifecycleStatus
    entry_attempt_count: int
    previous_attestation_hash: str | None
    observed_at: datetime
    attestation_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.phase, CryptoFinalWritePhase):
            raise ValueError("phase must be CryptoFinalWritePhase")
        if not isinstance(self.operator_status, CryptoOperatorDecisionStatus):
            raise ValueError("operator_status must be CryptoOperatorDecisionStatus")
        if not isinstance(self.lifecycle_status, CryptoLifecycleStatus):
            raise ValueError("lifecycle_status must be CryptoLifecycleStatus")
        for label, value in (
            ("package_hash", self.package_hash),
            ("preparation_hash", self.preparation_hash),
            ("operator_decision_hash", self.operator_decision_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("fresh_account_fingerprint", self.fresh_account_fingerprint),
            ("prepared_asset_fingerprint", self.prepared_asset_fingerprint),
            ("fresh_asset_fingerprint", self.fresh_asset_fingerprint),
            ("asset_contract_fingerprint", self.asset_contract_fingerprint),
            ("prepared_product_profile_fingerprint", self.prepared_product_profile_fingerprint),
            ("fresh_product_profile_fingerprint", self.fresh_product_profile_fingerprint),
            ("product_contract_fingerprint", self.product_contract_fingerprint),
            ("fresh_market_attestation_fingerprint", self.fresh_market_attestation_fingerprint),
            ("flat_account_fingerprint", self.flat_account_fingerprint),
            ("lifecycle_binding_hash", self.lifecycle_binding_hash),
            ("lifecycle_control_hash", self.lifecycle_control_hash),
            ("lifecycle_event_head_hash", self.lifecycle_event_head_hash),
            ("attestation_hash", self.attestation_hash),
        ):
            if not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if self.safety_state_version < 0 or self.portfolio_version <= 0:
            raise ValueError("final-write state versions are invalid")
        if self.entry_attempt_count < 0:
            raise ValueError("entry_attempt_count cannot be negative")
        if not self.account_reference or not self.credential_reference:
            raise ValueError("account/credential references are required")
        _require_aware(self.observed_at, "observed_at")
        if self.phase is CryptoFinalWritePhase.PRE_CONSUME:
            if self.previous_attestation_hash is not None:
                raise ValueError("PRE_CONSUME cannot carry previous_attestation_hash")
        elif self.previous_attestation_hash is None or not _HASH_RE.fullmatch(self.previous_attestation_hash):
            raise ValueError("PRE_IO requires previous_attestation_hash")


class CryptoPaperFinalWriteGuard:
    """Offline two-phase just-in-time guard around one crypto PAPER entry POST.

    The guard never performs network I/O and never mutates OMS, operator or
    lifecycle state. PRE_CONSUME proves the exact human-approved package is
    still safe while OMS is VALIDATED and lifecycle is ENTRY_PREPARED.
    PRE_IO proves the same authority immediately after the operator decision is
    consumed, OMS is SUBMITTING and lifecycle is durably UNKNOWN, before the
    dedicated writer may touch the network.
    """

    def __init__(
        self,
        *,
        order_store: OrderStore,
        safety_state_store: SafetyStateStore,
        portfolio_store: PortfolioStore,
        health_bridge: HealthBridgeControlProvider,
        portfolio_health_entity_id: str,
    ) -> None:
        if not portfolio_health_entity_id or portfolio_health_entity_id != portfolio_health_entity_id.strip():
            raise ValueError("portfolio_health_entity_id must be canonical non-empty text")
        self._orders = order_store
        self._safety = safety_state_store
        self._portfolio = portfolio_store
        self._health = health_bridge
        self._portfolio_health_entity_id = portfolio_health_entity_id

    def authorize(
        self,
        *,
        package: PreparedCryptoPaperCanaryPackage,
        operator_decision: CryptoOperatorDecision,
        operator_registry: SQLiteCryptoOperatorDecisionRegistry,
        broker_order: AlpacaPaperCryptoOrderRequest,
        lifecycle: SQLiteCryptoPaperLifecycle,
        prepared_account: AlpacaPaperAccountAttestation,
        prepared_asset: AlpacaPaperCryptoAssetAttestation,
        prepared_product_profile: ProductCapabilities,
        fresh_account: AlpacaPaperAccountAttestation,
        fresh_asset: AlpacaPaperCryptoAssetAttestation,
        fresh_product_profile: ProductCapabilities,
        fresh_market: AlpacaPaperCryptoMarketAttestation,
        fresh_flat_account: PaperFlatAccountAttestation,
        now: datetime,
        phase: CryptoFinalWritePhase,
        expected_attempt_id: str | None = None,
        previous_attestation: CryptoFinalWriteAttestation | None = None,
    ) -> CryptoFinalWriteAttestation:
        _require_aware(now, "now")
        if not isinstance(phase, CryptoFinalWritePhase):
            raise ValueError("phase must be CryptoFinalWritePhase")
        observed_at = now.astimezone(timezone.utc)
        reasons: list[str] = []

        if observed_at >= package.execution_deadline.astimezone(timezone.utc):
            reasons.append("prepared crypto package is expired")
        if observed_at >= package.risk_decision_valid_until.astimezone(timezone.utc):
            reasons.append("prepared RiskDecision is expired")
        if not operator_decision.is_valid_at(observed_at):
            reasons.append("human crypto operator decision is not valid at final-write observation")
        context = operator_decision.context
        if context.prepared_package_hash != package.package_hash:
            reasons.append("operator decision/package hash mismatch")
        if context.order_id != package.order_id or context.client_order_id != package.client_order_id:
            reasons.append("operator decision/order identity mismatch")
        if context.lifecycle_id != package.lifecycle_id:
            reasons.append("operator decision/lifecycle identity mismatch")
        if context.crypto_order_fingerprint != package.crypto_order_fingerprint:
            reasons.append("operator decision/crypto order fingerprint mismatch")
        if context.crypto_order_payload_hash != package.crypto_order_payload_hash:
            reasons.append("operator decision/crypto payload hash mismatch")

        if broker_order.role is not CryptoOrderRole.ENTRY:
            reasons.append("first crypto final-write guard authorizes ENTRY role only")
        if broker_order.order_type is not BrokerOrderType.LIMIT or broker_order.time_in_force is not TimeInForce.IOC:
            reasons.append("first crypto final-write guard requires LIMIT IOC")
        if broker_order.fingerprint != package.crypto_order_fingerprint:
            reasons.append("broker order fingerprint changed after preparation")
        if broker_order.payload_hash != package.crypto_order_payload_hash:
            reasons.append("broker order payload changed after preparation")
        if broker_order.client_order_id != package.client_order_id or broker_order.symbol != package.symbol:
            reasons.append("broker order identity changed after preparation")
        if broker_order.quantity != package.quantity or broker_order.limit_price != package.limit_price:
            reasons.append("broker order economics changed after preparation")

        # Original evidence must still be exactly the evidence the human approved.
        if prepared_account.fingerprint != package.account_attestation_fingerprint:
            reasons.append("prepared account evidence no longer matches package")
        if prepared_asset.fingerprint != package.asset_attestation_fingerprint:
            reasons.append("prepared asset evidence no longer matches package")
        if prepared_product_profile.fingerprint != package.product_profile_fingerprint:
            reasons.append("prepared ProductCapabilities no longer matches package")
        if prepared_asset.account_attestation_fingerprint != prepared_account.fingerprint:
            reasons.append("prepared asset/account evidence binding mismatch")
        if prepared_asset.credential_reference != prepared_account.credential_reference:
            reasons.append("prepared asset/account credential binding mismatch")
        if prepared_product_profile.source_fingerprint != prepared_asset.fingerprint:
            reasons.append("prepared ProductCapabilities/asset evidence binding mismatch")

        # Fresh evidence may have new request/timestamp hashes but must represent
        # the exact same account and stable product contract.
        if fresh_account.account_id != prepared_account.account_id:
            reasons.append("fresh account_id changed")
        if fresh_account.account_reference != prepared_account.account_reference:
            reasons.append("fresh stable account reference changed")
        if fresh_account.credential_reference != prepared_account.credential_reference:
            reasons.append("fresh credential reference changed")
        if fresh_account.status != "ACTIVE" or fresh_account.currency != "USD":
            reasons.append("fresh PAPER account is not ACTIVE USD")
        if fresh_account.buying_power < package.notional:
            reasons.append("fresh buying power is below approved canary notional")
        fresh_cap = min(
            FIRST_CANARY_MAX_NOTIONAL,
            fresh_account.portfolio_value * FIRST_CANARY_MAX_ACCOUNT_FRACTION,
            fresh_account.buying_power,
        )
        if package.notional > fresh_cap:
            reasons.append("fresh account no longer satisfies conservative first-canary cap")

        if fresh_asset.account_attestation_fingerprint != fresh_account.fingerprint:
            reasons.append("fresh asset is not bound to fresh account evidence")
        if fresh_asset.credential_reference != fresh_account.credential_reference:
            reasons.append("fresh asset credential reference mismatch")
        if fresh_asset.contract_fingerprint != prepared_asset.contract_fingerprint:
            reasons.append("crypto asset contract changed since human-approved preparation")
        if fresh_product_profile.asset_class is not AssetClass.CRYPTO:
            reasons.append("fresh ProductCapabilities is not CRYPTO")
        if fresh_product_profile.source_fingerprint != fresh_asset.fingerprint:
            reasons.append("fresh ProductCapabilities is not bound to fresh asset evidence")
        if fresh_product_profile.contract_fingerprint != prepared_product_profile.contract_fingerprint:
            reasons.append("crypto ProductCapabilities contract changed since preparation")
        try:
            fresh_product_profile.require_order(
                order_type=BrokerOrderType.LIMIT,
                time_in_force=TimeInForce.IOC,
            )
            fresh_product_profile.require_margin(uses_margin=False)
            fresh_product_profile.require_opening_short(opening_short=False)
        except Exception as exc:  # fail closed on product drift
            reasons.append(f"fresh crypto ProductCapabilities reject first-canary order: {exc}")
        if package.quantity % fresh_asset.min_trade_increment != 0:
            reasons.append("prepared quantity violates fresh broker trade increment")
        if package.quantity < fresh_asset.min_order_size:
            reasons.append("prepared quantity is below fresh broker minimum")
        if package.limit_price % fresh_asset.price_increment != 0:
            reasons.append("prepared limit price violates fresh broker price increment")

        if fresh_market.market.symbol != package.symbol:
            reasons.append("fresh crypto market symbol mismatch")
        if not fresh_flat_account.clean_for_first_canary:
            reasons.append("fresh broker account is not flat for first crypto canary")
        if fresh_flat_account.account_attestation_fingerprint != fresh_account.fingerprint:
            reasons.append("fresh flat-account evidence is not bound to fresh account")
        if fresh_flat_account.credential_reference != fresh_account.credential_reference:
            reasons.append("fresh flat-account credential reference mismatch")

        for label, timestamp in (
            ("account", fresh_account.attested_at),
            ("asset", fresh_asset.observed_at),
            ("ProductCapabilities", fresh_product_profile.observed_at),
            ("market", fresh_market.received_at),
            ("flat-account", fresh_flat_account.attested_at),
        ):
            _check_freshness(
                reasons=reasons,
                label=label,
                timestamp=timestamp,
                now=observed_at,
            )

        current_order = self._orders.get_by_order_id(package.order_id)
        if current_order is None:
            reasons.append("authoritative OMS order is missing")
            current_intent_fingerprint = "0" * 64
        else:
            current_intent_fingerprint = intent_fingerprint(current_order.intent)
            if current_intent_fingerprint != package.intent_fingerprint:
                reasons.append("authoritative OMS intent changed after preparation")
            if current_order.risk_decision_id != package.risk_decision_id:
                reasons.append("authoritative OMS RiskDecision identity changed")
            if current_order.intent.symbol != package.symbol:
                reasons.append("authoritative OMS symbol changed")
            if current_order.intent.quantity != package.quantity:
                reasons.append("authoritative OMS quantity changed")
            if current_order.intent.limit_price != package.limit_price:
                reasons.append("authoritative OMS limit price changed")

        safety = self._safety.get()
        if safety.kill_switch_active:
            reasons.append("authoritative kill switch is active")
        if safety.circuit_active:
            reasons.append("authoritative safety circuit is active")
        if safety.version != package.risk_decision_safety_state_version:
            reasons.append("authoritative Safety state version changed after approved RiskDecision")

        try:
            versioned_portfolio = self._portfolio.get()
        except Exception as exc:
            raise CryptoFinalWriteBlocked(("authoritative Portfolio State unavailable",)) from exc
        portfolio = versioned_portfolio.snapshot
        if not portfolio.reconciliation_ok:
            reasons.append("authoritative Portfolio State reconciliation is not clean")
        if not portfolio.broker_state_known:
            reasons.append("authoritative broker state is unknown")
        if portfolio.open_orders != 0:
            reasons.append("authoritative Portfolio State has open orders")
        if portfolio.gross_exposure != _ZERO or portfolio.net_exposure != _ZERO:
            reasons.append("first crypto canary requires zero authoritative portfolio exposure")
        if any(value != _ZERO for value in portfolio.signed_position_notional_by_symbol.values()):
            reasons.append("first crypto canary requires zero authoritative symbol positions")

        if current_order is None:
            health = None
        else:
            try:
                health = self._health.effective_control(
                    strategy_id=current_order.intent.strategy_id,
                    portfolio_entity_id=self._portfolio_health_entity_id,
                    now=observed_at,
                )
            except (HealthBridgeError, Exception) as exc:  # noqa: BLE001
                raise CryptoFinalWriteBlocked(("authoritative Health control unavailable",)) from exc
            if health.mode is not HealthRiskMode.NORMAL:
                reasons.append("authoritative Health mode is not NORMAL")
            if (
                health.order_multiplier != _ONE
                or health.strategy_multiplier != _ONE
                or health.portfolio_multiplier != _ONE
            ):
                reasons.append("authoritative Health multipliers are not exactly 1")

        try:
            operator_state = operator_registry.get(context.preparation_hash)
        except Exception as exc:
            raise CryptoFinalWriteBlocked(("durable crypto operator decision is unavailable or corrupt",)) from exc
        if operator_state.decision != operator_decision:
            reasons.append("durable crypto operator decision differs from supplied decision")

        try:
            lifecycle_snapshot = lifecycle.snapshot(package.lifecycle_id)
        except Exception as exc:
            raise CryptoFinalWriteBlocked(("durable crypto lifecycle is unavailable or corrupt",)) from exc
        binding = lifecycle_snapshot.binding
        lifecycle_state = lifecycle_snapshot.state
        if binding.fingerprint != package.lifecycle_binding_hash:
            reasons.append("durable crypto lifecycle binding hash mismatch")
        if binding.entry_order_fingerprint != package.crypto_order_fingerprint:
            reasons.append("durable lifecycle entry order fingerprint mismatch")
        if binding.entry_client_order_id != package.client_order_id:
            reasons.append("durable lifecycle client_order_id mismatch")
        if binding.entry_quantity != package.quantity:
            reasons.append("durable lifecycle quantity mismatch")
        if binding.asset_attestation_fingerprint != package.asset_attestation_fingerprint:
            reasons.append("durable lifecycle prepared asset evidence mismatch")
        if binding.product_profile_fingerprint != package.product_profile_fingerprint:
            reasons.append("durable lifecycle prepared ProductCapabilities mismatch")

        if phase is CryptoFinalWritePhase.PRE_CONSUME:
            if expected_attempt_id is not None:
                reasons.append("PRE_CONSUME must not carry expected_attempt_id")
            if previous_attestation is not None:
                reasons.append("PRE_CONSUME must not carry previous attestation")
            if current_order is not None and current_order.status is not OrderStatus.VALIDATED:
                reasons.append("PRE_CONSUME requires authoritative OMS VALIDATED")
            if operator_state.status is not CryptoOperatorDecisionStatus.ISSUED:
                reasons.append("PRE_CONSUME requires unconsumed ISSUED human decision")
            if lifecycle_state.status is not CryptoLifecycleStatus.ENTRY_PREPARED:
                reasons.append("PRE_CONSUME requires ENTRY_PREPARED crypto lifecycle")
            if lifecycle_state.entry_attempt_count != 0:
                reasons.append("PRE_CONSUME requires zero crypto entry attempts")
            if lifecycle_state.control_hash != package.lifecycle_control_hash:
                reasons.append("PRE_CONSUME lifecycle control hash changed after preparation")
            if lifecycle_state.event_head_hash != package.lifecycle_event_head_hash:
                reasons.append("PRE_CONSUME lifecycle event head changed after preparation")
        else:
            if expected_attempt_id != context.attempt_id:
                reasons.append("PRE_IO expected attempt_id does not match human-approved attempt")
            if previous_attestation is None:
                reasons.append("PRE_IO requires actual PRE_CONSUME attestation")
            if current_order is not None and current_order.status is not OrderStatus.SUBMITTING:
                reasons.append("PRE_IO requires authoritative OMS SUBMITTING")
            if operator_state.status is not CryptoOperatorDecisionStatus.CONSUMED:
                reasons.append("PRE_IO requires consumed human crypto operator decision")
            if operator_state.consumed_attempt_id != context.attempt_id:
                reasons.append("PRE_IO operator decision consumed by wrong attempt")
            if lifecycle_state.status is not CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN:
                reasons.append("PRE_IO requires durable ENTRY_SUBMISSION_UNKNOWN before broker I/O")
            if lifecycle_state.entry_attempt_count != 1:
                reasons.append("PRE_IO requires exactly one durable entry attempt")
            if previous_attestation is not None:
                _validate_predecessor(
                    reasons=reasons,
                    previous=previous_attestation,
                    package=package,
                    operator_decision=operator_decision,
                    prepared_asset=prepared_asset,
                    fresh_account=fresh_account,
                    fresh_asset=fresh_asset,
                    prepared_product_profile=prepared_product_profile,
                    fresh_product_profile=fresh_product_profile,
                    fresh_market=fresh_market,
                    fresh_flat_account=fresh_flat_account,
                    safety_state_version=safety.version,
                    portfolio_version=versioned_portfolio.version,
                    strategy_health_fingerprint=(
                        health.strategy_state_fingerprint if health is not None else "0" * 64
                    ),
                    portfolio_health_fingerprint=(
                        health.portfolio_state_fingerprint if health is not None else "0" * 64
                    ),
                    observed_at=observed_at,
                )

        if reasons:
            raise CryptoFinalWriteBlocked(reasons)
        assert current_order is not None
        assert health is not None

        payload = {
            "phase": phase.value,
            "package_hash": package.package_hash,
            "preparation_hash": context.preparation_hash,
            "operator_decision_hash": operator_decision.decision_hash,
            "operator_status": operator_state.status.value,
            "attempt_id": context.attempt_id,
            "order_id": package.order_id,
            "client_order_id": package.client_order_id,
            "intent_fingerprint": current_intent_fingerprint,
            "risk_decision_id": package.risk_decision_id,
            "safety_state_version": safety.version,
            "portfolio_version": versioned_portfolio.version,
            "portfolio_snapshot_id": portfolio.snapshot_id,
            "strategy_health_fingerprint": health.strategy_state_fingerprint,
            "portfolio_health_fingerprint": health.portfolio_state_fingerprint,
            "account_reference": fresh_account.account_reference,
            "credential_reference": fresh_account.credential_reference,
            "fresh_account_fingerprint": fresh_account.fingerprint,
            "prepared_asset_fingerprint": prepared_asset.fingerprint,
            "fresh_asset_fingerprint": fresh_asset.fingerprint,
            "asset_contract_fingerprint": fresh_asset.contract_fingerprint,
            "prepared_product_profile_fingerprint": prepared_product_profile.fingerprint,
            "fresh_product_profile_fingerprint": fresh_product_profile.fingerprint,
            "product_contract_fingerprint": fresh_product_profile.contract_fingerprint,
            "fresh_market_attestation_fingerprint": fresh_market.fingerprint,
            "flat_account_fingerprint": fresh_flat_account.fingerprint,
            "lifecycle_binding_hash": binding.fingerprint,
            "lifecycle_control_hash": lifecycle_state.control_hash,
            "lifecycle_event_head_hash": lifecycle_state.event_head_hash,
            "lifecycle_status": lifecycle_state.status.value,
            "entry_attempt_count": lifecycle_state.entry_attempt_count,
            "previous_attestation_hash": (
                previous_attestation.attestation_hash if previous_attestation is not None else None
            ),
            "observed_at": observed_at.isoformat(),
        }
        attestation_hash = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        return CryptoFinalWriteAttestation(
            phase=phase,
            package_hash=package.package_hash,
            preparation_hash=context.preparation_hash,
            operator_decision_hash=operator_decision.decision_hash,
            operator_status=operator_state.status,
            attempt_id=context.attempt_id,
            order_id=package.order_id,
            client_order_id=package.client_order_id,
            intent_fingerprint=current_intent_fingerprint,
            risk_decision_id=package.risk_decision_id,
            safety_state_version=safety.version,
            portfolio_version=versioned_portfolio.version,
            portfolio_snapshot_id=portfolio.snapshot_id,
            strategy_health_fingerprint=health.strategy_state_fingerprint,
            portfolio_health_fingerprint=health.portfolio_state_fingerprint,
            account_reference=fresh_account.account_reference,
            credential_reference=fresh_account.credential_reference,
            fresh_account_fingerprint=fresh_account.fingerprint,
            prepared_asset_fingerprint=prepared_asset.fingerprint,
            fresh_asset_fingerprint=fresh_asset.fingerprint,
            asset_contract_fingerprint=fresh_asset.contract_fingerprint,
            prepared_product_profile_fingerprint=prepared_product_profile.fingerprint,
            fresh_product_profile_fingerprint=fresh_product_profile.fingerprint,
            product_contract_fingerprint=fresh_product_profile.contract_fingerprint,
            fresh_market_attestation_fingerprint=fresh_market.fingerprint,
            flat_account_fingerprint=fresh_flat_account.fingerprint,
            lifecycle_binding_hash=binding.fingerprint,
            lifecycle_control_hash=lifecycle_state.control_hash,
            lifecycle_event_head_hash=lifecycle_state.event_head_hash,
            lifecycle_status=lifecycle_state.status,
            entry_attempt_count=lifecycle_state.entry_attempt_count,
            previous_attestation_hash=(
                previous_attestation.attestation_hash if previous_attestation is not None else None
            ),
            observed_at=observed_at,
            attestation_hash=attestation_hash,
        )


def _validate_predecessor(
    *,
    reasons: list[str],
    previous: CryptoFinalWriteAttestation,
    package: PreparedCryptoPaperCanaryPackage,
    operator_decision: CryptoOperatorDecision,
    prepared_asset: AlpacaPaperCryptoAssetAttestation,
    fresh_account: AlpacaPaperAccountAttestation,
    fresh_asset: AlpacaPaperCryptoAssetAttestation,
    prepared_product_profile: ProductCapabilities,
    fresh_product_profile: ProductCapabilities,
    fresh_market: AlpacaPaperCryptoMarketAttestation,
    fresh_flat_account: PaperFlatAccountAttestation,
    safety_state_version: int,
    portfolio_version: int,
    strategy_health_fingerprint: str,
    portfolio_health_fingerprint: str,
    observed_at: datetime,
) -> None:
    if previous.phase is not CryptoFinalWritePhase.PRE_CONSUME:
        reasons.append("PRE_IO predecessor must be PRE_CONSUME")
    if previous.package_hash != package.package_hash:
        reasons.append("PRE_IO predecessor package hash mismatch")
    if previous.preparation_hash != operator_decision.context.preparation_hash:
        reasons.append("PRE_IO predecessor preparation hash mismatch")
    if previous.operator_decision_hash != operator_decision.decision_hash:
        reasons.append("PRE_IO predecessor operator decision hash mismatch")
    if previous.attempt_id != operator_decision.context.attempt_id:
        reasons.append("PRE_IO predecessor attempt_id mismatch")
    if previous.order_id != package.order_id or previous.client_order_id != package.client_order_id:
        reasons.append("PRE_IO predecessor order identity mismatch")
    if previous.safety_state_version != safety_state_version:
        reasons.append("Safety state changed between PRE_CONSUME and PRE_IO")
    if previous.portfolio_version != portfolio_version:
        reasons.append("Portfolio State changed between PRE_CONSUME and PRE_IO")
    if previous.strategy_health_fingerprint != strategy_health_fingerprint:
        reasons.append("strategy Health changed between PRE_CONSUME and PRE_IO")
    if previous.portfolio_health_fingerprint != portfolio_health_fingerprint:
        reasons.append("portfolio Health changed between PRE_CONSUME and PRE_IO")
    if previous.account_reference != fresh_account.account_reference:
        reasons.append("account reference changed between PRE_CONSUME and PRE_IO")
    if previous.credential_reference != fresh_account.credential_reference:
        reasons.append("credential reference changed between PRE_CONSUME and PRE_IO")
    if previous.fresh_account_fingerprint != fresh_account.fingerprint:
        reasons.append("fresh account evidence changed between PRE_CONSUME and PRE_IO")
    if previous.prepared_asset_fingerprint != prepared_asset.fingerprint:
        reasons.append("prepared asset evidence changed between PRE_CONSUME and PRE_IO")
    if previous.fresh_asset_fingerprint != fresh_asset.fingerprint:
        reasons.append("fresh asset evidence changed between PRE_CONSUME and PRE_IO")
    if previous.asset_contract_fingerprint != fresh_asset.contract_fingerprint:
        reasons.append("asset contract changed between PRE_CONSUME and PRE_IO")
    if previous.prepared_product_profile_fingerprint != prepared_product_profile.fingerprint:
        reasons.append("prepared ProductCapabilities changed between PRE_CONSUME and PRE_IO")
    if previous.fresh_product_profile_fingerprint != fresh_product_profile.fingerprint:
        reasons.append("fresh ProductCapabilities evidence changed between PRE_CONSUME and PRE_IO")
    if previous.product_contract_fingerprint != fresh_product_profile.contract_fingerprint:
        reasons.append("ProductCapabilities contract changed between PRE_CONSUME and PRE_IO")
    if previous.fresh_market_attestation_fingerprint != fresh_market.fingerprint:
        reasons.append("fresh market evidence changed between PRE_CONSUME and PRE_IO")
    if previous.flat_account_fingerprint != fresh_flat_account.fingerprint:
        reasons.append("fresh flat-account evidence changed between PRE_CONSUME and PRE_IO")
    if previous.lifecycle_status is not CryptoLifecycleStatus.ENTRY_PREPARED:
        reasons.append("PRE_IO predecessor did not observe ENTRY_PREPARED")
    if previous.entry_attempt_count != 0:
        reasons.append("PRE_IO predecessor did not observe zero attempts")
    if previous.observed_at > observed_at:
        reasons.append("PRE_IO observation cannot precede PRE_CONSUME")


def _check_freshness(
    *,
    reasons: list[str],
    label: str,
    timestamp: datetime,
    now: datetime,
) -> None:
    _require_aware(timestamp, label)
    value = timestamp.astimezone(timezone.utc)
    if value > now + _FUTURE_TOLERANCE:
        reasons.append(f"fresh {label} evidence is future-dated")
    elif now - value > _FINAL_EVIDENCE_TTL:
        reasons.append(f"fresh {label} evidence exceeds 5-second final-write TTL")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "CryptoFinalWriteAttestation",
    "CryptoFinalWriteBlocked",
    "CryptoFinalWriteError",
    "CryptoFinalWritePhase",
    "CryptoPaperFinalWriteGuard",
]
