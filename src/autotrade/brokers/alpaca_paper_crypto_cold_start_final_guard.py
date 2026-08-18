from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import OrderStatus, intent_fingerprint
from autotrade.persistence import SQLitePortfolioStore, SQLiteRuntime
from autotrade.product_profile import AssetClass, BrokerOrderType, ProductCapabilities, TimeInForce
from autotrade.risk_state import SQLiteR2SafetyStateStore
from autotrade.state import OrderStore

from .alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from .alpaca_paper_crypto_canary_coordinator import PreparedCryptoPaperCanaryPackage
from .alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus, SQLiteCryptoPaperLifecycle
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
_ZERO = Decimal("0")
COLD_START_SYMBOL = "BTC/USD"
COLD_START_SCOPE = "FIRST_TECHNICAL_CANARY_ONLY"
COLD_START_KILL_REASON = "R6_HEALTH_R4_EVIDENCE_REQUIRED"
COLD_START_MIN_NOTIONAL = Decimal("1")
COLD_START_MAX_NOTIONAL = Decimal("5")
COLD_START_MAX_ACCOUNT_FRACTION = Decimal("0.001")
_FINAL_EVIDENCE_TTL = timedelta(seconds=5)
_FUTURE_TOLERANCE = timedelta(seconds=2)


class CryptoColdStartFinalWriteError(RuntimeError):
    pass


