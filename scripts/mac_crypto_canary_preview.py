from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from autotrade.brokers.alpaca_paper_crypto_asset import (
    CRYPTO_PAIR,
    AlpacaPaperCryptoAssetGateway,
    normalize_crypto_pair,
)
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import CryptoPaperCanaryCoordinator
from autotrade.brokers.alpaca_paper_crypto_lifecycle import SQLiteCryptoPaperLifecycle
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    AlpacaPaperCryptoMarketDataConfig,
    AlpacaPaperCryptoMarketDataGateway,
)
from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    crypto_operator_confirmation_challenge,
)
from autotrade.brokers.alpaca_paper_crypto_order import (
    CryptoOrderRole,
    deterministic_crypto_client_order_id,
)
from autotrade.brokers.alpaca_paper_flat_account import AlpacaPaperFlatAccountGateway
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.domain import OrderIntent, OrderType, PortfolioSnapshot, RiskDecisionStatus, Side
from autotrade.instrument_master import AuthoritativeInstrumentRules, InstrumentTradingStatus, SQLiteInstrumentMaster
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
STRATEGY_ID = "R6_CRYPTO_PAPER_QUALIFICATION_PREVIEW"
PREVIEW_MAX_NOTIONAL = Decimal("5")
PREVIEW_TARGET_NOTIONAL = Decimal("2")
MIN_BUY_MARKET_VALUE = Decimal("1")
MAX_ACCOUNT_FRACTION = Decimal("0.001")
QUALIFICATION_STOP_BPS = Decimal("100")
QUALIFICATION_LIMIT_BPS = Decimal("150")
CERTIFIED_TRACKS = ("R0", "R1", "R2", "R3", "R4", "R5")


class CryptoPaperCanaryPreviewError(RuntimeError):
    pass


class _NoBrokerSurface:
    def submit(self, **_kwargs):
        raise CryptoPaperCanaryPreviewError("qualification preview has no broker submission surface")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only BTC/USD PAPER qualification preview. It uses fresh broker GET evidence and the certified "
            "Safety/OMS/canary coordinator in an isolated temporary runtime, then displays the exact dry-run entry "
            "payload and protection/reconciliation plan. It cannot issue operator approval or broker POST."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--symbol", default=CRYPTO_PAIR)
    parser.add_argument("--allow-paper-crypto-read", action="store_true")
    return parser


def _credentials() -> AlpacaPaperCredentials:
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if not key or not secret:
        raise CryptoPaperCanaryPreviewError("PAPER Key + Secret are required for canary preview")
    return AlpacaPaperCredentials(key_id=key, secret_key=secret)


