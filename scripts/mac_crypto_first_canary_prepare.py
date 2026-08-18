from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_crypto_asset import (
    CRYPTO_PAIR,
    AlpacaPaperCryptoAssetAttestation,
    AlpacaPaperCryptoAssetGateway,
)
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import (
    CryptoCanaryPreparationResult,
    CryptoPaperCanaryCoordinator,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_final_guard import (
    COLD_START_KILL_REASON,
    COLD_START_SCOPE,
    SQLiteCryptoColdStartAuthorityProvider,
)
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    ATTEMPT_ID_RE,
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import SQLiteCryptoPaperLifecycle
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    AlpacaPaperCryptoMarketAttestation,
    AlpacaPaperCryptoMarketDataConfig,
    AlpacaPaperCryptoMarketDataGateway,
)
from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    crypto_operator_confirmation_challenge,
)
from autotrade.brokers.alpaca_paper_flat_account import (
    AlpacaPaperFlatAccountGateway,
    PaperFlatAccountAttestation,
)
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.domain import OrderIntent, OrderType, RiskDecision, RiskDecisionStatus, Side
from autotrade.instrument_master import (
    AuthoritativeInstrumentRules,
    InstrumentTradingStatus,
    SQLiteInstrumentMaster,
)
from autotrade.oms import OrderManagementSystem
from autotrade.persistence import (
    SQLiteEventLedger,
    SQLiteOrderStore,
    SQLitePortfolioStore,
    SQLiteRuntime,
    SQLiteSafetyStateStore,
)
from autotrade.product_profile import BrokerOrderType, ProductCapabilities, TimeInForce
from autotrade.safety import CapitalSafetyKernel, SafetyLimits


WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
EXPECTED_SYMBOL = CRYPTO_PAIR
STRATEGY_ID = "R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION"
TARGET_NOTIONAL = Decimal("2")
MIN_NOTIONAL = Decimal("1")
MAX_NOTIONAL = Decimal("5")
MAX_ACCOUNT_FRACTION = Decimal("0.001")
DECISION_TTL_MS = 30_000
CERTIFIED_TRACKS = ("R0", "R1", "R2", "R3", "R4", "R5")
_ZERO = Decimal("0")


class CryptoFirstCanaryPreparationError(RuntimeError):
    pass


class _NoBrokerSurface:
    def submit(self, **_kwargs):
        raise CryptoFirstCanaryPreparationError(
            "first-canary preparation has no broker submission surface"
        )


@dataclass(frozen=True, slots=True)
class FirstCanaryPreparedSession:
    workspace: PaperOperationalWorkspace
    attempt: FirstCanaryAttemptWorkspace
    core_runtime: SQLiteRuntime
    attempt_runtime: SQLiteRuntime
    credentials: AlpacaPaperCredentials
    account: AlpacaPaperAccountAttestation
    asset: AlpacaPaperCryptoAssetAttestation
    product_profile: ProductCapabilities
    flat_account: PaperFlatAccountAttestation
    market_attestation: AlpacaPaperCryptoMarketAttestation
    risk_decision: RiskDecision
    preparation: CryptoCanaryPreparationResult
    operator_context: CryptoOperatorDecisionContext
    authority_state_fingerprint: str
    preparation_document: dict[str, object]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _credentials() -> AlpacaPaperCredentials:
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if not key or not secret:
        raise CryptoFirstCanaryPreparationError(
            "PAPER Key + Secret are required for first-canary preparation"
        )
    return AlpacaPaperCredentials(key_id=key, secret_key=secret)


