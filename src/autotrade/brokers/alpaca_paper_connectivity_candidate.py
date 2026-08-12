from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from hashlib import sha256
import json
from pathlib import Path

from autotrade.connectivity_canary_authority import (
    CONNECTIVITY_CANARY_STRATEGY_ID,
    ConnectivityCanaryAuthority,
    SQLiteConnectivityCanaryAuthorityStore,
)
from autotrade.domain import (
    OrderIntent,
    OrderType,
    PortfolioSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    Side,
    intent_fingerprint,
    market_fingerprint,
    risk_decision_fingerprint,
)
from autotrade.instrument_master import (
    AuthoritativeInstrumentRules,
    InstrumentTradingStatus,
    SQLiteInstrumentMaster,
)
from autotrade.ledger import LedgerEvent
from autotrade.oms import OrderManagementSystem
from autotrade.persistence import (
    SQLiteEventLedger,
    SQLiteOrderStore,
    SQLitePortfolioStore,
    SQLiteRuntime,
    SQLiteSafetyStateStore,
    _portfolio_for_storage,
)
from autotrade.safety import CapitalSafetyKernel, SafetyLimits

from .alpaca_paper_asset_evidence import PaperAssetEvidenceStore
from .alpaca_paper_flat_account_evidence import PaperFlatAccountEvidenceStore
from .alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
)
from .alpaca_paper_market_evidence import PaperMarketEvidenceStore
from .alpaca_paper_operational import (
    PaperOperationalWorkspace,
    _file_sha256,
    _read_json_object,
    _write_json_idempotent,
    account_attestation_payload,
)


CANDIDATE_ARTIFACT = "connectivity_candidate.json"
MAX_ACCOUNT_AGE_SECONDS = 30
MAX_ASSET_AGE_SECONDS = 300
MAX_FLAT_AGE_SECONDS = 30
MAX_MARKET_AGE_SECONDS = 5
MAX_CONNECTIVITY_NOTIONAL = Decimal("10")
MAX_ACCOUNT_FRACTION = Decimal("0.001")
DECISION_TTL_MS = 15_000


class PaperConnectivityCandidateError(RuntimeError):
    pass


class PaperConnectivityCandidateRejected(PaperConnectivityCandidateError):
    pass


class _NoBrokerSurface:
    def submit(self, **_kwargs):
        raise PaperConnectivityCandidateRejected(
            "connectivity candidate builder has no broker submission surface"
        )


@dataclass(frozen=True, slots=True)
class PaperConnectivityCandidate:
    order_id: str
    intent_fingerprint: str
    risk_decision_fingerprint: str
    authority_id: str
    authority_hash: str
    instrument_rules_fingerprint: str
    portfolio_snapshot_id: str
    portfolio_snapshot_hash: str
    limit_price: Decimal
    quantity: Decimal
    effective_notional_cap: Decimal
    core_db_sha256: str
    candidate_hash: str
    artifact_path: Path