class CryptoColdStartFinalWriteBlocked(CryptoColdStartFinalWriteError):
    def __init__(self, reasons: list[str] | tuple[str, ...]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


class CryptoColdStartFinalWritePhase(StrEnum):
    PRE_CONSUME = "PRE_CONSUME"
    PRE_IO = "PRE_IO"


@dataclass(frozen=True, slots=True)
class CryptoColdStartAuthoritySnapshot:
    safety_state_version: int
    kill_switch_active: bool
    kill_switch_reason: str
    circuit_active: bool
    portfolio_version: int
    portfolio_snapshot_id: str
    portfolio_equity: Decimal
    portfolio_gross_exposure: Decimal
    portfolio_net_exposure: Decimal
    portfolio_open_orders: int
    portfolio_reconciliation_ok: bool
    portfolio_broker_state_known: bool
    health_schema_present: bool
    health_state_rows: int
    health_bridge_rows: int
    state_fingerprint: str

    def __post_init__(self) -> None:
        if self.safety_state_version < 0 or self.portfolio_version <= 0:
            raise ValueError("cold-start authority versions are invalid")
        if self.portfolio_open_orders < 0 or self.health_state_rows < 0 or self.health_bridge_rows < 0:
            raise ValueError("cold-start authority counts cannot be negative")
        if not _HASH_RE.fullmatch(self.state_fingerprint):
            raise ValueError("cold-start authority fingerprint must be lowercase SHA-256")


class SQLiteCryptoColdStartAuthorityProvider:
    """Read authoritative cold-start state directly from the durable core DB.

    The provider never mutates Safety, Portfolio or Health. It intentionally
    reports the commissioning kill switch and missing Health as evidence rather
    than translating them into NORMAL Health authority.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        if not isinstance(runtime, SQLiteRuntime):
            raise TypeError("cold-start authority provider requires SQLiteRuntime")
        self._runtime = runtime

    def snapshot(self) -> CryptoColdStartAuthoritySnapshot:
        safety = SQLiteR2SafetyStateStore(self._runtime).get()
        versioned = SQLitePortfolioStore(self._runtime).get()
        portfolio = versioned.snapshot
        conn = self._runtime.connect()
        try:
            tables = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            health_schema_present = {"health_state_v2", "health_bridge_state"}.issubset(tables)
            health_rows = (
                int(conn.execute("SELECT COUNT(*) FROM health_state_v2").fetchone()[0])
                if "health_state_v2" in tables
                else -1
            )
            bridge_rows = (
                int(conn.execute("SELECT COUNT(*) FROM health_bridge_state").fetchone()[0])
                if "health_bridge_state" in tables
                else -1
            )
        finally:
            conn.close()
        payload = {
            "safety_state_version": safety.version,
            "kill_switch_active": safety.kill_switch_active,
            "kill_switch_reason": safety.kill_switch_reason,
            "circuit_active": safety.circuit_active,
            "portfolio_version": versioned.version,
            "portfolio_snapshot_id": portfolio.snapshot_id,
            "portfolio_equity": str(portfolio.equity),
            "portfolio_gross_exposure": str(portfolio.gross_exposure),
            "portfolio_net_exposure": str(portfolio.net_exposure),
            "portfolio_open_orders": portfolio.open_orders,
            "portfolio_reconciliation_ok": portfolio.reconciliation_ok,
            "portfolio_broker_state_known": portfolio.broker_state_known,
            "health_schema_present": health_schema_present,
            "health_state_rows": health_rows,
            "health_bridge_rows": bridge_rows,
        }
        fingerprint = sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        return CryptoColdStartAuthoritySnapshot(
            safety_state_version=safety.version,
            kill_switch_active=safety.kill_switch_active,
            kill_switch_reason=safety.kill_switch_reason,
            circuit_active=safety.circuit_active,
            portfolio_version=versioned.version,
            portfolio_snapshot_id=portfolio.snapshot_id,
            portfolio_equity=portfolio.equity,
            portfolio_gross_exposure=portfolio.gross_exposure,
            portfolio_net_exposure=portfolio.net_exposure,
            portfolio_open_orders=portfolio.open_orders,
            portfolio_reconciliation_ok=portfolio.reconciliation_ok,
            portfolio_broker_state_known=portfolio.broker_state_known,
            health_schema_present=health_schema_present,
            health_state_rows=health_rows,
            health_bridge_rows=bridge_rows,
            state_fingerprint=fingerprint,
        )


@dataclass(frozen=True, slots=True)
class CryptoColdStartFinalWriteAttestation:
    phase: CryptoColdStartFinalWritePhase
    bootstrap_scope: str
    bootstrap_kill_reason: str
    package_hash: str
    preparation_hash: str
    operator_decision_hash: str
    operator_status: CryptoOperatorDecisionStatus
    attempt_id: str
    order_id: str
    client_order_id: str
    intent_fingerprint: str
    risk_decision_id: str
    authority_state_fingerprint: str
    authoritative_safety_state_version: int
    portfolio_version: int
    portfolio_snapshot_id: str
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
        if not isinstance(self.phase, CryptoColdStartFinalWritePhase):
            raise ValueError("phase must be CryptoColdStartFinalWritePhase")
        if self.bootstrap_scope != COLD_START_SCOPE or self.bootstrap_kill_reason != COLD_START_KILL_REASON:
            raise ValueError("cold-start attestation scope/reason mismatch")
        if not isinstance(self.operator_status, CryptoOperatorDecisionStatus):
            raise ValueError("operator_status must be CryptoOperatorDecisionStatus")
        if not isinstance(self.lifecycle_status, CryptoLifecycleStatus):
            raise ValueError("lifecycle_status must be CryptoLifecycleStatus")
        for label, value in (
            ("package_hash", self.package_hash),
            ("preparation_hash", self.preparation_hash),
            ("operator_decision_hash", self.operator_decision_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("authority_state_fingerprint", self.authority_state_fingerprint),
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
        if self.authoritative_safety_state_version < 0 or self.portfolio_version <= 0:
            raise ValueError("cold-start attestation versions are invalid")
        if self.entry_attempt_count < 0:
            raise ValueError("entry_attempt_count cannot be negative")
        if not self.account_reference or not self.credential_reference:
            raise ValueError("account/credential references are required")
        _require_aware(self.observed_at, "observed_at")
        if self.phase is CryptoColdStartFinalWritePhase.PRE_CONSUME:
            if self.previous_attestation_hash is not None:
                raise ValueError("PRE_CONSUME cannot carry predecessor")
        elif self.previous_attestation_hash is None or not _HASH_RE.fullmatch(self.previous_attestation_hash):
            raise ValueError("PRE_IO requires predecessor hash")
        if self.attestation_hash != _attestation_hash(self):
            raise ValueError("cold-start final-write attestation hash mismatch")


class CryptoColdStartPaperFinalWriteGuard:
    """Offline two-phase authority for exactly one first technical PAPER canary.

    This is deliberately NOT the normal Health-NORMAL Final Guard. It accepts
    only the commissioned cold-start state: kill switch active for the exact
    missing-Health reason, no safety circuit, durable flat Portfolio v1 and zero
    authoritative Health/bridge rows. It performs no network I/O and no state
    mutation. Any real execution must still cross a separate checkpoint/bridge,
    durable lifecycle UNKNOWN and a guarded writer transport.
    """

    def __init__(
        self,
        *,
        order_store: OrderStore,
        authority_provider: SQLiteCryptoColdStartAuthorityProvider,
    ) -> None:
        self._orders = order_store
        self._authority = authority_provider

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
        phase: CryptoColdStartFinalWritePhase,
        expected_attempt_id: str | None = None,
        previous_attestation: CryptoColdStartFinalWriteAttestation | None = None,
    ) -> CryptoColdStartFinalWriteAttestation:
        _require_aware(now, "now")
        if not isinstance(phase, CryptoColdStartFinalWritePhase):
            raise ValueError("phase must be CryptoColdStartFinalWritePhase")
        observed_at = now.astimezone(timezone.utc)
        reasons: list[str] = []

        if package.symbol != COLD_START_SYMBOL:
            reasons.append("cold-start bootstrap is BTC/USD only")
        if not COLD_START_MIN_NOTIONAL <= package.notional <= COLD_START_MAX_NOTIONAL:
            reasons.append("cold-start notional must remain within USD 1-5")
        if package.network_write_authorized is not False or package.next_action != "OPERATOR_DECISION_REQUIRED":
            reasons.append("prepared package itself must remain non-executable")
        if observed_at >= package.execution_deadline.astimezone(timezone.utc):
            reasons.append("prepared crypto package is expired")
        if observed_at >= package.risk_decision_valid_until.astimezone(timezone.utc):
            reasons.append("prepared RiskDecision is expired")
        if not operator_decision.is_valid_at(observed_at):
            reasons.append("human operator decision is not valid")

        context = operator_decision.context
        if context.prepared_package_hash != package.package_hash:
            reasons.append("operator decision/package hash mismatch")
        if context.order_id != package.order_id or context.client_order_id != package.client_order_id:
            reasons.append("operator decision/order identity mismatch")
        if context.lifecycle_id != package.lifecycle_id:
            reasons.append("operator decision/lifecycle mismatch")
        if context.crypto_order_fingerprint != package.crypto_order_fingerprint:
            reasons.append("operator decision/order fingerprint mismatch")
        if context.crypto_order_payload_hash != package.crypto_order_payload_hash:
            reasons.append("operator decision/payload hash mismatch")

        if broker_order.role is not CryptoOrderRole.ENTRY:
            reasons.append("cold-start bootstrap authorizes ENTRY only")
        if broker_order.order_type is not BrokerOrderType.LIMIT or broker_order.time_in_force is not TimeInForce.IOC:
            reasons.append("cold-start bootstrap requires BUY LIMIT IOC")
        if broker_order.fingerprint != package.crypto_order_fingerprint or broker_order.payload_hash != package.crypto_order_payload_hash:
            reasons.append("broker order changed after preparation")
        if broker_order.client_order_id != package.client_order_id or broker_order.symbol != COLD_START_SYMBOL:
            reasons.append("broker order identity changed after preparation")
        if broker_order.quantity != package.quantity or broker_order.limit_price != package.limit_price:
            reasons.append("broker order economics changed after preparation")

        if prepared_account.fingerprint != package.account_attestation_fingerprint:
            reasons.append("prepared account evidence mismatch")
        if prepared_asset.fingerprint != package.asset_attestation_fingerprint:
            reasons.append("prepared asset evidence mismatch")
        if prepared_product_profile.fingerprint != package.product_profile_fingerprint:
            reasons.append("prepared ProductCapabilities evidence mismatch")
        if prepared_asset.account_attestation_fingerprint != prepared_account.fingerprint:
            reasons.append("prepared asset/account binding mismatch")
        if prepared_asset.credential_reference != prepared_account.credential_reference:
            reasons.append("prepared asset credential binding mismatch")
        if prepared_product_profile.source_fingerprint != prepared_asset.fingerprint:
            reasons.append("prepared ProductCapabilities/asset binding mismatch")

        if fresh_account.account_id != prepared_account.account_id:
            reasons.append("fresh account_id changed")
        if fresh_account.account_reference != prepared_account.account_reference:
            reasons.append("fresh account reference changed")
        if fresh_account.credential_reference != prepared_account.credential_reference:
            reasons.append("fresh credential reference changed")
        if fresh_account.status != "ACTIVE" or fresh_account.currency != "USD":
            reasons.append("fresh PAPER account is not ACTIVE USD")
        fresh_cap = min(
            COLD_START_MAX_NOTIONAL,
            fresh_account.portfolio_value * COLD_START_MAX_ACCOUNT_FRACTION,
            fresh_account.buying_power,
        )
        if package.notional > fresh_cap:
            reasons.append("fresh account no longer satisfies USD 5 cold-start cap")
        if fresh_asset.account_attestation_fingerprint != fresh_account.fingerprint:
            reasons.append("fresh asset/account binding mismatch")
        if fresh_asset.credential_reference != fresh_account.credential_reference:
            reasons.append("fresh asset credential mismatch")
        if fresh_asset.contract_fingerprint != prepared_asset.contract_fingerprint:
            reasons.append("crypto asset contract changed since preparation")
        if fresh_product_profile.asset_class is not AssetClass.CRYPTO:
            reasons.append("fresh ProductCapabilities is not CRYPTO")
        if fresh_product_profile.source_fingerprint != fresh_asset.fingerprint:
            reasons.append("fresh ProductCapabilities/asset binding mismatch")
        if fresh_product_profile.contract_fingerprint != prepared_product_profile.contract_fingerprint:
            reasons.append("ProductCapabilities contract changed since preparation")
        try:
            fresh_product_profile.require_order(order_type=BrokerOrderType.LIMIT, time_in_force=TimeInForce.IOC)
            fresh_product_profile.require_margin(uses_margin=False)
            fresh_product_profile.require_opening_short(opening_short=False)
        except Exception as exc:
            reasons.append(f"fresh ProductCapabilities reject cold-start order: {exc}")
        if package.quantity % fresh_asset.min_trade_increment != 0 or package.quantity < fresh_asset.min_order_size:
            reasons.append("prepared quantity violates fresh broker size contract")
        if package.limit_price % fresh_asset.price_increment != 0:
            reasons.append("prepared limit price violates fresh broker price increment")
        if fresh_market.market.symbol != COLD_START_SYMBOL:
            reasons.append("fresh crypto market symbol mismatch")
        if not fresh_flat_account.clean_for_first_canary:
            reasons.append("fresh PAPER account is not flat")
        if fresh_flat_account.account_attestation_fingerprint != fresh_account.fingerprint:
            reasons.append("fresh flat-account evidence/account mismatch")
        if fresh_flat_account.credential_reference != fresh_account.credential_reference:
            reasons.append("fresh flat-account credential mismatch")
        for label, timestamp in (
            ("account", fresh_account.attested_at),
            ("asset", fresh_asset.observed_at),
            ("ProductCapabilities", fresh_product_profile.observed_at),
            ("market", fresh_market.received_at),
            ("flat-account", fresh_flat_account.attested_at),
        ):
            _check_freshness(reasons=reasons, label=label, timestamp=timestamp, now=observed_at)

        current_order = self._orders.get_by_order_id(package.order_id)
        current_intent_hash = "0" * 64
        if current_order is None:
            reasons.append("authoritative bootstrap OMS order is missing")
        else:
            current_intent_hash = intent_fingerprint(current_order.intent)
            if current_intent_hash != package.intent_fingerprint:
                reasons.append("bootstrap OMS intent changed")
            if current_order.risk_decision_id != package.risk_decision_id:
                reasons.append("bootstrap OMS RiskDecision changed")
            if current_order.intent.symbol != COLD_START_SYMBOL:
                reasons.append("bootstrap OMS symbol changed")
            if current_order.intent.quantity != package.quantity or current_order.intent.limit_price != package.limit_price:
                reasons.append("bootstrap OMS economics changed")

        try:
            authority = self._authority.snapshot()
        except Exception as exc:
            raise CryptoColdStartFinalWriteBlocked(("authoritative cold-start core state unavailable",)) from exc
        if authority.kill_switch_active is not True or authority.kill_switch_reason != COLD_START_KILL_REASON:
            reasons.append("cold-start requires exact commissioning kill switch")
        if authority.circuit_active:
            reasons.append("safety circuit blocks cold-start canary")
        if authority.portfolio_version != 1:
            reasons.append("cold-start requires durable Portfolio State v1")
        if authority.portfolio_gross_exposure != _ZERO or authority.portfolio_net_exposure != _ZERO:
            reasons.append("cold-start requires zero authoritative portfolio exposure")
        if authority.portfolio_open_orders != 0:
            reasons.append("cold-start requires zero authoritative open orders")
        if not authority.portfolio_reconciliation_ok or not authority.portfolio_broker_state_known:
            reasons.append("cold-start Portfolio State is not reconciled/broker-known")
        if authority.portfolio_equity != fresh_account.portfolio_value:
            reasons.append("cold-start Portfolio equity differs from fresh PAPER account")
        if not authority.health_schema_present:
            reasons.append("cold-start Health R4 schemas are missing")
        if authority.health_state_rows != 0 or authority.health_bridge_rows != 0:
            reasons.append("cold-start requires Health and bridge rows to remain absent")

        try:
            operator_state = operator_registry.get(context.preparation_hash)
        except Exception as exc:
            raise CryptoColdStartFinalWriteBlocked(("durable operator decision unavailable or corrupt",)) from exc
        if operator_state.decision != operator_decision:
            reasons.append("durable operator decision differs from supplied decision")

        try:
            lifecycle_snapshot = lifecycle.snapshot(package.lifecycle_id)
        except Exception as exc:
            raise CryptoColdStartFinalWriteBlocked(("durable crypto lifecycle unavailable or corrupt",)) from exc
        binding = lifecycle_snapshot.binding
        lifecycle_state = lifecycle_snapshot.state
        if binding.fingerprint != package.lifecycle_binding_hash:
            reasons.append("lifecycle binding hash mismatch")
        if binding.entry_order_fingerprint != package.crypto_order_fingerprint or binding.entry_client_order_id != package.client_order_id:
            reasons.append("lifecycle entry identity mismatch")
        if binding.entry_quantity != package.quantity:
            reasons.append("lifecycle entry quantity mismatch")

        if phase is CryptoColdStartFinalWritePhase.PRE_CONSUME:
            if expected_attempt_id is not None or previous_attestation is not None:
                reasons.append("PRE_CONSUME cannot carry attempt/predecessor")
            if current_order is not None and current_order.status is not OrderStatus.VALIDATED:
                reasons.append("PRE_CONSUME requires bootstrap OMS VALIDATED")
            if operator_state.status is not CryptoOperatorDecisionStatus.ISSUED:
                reasons.append("PRE_CONSUME requires unconsumed ISSUED decision")
            if lifecycle_state.status is not CryptoLifecycleStatus.ENTRY_PREPARED or lifecycle_state.entry_attempt_count != 0:
                reasons.append("PRE_CONSUME requires ENTRY_PREPARED with zero attempts")
            if lifecycle_state.control_hash != package.lifecycle_control_hash or lifecycle_state.event_head_hash != package.lifecycle_event_head_hash:
                reasons.append("PRE_CONSUME lifecycle changed after preparation")
        else:
            if expected_attempt_id != context.attempt_id:
                reasons.append("PRE_IO attempt_id mismatch")
            if previous_attestation is None:
                reasons.append("PRE_IO requires actual PRE_CONSUME attestation")
            if current_order is not None and current_order.status is not OrderStatus.SUBMITTING:
                reasons.append("PRE_IO requires bootstrap OMS SUBMITTING")
            if operator_state.status is not CryptoOperatorDecisionStatus.CONSUMED or operator_state.consumed_attempt_id != context.attempt_id:
                reasons.append("PRE_IO requires decision consumed by exact attempt")
            if lifecycle_state.status is not CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN or lifecycle_state.entry_attempt_count != 1:
                reasons.append("PRE_IO requires durable ENTRY_SUBMISSION_UNKNOWN with one attempt")
            if previous_attestation is not None:
                _validate_predecessor(
                    reasons=reasons,
                    previous=previous_attestation,
                    package=package,
                    operator_decision=operator_decision,
                    authority=authority,
                    prepared_asset=prepared_asset,
                    fresh_account=fresh_account,
                    fresh_asset=fresh_asset,
                    prepared_product_profile=prepared_product_profile,
                    fresh_product_profile=fresh_product_profile,
                    fresh_market=fresh_market,
                    fresh_flat_account=fresh_flat_account,
                    observed_at=observed_at,
                )

        if reasons:
            raise CryptoColdStartFinalWriteBlocked(reasons)
        assert current_order is not None
        payload = {
            "phase": phase.value,
            "bootstrap_scope": COLD_START_SCOPE,
            "bootstrap_kill_reason": COLD_START_KILL_REASON,
            "package_hash": package.package_hash,
            "preparation_hash": context.preparation_hash,
            "operator_decision_hash": operator_decision.decision_hash,
            "operator_status": operator_state.status.value,
            "attempt_id": context.attempt_id,
            "order_id": package.order_id,
            "client_order_id": package.client_order_id,
            "intent_fingerprint": current_intent_hash,
            "risk_decision_id": package.risk_decision_id,
            "authority_state_fingerprint": authority.state_fingerprint,
            "authoritative_safety_state_version": authority.safety_state_version,
            "portfolio_version": authority.portfolio_version,
            "portfolio_snapshot_id": authority.portfolio_snapshot_id,
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
            "previous_attestation_hash": previous_attestation.attestation_hash if previous_attestation else None,
            "observed_at": observed_at.isoformat(),
        }
        return _attestation_from_payload(payload)


def _validate_predecessor(*, reasons, previous, package, operator_decision, authority, prepared_asset, fresh_account, fresh_asset, prepared_product_profile, fresh_product_profile, fresh_market, fresh_flat_account, observed_at) -> None:
    if previous.phase is not CryptoColdStartFinalWritePhase.PRE_CONSUME:
        reasons.append("PRE_IO predecessor must be PRE_CONSUME")
    if previous.package_hash != package.package_hash or previous.preparation_hash != operator_decision.context.preparation_hash:
        reasons.append("PRE_IO predecessor package/preparation mismatch")
    if previous.operator_decision_hash != operator_decision.decision_hash or previous.attempt_id != operator_decision.context.attempt_id:
        reasons.append("PRE_IO predecessor decision/attempt mismatch")
    if previous.order_id != package.order_id or previous.client_order_id != package.client_order_id:
        reasons.append("PRE_IO predecessor order identity mismatch")
    if previous.authority_state_fingerprint != authority.state_fingerprint:
        reasons.append("authoritative cold-start core state changed between PRE_CONSUME and PRE_IO")
    if previous.authoritative_safety_state_version != authority.safety_state_version:
        reasons.append("Safety state changed between PRE_CONSUME and PRE_IO")
    if previous.portfolio_version != authority.portfolio_version or previous.portfolio_snapshot_id != authority.portfolio_snapshot_id:
        reasons.append("Portfolio State changed between PRE_CONSUME and PRE_IO")
    if previous.account_reference != fresh_account.account_reference or previous.credential_reference != fresh_account.credential_reference:
        reasons.append("account/credential changed between PRE_CONSUME and PRE_IO")
    if previous.fresh_account_fingerprint != fresh_account.fingerprint:
        reasons.append("fresh account evidence changed between PRE_CONSUME and PRE_IO")
    if previous.prepared_asset_fingerprint != prepared_asset.fingerprint or previous.fresh_asset_fingerprint != fresh_asset.fingerprint:
        reasons.append("asset evidence changed between PRE_CONSUME and PRE_IO")
    if previous.prepared_product_profile_fingerprint != prepared_product_profile.fingerprint or previous.fresh_product_profile_fingerprint != fresh_product_profile.fingerprint:
        reasons.append("ProductCapabilities evidence changed between PRE_CONSUME and PRE_IO")
    if previous.fresh_market_attestation_fingerprint != fresh_market.fingerprint or previous.flat_account_fingerprint != fresh_flat_account.fingerprint:
        reasons.append("market/flat evidence changed between PRE_CONSUME and PRE_IO")
    if previous.lifecycle_status is not CryptoLifecycleStatus.ENTRY_PREPARED or previous.entry_attempt_count != 0:
        reasons.append("PRE_IO predecessor did not observe pristine ENTRY_PREPARED")
    if previous.observed_at > observed_at:
        reasons.append("PRE_IO observation cannot precede PRE_CONSUME")


def _attestation_from_payload(payload: dict[str, object]) -> CryptoColdStartFinalWriteAttestation:
    raw = dict(payload)
    attestation_hash = sha256(_canonical_json(raw).encode("utf-8")).hexdigest()
    return CryptoColdStartFinalWriteAttestation(
        phase=CryptoColdStartFinalWritePhase(str(raw["phase"])),
        bootstrap_scope=str(raw["bootstrap_scope"]),
        bootstrap_kill_reason=str(raw["bootstrap_kill_reason"]),
        package_hash=str(raw["package_hash"]),
        preparation_hash=str(raw["preparation_hash"]),
        operator_decision_hash=str(raw["operator_decision_hash"]),
        operator_status=CryptoOperatorDecisionStatus(str(raw["operator_status"])),
        attempt_id=str(raw["attempt_id"]),
        order_id=str(raw["order_id"]),
        client_order_id=str(raw["client_order_id"]),
        intent_fingerprint=str(raw["intent_fingerprint"]),
        risk_decision_id=str(raw["risk_decision_id"]),
        authority_state_fingerprint=str(raw["authority_state_fingerprint"]),
        authoritative_safety_state_version=int(raw["authoritative_safety_state_version"]),
        portfolio_version=int(raw["portfolio_version"]),
        portfolio_snapshot_id=str(raw["portfolio_snapshot_id"]),
        account_reference=str(raw["account_reference"]),
        credential_reference=str(raw["credential_reference"]),
        fresh_account_fingerprint=str(raw["fresh_account_fingerprint"]),
        prepared_asset_fingerprint=str(raw["prepared_asset_fingerprint"]),
        fresh_asset_fingerprint=str(raw["fresh_asset_fingerprint"]),
        asset_contract_fingerprint=str(raw["asset_contract_fingerprint"]),
        prepared_product_profile_fingerprint=str(raw["prepared_product_profile_fingerprint"]),
        fresh_product_profile_fingerprint=str(raw["fresh_product_profile_fingerprint"]),
        product_contract_fingerprint=str(raw["product_contract_fingerprint"]),
        fresh_market_attestation_fingerprint=str(raw["fresh_market_attestation_fingerprint"]),
        flat_account_fingerprint=str(raw["flat_account_fingerprint"]),
        lifecycle_binding_hash=str(raw["lifecycle_binding_hash"]),
        lifecycle_control_hash=str(raw["lifecycle_control_hash"]),
        lifecycle_event_head_hash=str(raw["lifecycle_event_head_hash"]),
        lifecycle_status=CryptoLifecycleStatus(str(raw["lifecycle_status"])),
        entry_attempt_count=int(raw["entry_attempt_count"]),
        previous_attestation_hash=(str(raw["previous_attestation_hash"]) if raw["previous_attestation_hash"] is not None else None),
        observed_at=datetime.fromisoformat(str(raw["observed_at"])),
        attestation_hash=attestation_hash,
    )


def _attestation_hash(attestation: CryptoColdStartFinalWriteAttestation) -> str:
    return sha256(_canonical_json(_attestation_payload(attestation)).encode("utf-8")).hexdigest()


def _attestation_payload(attestation: CryptoColdStartFinalWriteAttestation) -> dict[str, object]:
    return {
        "phase": attestation.phase.value,
        "bootstrap_scope": attestation.bootstrap_scope,
        "bootstrap_kill_reason": attestation.bootstrap_kill_reason,
        "package_hash": attestation.package_hash,
        "preparation_hash": attestation.preparation_hash,
        "operator_decision_hash": attestation.operator_decision_hash,
        "operator_status": attestation.operator_status.value,
        "attempt_id": attestation.attempt_id,
        "order_id": attestation.order_id,
        "client_order_id": attestation.client_order_id,
        "intent_fingerprint": attestation.intent_fingerprint,
        "risk_decision_id": attestation.risk_decision_id,
        "authority_state_fingerprint": attestation.authority_state_fingerprint,
        "authoritative_safety_state_version": attestation.authoritative_safety_state_version,
        "portfolio_version": attestation.portfolio_version,
        "portfolio_snapshot_id": attestation.portfolio_snapshot_id,
        "account_reference": attestation.account_reference,
        "credential_reference": attestation.credential_reference,
        "fresh_account_fingerprint": attestation.fresh_account_fingerprint,
        "prepared_asset_fingerprint": attestation.prepared_asset_fingerprint,
        "fresh_asset_fingerprint": attestation.fresh_asset_fingerprint,
        "asset_contract_fingerprint": attestation.asset_contract_fingerprint,
        "prepared_product_profile_fingerprint": attestation.prepared_product_profile_fingerprint,
        "fresh_product_profile_fingerprint": attestation.fresh_product_profile_fingerprint,
        "product_contract_fingerprint": attestation.product_contract_fingerprint,
        "fresh_market_attestation_fingerprint": attestation.fresh_market_attestation_fingerprint,
        "flat_account_fingerprint": attestation.flat_account_fingerprint,
        "lifecycle_binding_hash": attestation.lifecycle_binding_hash,
        "lifecycle_control_hash": attestation.lifecycle_control_hash,
        "lifecycle_event_head_hash": attestation.lifecycle_event_head_hash,
        "lifecycle_status": attestation.lifecycle_status.value,
        "entry_attempt_count": attestation.entry_attempt_count,
        "previous_attestation_hash": attestation.previous_attestation_hash,
        "observed_at": attestation.observed_at.astimezone(timezone.utc).isoformat(),
    }


def _check_freshness(*, reasons: list[str], label: str, timestamp: datetime, now: datetime) -> None:
    _require_aware(timestamp, label)
    value = timestamp.astimezone(timezone.utc)
    if value > now + _FUTURE_TOLERANCE:
        reasons.append(f"fresh {label} evidence is future-dated")
    elif now - value > _FINAL_EVIDENCE_TTL:
        reasons.append(f"fresh {label} evidence exceeds 5-second cold-start TTL")


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "COLD_START_KILL_REASON",
    "COLD_START_MAX_NOTIONAL",
    "COLD_START_MIN_NOTIONAL",
    "COLD_START_SCOPE",
    "COLD_START_SYMBOL",
    "CryptoColdStartAuthoritySnapshot",
    "CryptoColdStartFinalWriteAttestation",
    "CryptoColdStartFinalWriteBlocked",
    "CryptoColdStartFinalWriteError",
    "CryptoColdStartFinalWritePhase",
    "CryptoColdStartPaperFinalWriteGuard",
    "SQLiteCryptoColdStartAuthorityProvider",
]