def _account_anchor(workspace: PaperOperationalWorkspace) -> str:
    path = workspace.account_attestation_path
    if path.is_symlink() or not path.is_file():
        raise CryptoPaperCanaryPreviewError(
            "verified PAPER account is missing; verify the PAPER account in the main Control Center first"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoPaperCanaryPreviewError("verified PAPER account evidence cannot be read") from exc
    if not isinstance(raw, dict) or raw.get("environment") != "PAPER":
        raise CryptoPaperCanaryPreviewError("workspace account evidence is not PAPER")
    if raw.get("credentials_persisted") is not False:
        raise CryptoPaperCanaryPreviewError("workspace account evidence violates credential policy")
    account_id = raw.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise CryptoPaperCanaryPreviewError("workspace account ID is missing")
    return account_id.strip()


def _ceil(value: Decimal, increment: Decimal) -> Decimal:
    units = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return units * increment


def _floor(value: Decimal, increment: Decimal) -> Decimal:
    units = (value / increment).to_integral_value(rounding=ROUND_FLOOR)
    return units * increment


def _qualification_quantity(
    *,
    min_order_size: Decimal,
    min_trade_increment: Decimal,
    limit_price: Decimal,
) -> Decimal:
    if limit_price <= 0 or min_order_size <= 0 or min_trade_increment <= 0:
        raise CryptoPaperCanaryPreviewError("qualification sizing inputs must be positive")
    broker_minimum = _ceil(min_order_size, min_trade_increment)
    target_minimum = _ceil(PREVIEW_TARGET_NOTIONAL / limit_price, min_trade_increment)
    quantity = max(broker_minimum, target_minimum)
    notional = quantity * limit_price
    if notional < MIN_BUY_MARKET_VALUE:
        quantity = _ceil(MIN_BUY_MARKET_VALUE / limit_price, min_trade_increment)
        quantity = max(quantity, broker_minimum)
    return quantity


def _qualification_protection_reference(entry_price: Decimal, price_increment: Decimal) -> tuple[Decimal, Decimal]:
    stop_factor = Decimal("1") - (QUALIFICATION_STOP_BPS / Decimal("10000"))
    limit_factor = Decimal("1") - (QUALIFICATION_LIMIT_BPS / Decimal("10000"))
    stop_price = _floor(entry_price * stop_factor, price_increment)
    limit_price = _floor(entry_price * limit_factor, price_increment)
    if stop_price <= 0 or limit_price <= 0 or limit_price > stop_price:
        raise CryptoPaperCanaryPreviewError("qualification protection reference cannot satisfy broker increments")
    return stop_price, limit_price


def run(
    *,
    workspace_path: Path,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    symbol: str = CRYPTO_PAIR,
) -> dict[str, object]:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise CryptoPaperCanaryPreviewError("canary preview refuses R6_EXTERNAL_PAPER_WRITE=ENABLED")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    canonical = normalize_crypto_pair(symbol)
    if canonical != CRYPTO_PAIR:
        raise CryptoPaperCanaryPreviewError(
            "first TD-R6-017 qualification preview is deliberately fixed to BTC/USD"
        )

    path = workspace_path.expanduser()
    if path.is_symlink() or not path.is_dir():
        raise CryptoPaperCanaryPreviewError("existing non-symlink workspace is required")
    workspace = PaperOperationalWorkspace(root=path.resolve())
    expected_account_id = _account_anchor(workspace)
    instant = now.astimezone(timezone.utc)
    gateway_config = AlpacaPaperGatewayConfig(enabled=True)

    account = AlpacaPaperAccountGateway(config=gateway_config).attest_account(
        credentials=credentials,
        expected_account_id=expected_account_id,
        now=instant,
    )
    asset = AlpacaPaperCryptoAssetGateway(config=gateway_config).attest_asset(
        credentials=credentials,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=account.credential_reference,
        now=instant,
        symbol=canonical,
    )
    product_profile = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )
    product_profile.require_order(order_type=BrokerOrderType.LIMIT, time_in_force=TimeInForce.IOC)
    product_profile.require_order(order_type=BrokerOrderType.STOP_LIMIT, time_in_force=TimeInForce.GTC)
    product_profile.require_margin(uses_margin=False)
    product_profile.require_opening_short(opening_short=False)

    flat = AlpacaPaperFlatAccountGateway(config=gateway_config).attest_flatness(
        credentials=credentials,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=account.credential_reference,
        now=instant,
    )
    if not flat.clean_for_first_canary:
        raise CryptoPaperCanaryPreviewError(
            f"PAPER account must be flat; positions={flat.position_count}, open_orders={flat.open_order_count}"
        )

    market_attestation = AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True)
    ).attest_snapshot(credentials=credentials, now=instant, symbol=canonical)
    market = market_attestation.market
    if market.symbol != asset.symbol:
        raise CryptoPaperCanaryPreviewError("crypto asset and market-data pair identity mismatch")

    effective_cap = min(
        PREVIEW_MAX_NOTIONAL,
        account.portfolio_value * MAX_ACCOUNT_FRACTION,
        account.buying_power,
    )
    if effective_cap <= 0:
        raise CryptoPaperCanaryPreviewError("PAPER account has no positive qualification-preview capacity")

    limit_price = _ceil(market.ask, asset.price_increment)
    quantity = _qualification_quantity(
        min_order_size=asset.min_order_size,
        min_trade_increment=asset.min_trade_increment,
        limit_price=limit_price,
    )
    notional = quantity * limit_price
    if quantity % asset.min_trade_increment != 0:
        raise CryptoPaperCanaryPreviewError("qualification BTC/USD quantity violates current broker increment")
    if quantity < asset.min_order_size:
        raise CryptoPaperCanaryPreviewError("qualification BTC/USD quantity is below current broker minimum")
    if notional < MIN_BUY_MARKET_VALUE:
        raise CryptoPaperCanaryPreviewError("qualification BTC/USD buy value is below the USD 1 market-value floor")
    if notional > effective_cap:
        raise CryptoPaperCanaryPreviewError(
            f"qualification BTC/USD notional {notional} exceeds qualification preview hard cap {effective_cap}"
        )

    base_currency, quote_currency = canonical.split("/", 1)
    portfolio = PortfolioSnapshot(
        snapshot_id=f"crypto-paper-qualification-preview:{account.account_id[:12]}:{canonical}",
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
    intent = OrderIntent(
        intent_id=f"crypto-paper-qualification-preview:{canonical}:{int(instant.timestamp() * 1000)}",
        idempotency_key=(
            f"crypto-paper-qualification-preview:{canonical}:{product_profile.fingerprint}:"
            f"{market_attestation.fingerprint}"
        ),
        strategy_id=STRATEGY_ID,
        symbol=canonical,
        side=Side.BUY,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        created_at=instant,
        limit_price=limit_price,
    )

    with TemporaryDirectory(prefix="auto-trade-r6-qualification-preview-") as temp:
        runtime = SQLiteRuntime(Path(temp) / "qualification-preview.sqlite3")
        ledger = SQLiteEventLedger(runtime)
        safety_state = SQLiteSafetyStateStore(runtime)
        SQLitePortfolioStore(runtime).initialize(portfolio, now=instant)
        rules = AuthoritativeInstrumentRules(
            venue=product_profile.venue,
            symbol=canonical,
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
            source_version=f"asset:{asset.fingerprint}:profile:{product_profile.fingerprint}:qualification-preview-v1",
            source_payload_sha256=asset.response_sha256,
            observed_at=asset.observed_at,
            valid_until=instant + timedelta(minutes=5),
        )
        rules.validate_candidate(quantity=quantity, price=limit_price)
        SQLiteInstrumentMaster(runtime).publish(rules, now=instant)
        limits = SafetyLimits(
            limits_version="r6-crypto-paper-qualification-preview-v1",
            allowed_symbols=frozenset({canonical}),
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
            decision_ttl_ms=15_000,
        )
        decision = CapitalSafetyKernel(limits, ledger, state_store=safety_state).evaluate(
            intent=intent,
            market=market,
            portfolio=portfolio,
            now=instant,
        )
        if decision.status is not RiskDecisionStatus.APPROVED:
            raise CryptoPaperCanaryPreviewError(
                f"Capital Safety rejected BTC/USD qualification preview: {decision.reason_code}: {decision.reason_detail}"
            )

        oms = OrderManagementSystem(
            broker=_NoBrokerSurface(),
            ledger=ledger,
            order_store=SQLiteOrderStore(runtime),
            safety_state_store=safety_state,
        )
        lifecycle = SQLiteCryptoPaperLifecycle(runtime)
        try:
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
                relevant_open_orders=flat.open_order_count,
                confirmed_pair_position_quantity=Decimal("0"),
            )
        except Exception as exc:
            raise CryptoPaperCanaryPreviewError(
                f"coordinator preparation blocked: {type(exc).__name__}: {exc}"
            ) from exc
        package = prepared.package
        dry_run_attempt_id = f"preview-{package.package_hash[:24]}"
        context = CryptoOperatorDecisionContext.from_prepared_package(
            package,
            attempt_id=dry_run_attempt_id,
        )
        challenge = crypto_operator_confirmation_challenge(context)
        dry_run_protection_client_order_id = deterministic_crypto_client_order_id(
            lifecycle_id=package.lifecycle_id,
            role=CryptoOrderRole.PROTECTION,
        )
        reference_stop, reference_limit = _qualification_protection_reference(
            package.limit_price,
            asset.price_increment,
        )
        if not ledger.verify_integrity():
            raise CryptoPaperCanaryPreviewError("qualification preview Event Ledger integrity failed")

        return {
            "status": "CRYPTO_PAPER_QUALIFICATION_PREVIEW_PASS",
            "environment": "PAPER",
            "mode": "DRY_RUN_NO_POST",
            "symbol": canonical,
            "broker_reads": 6,
            "account_flat": True,
            "quote": {
                "bid": str(market.bid),
                "ask": str(market.ask),
                "last": str(market.last),
            },
            "entry": {
                "payload": prepared.broker_order.to_payload(),
                "quantity": str(package.quantity),
                "limit_price": str(package.limit_price),
                "notional": str(package.notional),
                "target_notional": str(PREVIEW_TARGET_NOTIONAL),
                "minimum_buy_market_value": str(MIN_BUY_MARKET_VALUE),
                "broker_min_order_size": str(asset.min_order_size),
                "broker_min_trade_increment": str(asset.min_trade_increment),
                "safety_hard_cap": str(effective_cap),
                "coordinator_effective_cap": str(package.effective_notional_cap),
                "dry_run_client_order_id": package.client_order_id,
                "payload_hash": package.crypto_order_payload_hash,
                "package_hash": package.package_hash,
                "oms_status": package.order_status,
                "network_write_authorized": package.network_write_authorized,
            },
            "operator": {
                "approval_recorded": False,
                "decision_consumed": False,
                "dry_run_attempt_id": dry_run_attempt_id,
                "dry_run_challenge": challenge,
                "execution_deadline": package.execution_deadline.isoformat(),
                "reusable_for_real_execution": False,
                "note": "Real execution must regenerate fresh evidence/package/client_order_id and require a new exact human challenge.",
            },
            "protection": {
                "model": product_profile.protection_model.value,
                "time_in_force": TimeInForce.GTC.value,
                "dry_run_client_order_id": dry_run_protection_client_order_id,
                "qualification_stop_bps_below_fill": str(QUALIFICATION_STOP_BPS),
                "qualification_limit_bps_below_fill": str(QUALIFICATION_LIMIT_BPS),
                "reference_stop_if_fill_at_entry_limit": str(reference_stop),
                "reference_limit_if_fill_at_entry_limit": str(reference_limit),
                "quantity_rule": "EXACT_CONFIRMED_NET_LONG_AFTER_RECONCILIATION",
                "warning": "STOP_LIMIT_IS_NOT_A_GUARANTEED_EXIT_OR_MAX_LOSS",
            },
            "ambiguity_policy": {
                "unknown_before_io": True,
                "blind_retry": False,
                "on_timeout_or_ambiguous_ack": "RECONCILE_ONLY",
                "order_404_retry_permission": False,
                "remaining_long": "HALT_AND_PROTECT_OR_RECONCILE",
            },
            "capital_safety": decision.status.value,
            "broker_write_performed": False,
            "external_post_authorized": False,
            "operator_approval_authority": "NONE",
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
            "profitability_claim": False,
            "next_action": "HUMAN_REVIEW_PREVIEW_THEN_BUILD_SEPARATE_ONE_SHOT_EXECUTION_GATE",
        }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_crypto_read:
        raise SystemExit("canary preview requires explicit --allow-paper-crypto-read")
    try:
        result = run(
            workspace_path=args.workspace,
            credentials=_credentials(),
            now=datetime.now(timezone.utc),
            symbol=args.symbol,
        )
    except Exception as exc:
        blocked = {
            "status": "CRYPTO_PAPER_QUALIFICATION_PREVIEW_BLOCKED",
            "mode": "DRY_RUN_NO_POST",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "broker_write_performed": False,
            "external_post_authorized": False,
            "operator_approval_authority": "NONE",
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        print(json.dumps(blocked, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