class PaperConnectivityCandidateBuilder:
    """Build one non-executable first-canary candidate from durable GET evidence.

    No network, credentials, writer, operator authority, submission state or
    Strategy Health exists on this path. The Portfolio P&L/drawdown zero-point is
    explicitly a connectivity-session baseline after broker evidence proves the
    PAPER account has no positions or open orders; it is never a strategy
    performance claim.
    """

    def __init__(self, workspace: PaperOperationalWorkspace) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace

    def build(self, *, now: datetime) -> PaperConnectivityCandidate:
        _require_aware(now)
        instant = now.astimezone(timezone.utc)
        if self._workspace.core_db_path.exists():
            raise PaperConnectivityCandidateRejected(
                "core.sqlite3 already exists; connectivity candidate requires a fresh preflight workspace"
            )
        if (self._workspace.root / CANDIDATE_ARTIFACT).exists():
            raise PaperConnectivityCandidateRejected(
                "connectivity candidate artifact already exists; use a fresh workspace"
            )

        account = _read_account(self._workspace)
        asset = PaperAssetEvidenceStore(self._workspace).read()
        flat = PaperFlatAccountEvidenceStore(self._workspace).read()
        market_attestation = PaperMarketEvidenceStore(self._workspace).read()
        market = market_attestation.market
        _require_evidence_freshness(
            account=account,
            asset_observed_at=asset.observed_at,
            flat_attested_at=flat.attested_at,
            market_observed_at=market.observed_at,
            now=instant,
        )
        if account.status != "ACTIVE" or account.currency != "USD":
            raise PaperConnectivityCandidateRejected(
                "connectivity candidate requires ACTIVE USD PAPER account"
            )
        if account.source_host != ALPACA_PAPER_TRADING_HOST or account.source_path != ALPACA_PAPER_ACCOUNT_PATH:
            raise PaperConnectivityCandidateRejected("account evidence endpoint is not exact PAPER")
        if asset.account_attestation_fingerprint != account.fingerprint:
            raise PaperConnectivityCandidateRejected("asset evidence/account binding mismatch")
        if flat.account_attestation_fingerprint != account.fingerprint:
            raise PaperConnectivityCandidateRejected("flat evidence/account binding mismatch")
        if not flat.clean_for_first_canary:
            raise PaperConnectivityCandidateRejected(
                "connectivity candidate requires zero positions and zero open orders"
            )
        if market.symbol != asset.symbol:
            raise PaperConnectivityCandidateRejected("asset/market symbol mismatch")
        if account.portfolio_value <= 0 or account.buying_power <= 0:
            raise PaperConnectivityCandidateRejected("PAPER account capital fields must be positive")

        effective_cap = min(
            MAX_CONNECTIVITY_NOTIONAL,
            account.portfolio_value * MAX_ACCOUNT_FRACTION,
            account.buying_power,
        )
        if not effective_cap.is_finite() or effective_cap <= 0:
            raise PaperConnectivityCandidateRejected("connectivity effective notional cap is invalid")
        quantity = Decimal("1")
        limit_price = _ceil_to_increment(market.ask, asset.price_increment)
        notional = quantity * limit_price
        if notional > effective_cap:
            raise PaperConnectivityCandidateRejected(
                f"one-share connectivity candidate {notional} exceeds strict cap {effective_cap}; choose another attested symbol"
            )
        if quantity < asset.min_order_size or quantity % asset.min_trade_increment != 0:
            raise PaperConnectivityCandidateRejected(
                "one-share connectivity candidate violates attested asset quantity constraints"
            )

        evidence_key = {
            "account": account.fingerprint,
            "asset": asset.fingerprint,
            "flat": flat.fingerprint,
            "market": market_attestation.fingerprint,
        }
        evidence_hash = _hash(evidence_key)
        snapshot = PortfolioSnapshot(
            snapshot_id=f"r6-connectivity-baseline:{evidence_hash[:24]}",
            equity=account.portfolio_value,
            gross_exposure=Decimal("0"),
            net_exposure=Decimal("0"),
            daily_pnl=Decimal("0"),
            drawdown=Decimal("0"),
            open_orders=0,
            signed_position_notional_by_symbol={},
            strategy_gross_exposure={},
            strategy_signed_position_notional_by_symbol={},
            reconciliation_ok=True,
            broker_state_known=True,
        )
        _, portfolio_hash = _portfolio_for_storage(snapshot)
        rules = AuthoritativeInstrumentRules(
            venue="ALPACA_PAPER",
            symbol=asset.symbol,
            base_currency=asset.symbol,
            quote_currency="USD",
            version=1,
            price_tick=asset.price_increment,
            quantity_step=Decimal("1"),
            min_quantity=Decimal("1"),
            max_quantity=Decimal("1"),
            min_notional=None,
            max_notional=effective_cap,
            trading_status=InstrumentTradingStatus.TRADING,
            source="ALPACA_PAPER_ASSET_PLUS_R6_WHOLE_SHARE_POLICY",
            source_version=f"asset:{asset.fingerprint}:whole-share-v1",
            source_payload_sha256=asset.response_sha256,
            observed_at=asset.observed_at,
            valid_until=instant + timedelta(minutes=5),
        )
        rules.validate_candidate(quantity=quantity, price=limit_price)

        runtime = SQLiteRuntime(self._workspace.core_db_path)
        ledger = SQLiteEventLedger(runtime)
        portfolio_store = SQLitePortfolioStore(runtime)
        versioned_portfolio = portfolio_store.initialize(snapshot, now=instant)
        if versioned_portfolio.version != 1 or versioned_portfolio.snapshot != snapshot:
            raise PaperConnectivityCandidateRejected("connectivity Portfolio baseline is not a fresh v1 snapshot")
        SQLiteInstrumentMaster(runtime).publish(rules, now=instant)
        ledger.append(
            LedgerEvent(
                event_id=f"connectivity-baseline:{evidence_hash}",
                event_type="CONNECTIVITY_PORTFOLIO_BASELINE_INITIALIZED",
                occurred_at=instant,
                payload={
                    "snapshot_id": snapshot.snapshot_id,
                    "portfolio_snapshot_hash": portfolio_hash,
                    "account_attestation_fingerprint": account.fingerprint,
                    "baseline_flat_account_fingerprint": flat.fingerprint,
                    "scope": "CONNECTIVITY_SESSION_ONLY",
                    "strategy_performance_claim": "false",
                    "external_post_authorized": "false",
                },
            )
        )

        intent_seed = _hash(
            {
                "evidence_hash": evidence_hash,
                "instrument_rules_fingerprint": rules.fingerprint,
                "limit_price": str(limit_price),
                "quantity": str(quantity),
                "strategy_id": CONNECTIVITY_CANARY_STRATEGY_ID,
            }
        )
        intent = OrderIntent(
            intent_id=f"r6-connectivity:{intent_seed[:32]}",
            idempotency_key=f"r6-connectivity:{intent_seed}",
            strategy_id=CONNECTIVITY_CANARY_STRATEGY_ID,
            symbol=asset.symbol,
            side=Side.BUY,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            created_at=instant,
            limit_price=limit_price,
        )
        safety_store = SQLiteSafetyStateStore(runtime)
        limits = SafetyLimits(
            limits_version="r6-connectivity-canary-v1",
            allowed_symbols=frozenset({asset.symbol}),
            allowed_order_types=frozenset({OrderType.LIMIT}),
            max_order_notional=effective_cap,
            max_position_notional=effective_cap,
            max_strategy_gross_exposure=effective_cap,
            max_portfolio_gross_exposure=effective_cap,
            max_net_exposure=effective_cap,
            max_leverage=MAX_ACCOUNT_FRACTION,
            max_daily_loss=Decimal("0.01"),
            max_drawdown=Decimal("0.0001"),
            max_open_orders=1,
            stale_market_data_ms=MAX_MARKET_AGE_SECONDS * 1000,
            price_deviation_bps=Decimal("100"),
            decision_ttl_ms=DECISION_TTL_MS,
        )
        decision = CapitalSafetyKernel(
            limits,
            ledger,
            state_store=safety_store,
        ).evaluate(
            intent=intent,
            market=market,
            portfolio=snapshot,
            now=instant,
        )
        if decision.status is not RiskDecisionStatus.APPROVED:
            raise PaperConnectivityCandidateRejected(
                f"Capital Safety rejected connectivity candidate: {decision.reason_code}: {decision.reason_detail}"
            )
        if decision.approved_notional != notional:
            raise PaperConnectivityCandidateRejected("Capital Safety approved notional mismatch")

        oms = OrderManagementSystem(
            broker=_NoBrokerSurface(),
            ledger=ledger,
            order_store=SQLiteOrderStore(runtime),
            safety_state_store=safety_store,
        )
        order = oms.validate_for_external_submission(
            intent=intent,
            decision=decision,
            market=market,
            now=instant,
        )
        authority = ConnectivityCanaryAuthority.issue(
            order_id=order.order_id,
            intent_fingerprint=intent_fingerprint(intent),
            risk_decision_id=decision.decision_id,
            risk_decision_fingerprint=risk_decision_fingerprint(decision),
            market_fingerprint=market_fingerprint(market),
            safety_state_version=decision.safety_state_version,
            portfolio_version=versioned_portfolio.version,
            portfolio_snapshot_id=snapshot.snapshot_id,
            portfolio_snapshot_hash=portfolio_hash,
            account_attestation_fingerprint=account.fingerprint,
            asset_attestation_fingerprint=asset.fingerprint,
            baseline_flat_account_fingerprint=flat.fingerprint,
            market_evidence_fingerprint=market_attestation.fingerprint,
            instrument_rules_fingerprint=rules.fingerprint,
            max_notional=effective_cap,
            issued_at=instant,
            expires_at=decision.valid_until,
        )
        SQLiteConnectivityCanaryAuthorityStore(runtime).issue(authority)
        if not SQLiteEventLedger(runtime).verify_integrity():
            raise PaperConnectivityCandidateRejected("core Event Ledger integrity verification failed")
        _checkpoint_core(runtime)
        core_hash = _file_sha256(self._workspace.core_db_path)

        artifact_path = self._workspace.root / CANDIDATE_ARTIFACT
        payload = {
            "schema_version": 1,
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "strategy_id": CONNECTIVITY_CANARY_STRATEGY_ID,
            "order_id": order.order_id,
            "order_status": order.status.value,
            "intent_fingerprint": intent_fingerprint(intent),
            "risk_decision": _risk_decision_payload(decision),
            "risk_decision_fingerprint": risk_decision_fingerprint(decision),
            "instrument_rules": rules.to_payload(),
            "authority": authority.payload(),
            "portfolio_baseline": {
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_hash": portfolio_hash,
                "version": versioned_portfolio.version,
                "equity": str(snapshot.equity),
                "gross_exposure": "0",
                "net_exposure": "0",
                "open_orders": 0,
                "performance_scope": "CONNECTIVITY_SESSION_ONLY",
                "strategy_performance_claim": False,
            },
            "source_evidence": evidence_key,
            "limit_price": str(limit_price),
            "quantity": str(quantity),
            "effective_notional_cap": str(effective_cap),
            "core_db_sha256": core_hash,
            "builder_network_used": False,
            "credentials_used": False,
            "strategy_health_required": False,
            "strategy_health_created": False,
            "strategy_trading_authorized": False,
            "external_post_authorized": False,
            "capital_authority": "NONE",
            "profitability_claim": False,
            "live_trading": "BLOCKED",
            "next_action": "CONNECTIVITY_PREPARATION_BRIDGE_REQUIRED",
        }
        candidate_hash = _hash(payload)
        payload["candidate_hash"] = candidate_hash
        _write_json_idempotent(artifact_path, payload)
        return PaperConnectivityCandidate(
            order_id=order.order_id,
            intent_fingerprint=intent_fingerprint(intent),
            risk_decision_fingerprint=risk_decision_fingerprint(decision),
            authority_id=authority.authority_id,
            authority_hash=authority.authority_hash,
            instrument_rules_fingerprint=rules.fingerprint,
            portfolio_snapshot_id=snapshot.snapshot_id,
            portfolio_snapshot_hash=portfolio_hash,
            limit_price=limit_price,
            quantity=quantity,
            effective_notional_cap=effective_cap,
            core_db_sha256=core_hash,
            candidate_hash=candidate_hash,
            artifact_path=artifact_path,
        )