def _account_anchor(workspace: PaperOperationalWorkspace) -> str:
    path = workspace.account_attestation_path
    if path.is_symlink() or not path.is_file():
        raise CryptoFirstCanaryPreparationError(
            "verified PAPER account is missing; verify PAPER account in the Control Center first"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoFirstCanaryPreparationError(
            "verified PAPER account evidence is unreadable"
        ) from exc
    if not isinstance(payload, dict) or payload.get("environment") != "PAPER":
        raise CryptoFirstCanaryPreparationError("workspace account evidence is not PAPER")
    if payload.get("credentials_persisted") is not False:
        raise CryptoFirstCanaryPreparationError(
            "workspace account evidence violates credential policy"
        )
    account_id = payload.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise CryptoFirstCanaryPreparationError("workspace PAPER account ID is missing")
    return account_id.strip()


def _ceil(value: Decimal, increment: Decimal) -> Decimal:
    units = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return units * increment


def _quantity(
    *,
    min_order_size: Decimal,
    min_trade_increment: Decimal,
    limit_price: Decimal,
) -> Decimal:
    if min_order_size <= 0 or min_trade_increment <= 0 or limit_price <= 0:
        raise CryptoFirstCanaryPreparationError("broker sizing inputs must be positive")
    broker_minimum = _ceil(min_order_size, min_trade_increment)
    target = _ceil(TARGET_NOTIONAL / limit_price, min_trade_increment)
    quantity = max(broker_minimum, target)
    if quantity * limit_price < MIN_NOTIONAL:
        quantity = max(
            broker_minimum,
            _ceil(MIN_NOTIONAL / limit_price, min_trade_increment),
        )
    return quantity


def _require_cold_start_authority(snapshot) -> None:
    expected = {
        "kill_switch_active": True,
        "kill_switch_reason": COLD_START_KILL_REASON,
        "circuit_active": False,
        "portfolio_version": 1,
        "portfolio_gross_exposure": _ZERO,
        "portfolio_net_exposure": _ZERO,
        "portfolio_open_orders": 0,
        "portfolio_reconciliation_ok": True,
        "portfolio_broker_state_known": True,
        "health_schema_present": True,
        "health_state_rows": 0,
        "health_bridge_rows": 0,
    }
    mismatches = [
        key for key, value in expected.items() if getattr(snapshot, key) != value
    ]
    if mismatches:
        raise CryptoFirstCanaryPreparationError(
            "authoritative cold-start Safety/Portfolio/Health state is not exact: "
            + ", ".join(mismatches)
        )


def _require_broker_binding(
    *,
    authority,
    account: AlpacaPaperAccountAttestation,
    asset: AlpacaPaperCryptoAssetAttestation,
    flat: PaperFlatAccountAttestation,
    market: AlpacaPaperCryptoMarketAttestation,
    product_profile: ProductCapabilities,
) -> None:
    if account.status != "ACTIVE" or account.currency != "USD":
        raise CryptoFirstCanaryPreparationError("first canary requires active USD PAPER account")
    expected_snapshot_id = f"r6-crypto-paper-cold-start:{account.account_reference[:20]}"
    if authority.portfolio_snapshot_id != expected_snapshot_id:
        raise CryptoFirstCanaryPreparationError(
            "durable Portfolio v1 is not bound to the fresh PAPER account"
        )
    if authority.portfolio_equity != account.portfolio_value:
        raise CryptoFirstCanaryPreparationError(
            "durable Portfolio equity differs from fresh PAPER account"
        )
    if asset.account_attestation_fingerprint != account.fingerprint:
        raise CryptoFirstCanaryPreparationError("asset evidence is not bound to fresh account")
    if asset.credential_reference != account.credential_reference:
        raise CryptoFirstCanaryPreparationError("asset credential provenance drifted")
    if flat.account_attestation_fingerprint != account.fingerprint:
        raise CryptoFirstCanaryPreparationError("flat-account evidence is not bound to fresh account")
    if flat.credential_reference != account.credential_reference:
        raise CryptoFirstCanaryPreparationError("flat-account credential provenance drifted")
    if not flat.clean_for_first_canary:
        raise CryptoFirstCanaryPreparationError(
            f"PAPER account is not flat; positions={flat.position_count}, open_orders={flat.open_order_count}"
        )
    if asset.symbol != EXPECTED_SYMBOL or market.market.symbol != EXPECTED_SYMBOL:
        raise CryptoFirstCanaryPreparationError("asset/market identity is not exact BTC/USD")
    if product_profile.source_fingerprint != asset.fingerprint:
        raise CryptoFirstCanaryPreparationError("ProductCapabilities is not bound to fresh asset")
    product_profile.require_order(
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.IOC,
    )
    product_profile.require_order(
        order_type=BrokerOrderType.STOP_LIMIT,
        time_in_force=TimeInForce.GTC,
    )
    product_profile.require_margin(uses_margin=False)
    product_profile.require_opening_short(opening_short=False)


def _attempt_risk_decision(
    *,
    runtime: SQLiteRuntime,
    account: AlpacaPaperAccountAttestation,
    asset: AlpacaPaperCryptoAssetAttestation,
    product_profile: ProductCapabilities,
    market_attestation: AlpacaPaperCryptoMarketAttestation,
    portfolio,
    now: datetime,
    attempt_id: str,
) -> tuple[OrderIntent, RiskDecision, Decimal]:
    effective_cap = min(
        MAX_NOTIONAL,
        account.portfolio_value * MAX_ACCOUNT_FRACTION,
        account.buying_power,
    )
    if effective_cap < MIN_NOTIONAL:
        raise CryptoFirstCanaryPreparationError(
            "PAPER account conservative first-canary capacity is below USD 1"
        )
    limit_price = _ceil(market_attestation.market.ask, asset.price_increment)
    quantity = _quantity(
        min_order_size=asset.min_order_size,
        min_trade_increment=asset.min_trade_increment,
        limit_price=limit_price,
    )
    notional = quantity * limit_price
    if not MIN_NOTIONAL <= notional <= MAX_NOTIONAL or notional > effective_cap:
        raise CryptoFirstCanaryPreparationError(
            f"prepared BTC/USD notional {notional} is outside exact USD 1-5/cap bounds"
        )

    base_currency, quote_currency = EXPECTED_SYMBOL.split("/", 1)
    SQLitePortfolioStore(runtime).initialize(portfolio, now=now)
    rules = AuthoritativeInstrumentRules(
        venue=product_profile.venue,
        symbol=EXPECTED_SYMBOL,
        base_currency=base_currency,
        quote_currency=quote_currency,
        version=1,
        price_tick=asset.price_increment,
        quantity_step=asset.min_trade_increment,
        min_quantity=asset.min_order_size,
        max_quantity=quantity,
        min_notional=None,
        max_notional=effective_cap,
        trading_status=InstrumentTradingStatus.TRADING,
        source="ALPACA_PAPER_CRYPTO_ASSET",
        source_version=(
            f"asset:{asset.fingerprint}:profile:{product_profile.fingerprint}:first-canary-execution-v1"
        ),
        source_payload_sha256=asset.response_sha256,
        observed_at=asset.observed_at,
        valid_until=now + timedelta(minutes=5),
    )
    rules.validate_candidate(quantity=quantity, price=limit_price)
    SQLiteInstrumentMaster(runtime).publish(rules, now=now)
    intent = OrderIntent(
        intent_id=f"crypto-paper-first-canary:{attempt_id}",
        idempotency_key=(
            f"crypto-paper-first-canary:{attempt_id}:{product_profile.fingerprint}:"
            f"{market_attestation.fingerprint}"
        ),
        strategy_id=STRATEGY_ID,
        symbol=EXPECTED_SYMBOL,
        side=Side.BUY,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        created_at=now,
        limit_price=limit_price,
    )
    ledger = SQLiteEventLedger(runtime)
    local_safety = SQLiteSafetyStateStore(runtime)
    limits = SafetyLimits(
        limits_version="r6-crypto-paper-first-canary-execution-v1",
        allowed_symbols=frozenset({EXPECTED_SYMBOL}),
        allowed_order_types=frozenset({OrderType.LIMIT}),
        max_order_notional=effective_cap,
        max_position_notional=effective_cap,
        max_strategy_gross_exposure=effective_cap,
        max_portfolio_gross_exposure=effective_cap,
        max_net_exposure=effective_cap,
        max_leverage=Decimal("1"),
        max_daily_loss=Decimal("1"),
        max_drawdown=Decimal("0.01"),
        max_open_orders=1,
        stale_market_data_ms=60_000,
        price_deviation_bps=Decimal("100"),
        decision_ttl_ms=DECISION_TTL_MS,
    )
    decision = CapitalSafetyKernel(
        limits,
        ledger,
        state_store=local_safety,
    ).evaluate(
        intent=intent,
        market=market_attestation.market,
        portfolio=portfolio,
        now=now,
    )
    if decision.status is not RiskDecisionStatus.APPROVED:
        raise CryptoFirstCanaryPreparationError(
            f"Capital Safety rejected first canary: {decision.reason_code}: {decision.reason_detail}"
        )
    if decision.approved_notional is None or decision.approved_notional > MAX_NOTIONAL:
        raise CryptoFirstCanaryPreparationError("Safety approval exceeds exact USD 5 hard cap")
    return intent, decision, effective_cap


def prepare_from_evidence(
    *,
    workspace_path: Path,
    attempt_id: str,
    credentials: AlpacaPaperCredentials,
    account: AlpacaPaperAccountAttestation,
    asset: AlpacaPaperCryptoAssetAttestation,
    flat_account: PaperFlatAccountAttestation,
    market_attestation: AlpacaPaperCryptoMarketAttestation,
    now: datetime,
) -> FirstCanaryPreparedSession:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise CryptoFirstCanaryPreparationError(
            "first-canary preparation refuses broker-write enabled environment"
        )
    instant = _aware(now)
    if not ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise CryptoFirstCanaryPreparationError("execution attempt_id is invalid")
    raw = workspace_path.expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise CryptoFirstCanaryPreparationError("existing non-symlink workspace is required")
    workspace = PaperOperationalWorkspace(root=raw.resolve())
    if workspace.core_db_path.is_symlink() or not workspace.core_db_path.is_file():
        raise CryptoFirstCanaryPreparationError("commissioned core.sqlite3 is required")
    core_runtime = SQLiteRuntime(workspace.core_db_path)
    authority_provider = SQLiteCryptoColdStartAuthorityProvider(core_runtime)
    authority_before = authority_provider.snapshot()
    _require_cold_start_authority(authority_before)

    product_profile = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )
    _require_broker_binding(
        authority=authority_before,
        account=account,
        asset=asset,
        flat=flat_account,
        market=market_attestation,
        product_profile=product_profile,
    )
    if credentials.credential_reference != account.credential_reference:
        raise CryptoFirstCanaryPreparationError(
            "effective PAPER credential reference differs from fresh account evidence"
        )

    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace.root,
        attempt_id=attempt_id,
    )
    attempt.assert_unexecuted()
    if attempt.preparation_path.exists() or attempt.approval_receipt_path.exists() or attempt.database_path.exists():
        raise CryptoFirstCanaryPreparationError(
            "execution attempt identity already exists; prepare a new attempt instead of replaying"
        )
    attempt_runtime = SQLiteRuntime(attempt.database_path)
    portfolio = SQLitePortfolioStore(core_runtime).get().snapshot
    intent, decision, effective_cap = _attempt_risk_decision(
        runtime=attempt_runtime,
        account=account,
        asset=asset,
        product_profile=product_profile,
        market_attestation=market_attestation,
        portfolio=portfolio,
        now=instant,
        attempt_id=attempt_id,
    )
    ledger = SQLiteEventLedger(attempt_runtime)
    oms = OrderManagementSystem(
        broker=_NoBrokerSurface(),
        ledger=ledger,
        order_store=SQLiteOrderStore(attempt_runtime),
        safety_state_store=SQLiteSafetyStateStore(attempt_runtime),
    )
    lifecycle = SQLiteCryptoPaperLifecycle(attempt_runtime)
    prepared = CryptoPaperCanaryCoordinator(oms=oms).prepare_entry(
        intent=intent,
        decision=decision,
        market_attestation=market_attestation,
        account_attestation=account,
        asset_attestation=asset,
        product_profile=product_profile,
        lifecycle=lifecycle,
        now=instant,
        certified_tracks=CERTIFIED_TRACKS,
        reconciliation_clean=True,
        unresolved_unknown_orders=0,
        relevant_open_orders=flat_account.open_order_count,
        confirmed_pair_position_quantity=_ZERO,
    )
    if prepared.package.notional > MAX_NOTIONAL or prepared.package.notional > effective_cap:
        raise CryptoFirstCanaryPreparationError("coordinator package exceeds exact first-canary cap")
    context = CryptoOperatorDecisionContext.from_prepared_package(
        prepared.package,
        attempt_id=attempt_id,
    )
    challenge = crypto_operator_confirmation_challenge(context)

    authority_after = authority_provider.snapshot()
    _require_cold_start_authority(authority_after)
    if authority_after.state_fingerprint != authority_before.state_fingerprint:
        raise CryptoFirstCanaryPreparationError(
            "authoritative core state changed during first-canary preparation"
        )
    if not ledger.verify_integrity():
        raise CryptoFirstCanaryPreparationError("attempt Event Ledger integrity failed")

    document: dict[str, object] = {
        "schema_version": 1,
        "preparation_type": "R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION",
        "status": "CRYPTO_PAPER_FIRST_CANARY_EXECUTION_PREPARED_NO_POST",
        "environment": "PAPER",
        "symbol": EXPECTED_SYMBOL,
        "scope": COLD_START_SCOPE,
        "attempt_id": attempt_id,
        "prepared_at": instant.isoformat(),
        "execution_deadline": prepared.package.execution_deadline.isoformat(),
        "broker_reads": 6,
        "target_notional": str(TARGET_NOTIONAL),
        "minimum_notional": str(MIN_NOTIONAL),
        "hard_max_notional": str(MAX_NOTIONAL),
        "effective_notional_cap": str(effective_cap),
        "prepared_notional": str(prepared.package.notional),
        "prepared_quantity": str(prepared.package.quantity),
        "prepared_limit_price": str(prepared.package.limit_price),
        "account_reference": account.account_reference,
        "credential_reference": account.credential_reference,
        "prepared_account_fingerprint": account.fingerprint,
        "prepared_asset_fingerprint": asset.fingerprint,
        "prepared_product_profile_fingerprint": product_profile.fingerprint,
        "prepared_market_attestation_fingerprint": market_attestation.fingerprint,
        "prepared_flat_account_fingerprint": flat_account.fingerprint,
        "position_count": flat_account.position_count,
        "open_order_count": flat_account.open_order_count,
        "authority_state_fingerprint": authority_before.state_fingerprint,
        "authoritative_safety_state_version": authority_before.safety_state_version,
        "portfolio_version": authority_before.portfolio_version,
        "portfolio_snapshot_id": authority_before.portfolio_snapshot_id,
        "health_state_rows": authority_before.health_state_rows,
        "health_bridge_rows": authority_before.health_bridge_rows,
        "kill_switch_active": authority_before.kill_switch_active,
        "kill_switch_reason": authority_before.kill_switch_reason,
        "circuit_active": authority_before.circuit_active,
        "risk_decision_id": decision.decision_id,
        "risk_decision_status": decision.status.value,
        "risk_decision_valid_until": decision.valid_until.isoformat(),
        "prepared_package": prepared.package.canonical_payload(),
        "broker_order": {
            "role": prepared.broker_order.role.value,
            "product_profile_fingerprint": prepared.broker_order.product_profile_fingerprint,
            "asset_attestation_fingerprint": prepared.broker_order.asset_attestation_fingerprint,
            "fingerprint": prepared.broker_order.fingerprint,
            "payload_hash": prepared.broker_order.payload_hash,
            "payload": prepared.broker_order.to_payload(),
        },
        "operator_context": context.to_dict(),
        "operator_challenge": challenge,
        "operator_decision_recorded": False,
        "operator_decision_consumed": False,
        "final_guard_pre_consume_authorized": False,
        "oms_submitting": False,
        "lifecycle_unknown": False,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "credentials_persisted": False,
        "uat_only": False,
        "reusable_for_uat": False,
        "live_trading": "BLOCKED",
        "profitability_claim": False,
        "ambiguity_policy": "UNKNOWN_BEFORE_IO_RECONCILE_ONLY_NO_BLIND_RETRY",
        "next_action": "TYPE_EXACT_NEW_CHALLENGE_THEN_PRE_CONSUME_OMS_PRE_IO",
    }
    document["preparation_hash"] = attempt.document_hash(
        document,
        hash_key="preparation_hash",
    )
    if context.preparation_hash != context.to_dict().get("preparation_hash"):
        raise CryptoFirstCanaryPreparationError("operator context preparation hash is inconsistent")
    attempt.write_once(path=attempt.preparation_path, document=document)

    return FirstCanaryPreparedSession(
        workspace=workspace,
        attempt=attempt,
        core_runtime=core_runtime,
        attempt_runtime=attempt_runtime,
        credentials=credentials,
        account=account,
        asset=asset,
        product_profile=product_profile,
        flat_account=flat_account,
        market_attestation=market_attestation,
        risk_decision=decision,
        preparation=prepared,
        operator_context=context,
        authority_state_fingerprint=authority_before.state_fingerprint,
        preparation_document=document,
    )


def prepare_first_canary(
    *,
    workspace_path: Path,
    attempt_id: str,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    account_gateway=None,
    asset_gateway=None,
    flat_gateway=None,
    market_gateway=None,
) -> FirstCanaryPreparedSession:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise CryptoFirstCanaryPreparationError(
            "first-canary preparation refuses broker-write enabled environment"
        )
    instant = _aware(now)
    raw = workspace_path.expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise CryptoFirstCanaryPreparationError("existing non-symlink workspace is required")
    workspace = PaperOperationalWorkspace(root=raw.resolve())
    expected_account_id = _account_anchor(workspace)
    config = AlpacaPaperGatewayConfig(enabled=True)
    account_reader = account_gateway or AlpacaPaperAccountGateway(config=config)
    account = account_reader.attest_account(
        credentials=credentials,
        expected_account_id=expected_account_id,
        now=instant,
    )
    asset_reader = asset_gateway or AlpacaPaperCryptoAssetGateway(config=config)
    asset = asset_reader.attest_asset(
        credentials=credentials,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=account.credential_reference,
        now=instant,
        symbol=EXPECTED_SYMBOL,
    )
    flat_reader = flat_gateway or AlpacaPaperFlatAccountGateway(config=config)
    flat = flat_reader.attest_flatness(
        credentials=credentials,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=account.credential_reference,
        now=instant,
    )
    market_reader = market_gateway or AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True)
    )
    market = market_reader.attest_snapshot(
        credentials=credentials,
        now=instant,
        symbol=EXPECTED_SYMBOL,
    )
    return prepare_from_evidence(
        workspace_path=workspace.root,
        attempt_id=attempt_id,
        credentials=credentials,
        account=account,
        asset=asset,
        flat_account=flat,
        market_attestation=market,
        now=instant,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare one fresh executable BTC/USD PAPER canary attempt using authoritative cold-start core state "
            "and six fresh broker GETs. This phase creates no approval, consumes nothing and performs no POST."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--allow-paper-crypto-read", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_crypto_read:
        raise SystemExit("first-canary preparation requires explicit --allow-paper-crypto-read")
    try:
        session = prepare_first_canary(
            workspace_path=args.workspace,
            attempt_id=str(args.attempt_id),
            credentials=_credentials(),
            now=datetime.now(timezone.utc),
        )
        result = session.preparation_document
    except Exception as exc:
        result = {
            "status": "CRYPTO_PAPER_FIRST_CANARY_EXECUTION_PREPARATION_BLOCKED",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "broker_write_performed": False,
            "external_post_authorized": False,
            "operator_decision_recorded": False,
            "operator_decision_consumed": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