def _checkpoint_core(runtime: SQLiteRuntime) -> None:
    conn = runtime.connect()
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if row is not None and int(row[0]) != 0:
            raise PaperConnectivityCandidateRejected("cannot checkpoint connectivity core database")
    finally:
        conn.close()


def _read_account(workspace: PaperOperationalWorkspace) -> AlpacaPaperAccountAttestation:
    raw = _read_json_object(workspace.account_attestation_path)
    try:
        account = AlpacaPaperAccountAttestation(
            account_id=_string(raw, "account_id"),
            account_reference=_string(raw, "account_reference"),
            credential_reference=_string(raw, "credential_reference"),
            status=_string(raw, "status"),
            currency=_string(raw, "currency"),
            buying_power=_decimal(raw, "buying_power"),
            portfolio_value=_decimal(raw, "portfolio_value"),
            shorting_enabled=_boolean(raw, "shorting_enabled"),
            attested_at=_timestamp(raw, "attested_at"),
            request_id=_string(raw, "request_id"),
            source_host=_string(raw, "source_host"),
            source_path=_string(raw, "source_path"),
        )
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise PaperConnectivityCandidateRejected("account evidence is invalid") from exc
    if account_attestation_payload(account) != raw:
        raise PaperConnectivityCandidateRejected("account evidence is non-canonical")
    return account


def _require_evidence_freshness(
    *,
    account: AlpacaPaperAccountAttestation,
    asset_observed_at: datetime,
    flat_attested_at: datetime,
    market_observed_at: datetime,
    now: datetime,
) -> None:
    for label, value, maximum in (
        ("account", account.attested_at, MAX_ACCOUNT_AGE_SECONDS),
        ("asset", asset_observed_at, MAX_ASSET_AGE_SECONDS),
        ("flat-account", flat_attested_at, MAX_FLAT_AGE_SECONDS),
        ("market", market_observed_at, MAX_MARKET_AGE_SECONDS),
    ):
        _require_aware(value)
        age = (now - value.astimezone(timezone.utc)).total_seconds()
        if age < 0:
            raise PaperConnectivityCandidateRejected(f"{label} evidence is from the future")
        if age > maximum:
            raise PaperConnectivityCandidateRejected(
                f"{label} evidence is stale for connectivity candidate; repeat GET-only preflight"
            )


def _ceil_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0 or not increment.is_finite() or increment <= 0:
        raise PaperConnectivityCandidateRejected("price/increment must be finite and positive")
    units = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return units * increment


def _risk_decision_payload(decision: RiskDecision) -> dict[str, object]:
    return {
        "decision_id": decision.decision_id,
        "intent_id": decision.intent_id,
        "status": decision.status.value,
        "reason_code": decision.reason_code,
        "reason_detail": decision.reason_detail,
        "evaluated_at": decision.evaluated_at.isoformat(),
        "valid_until": decision.valid_until.isoformat(),
        "limits_version": decision.limits_version,
        "intent_fingerprint": decision.intent_fingerprint,
        "market_fingerprint": decision.market_fingerprint,
        "approved_notional": str(decision.approved_notional) if decision.approved_notional is not None else None,
        "risk_reducing": decision.risk_reducing,
        "safety_state_version": decision.safety_state_version,
    }


def _string(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty string")
    return value


def _decimal(raw: dict[str, object], key: str) -> Decimal:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValueError(f"{key} must be finite")
    return parsed


def _boolean(raw: dict[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def _timestamp(raw: dict[str, object], key: str) -> datetime:
    value = _string(raw, key)
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed)
    if parsed.isoformat() != value:
        raise ValueError(f"{key} must be canonical timestamp")
    return parsed


def _require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


def _hash(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "CANDIDATE_ARTIFACT",
    "PaperConnectivityCandidate",
    "PaperConnectivityCandidateBuilder",
    "PaperConnectivityCandidateError",
    "PaperConnectivityCandidateRejected",
]
