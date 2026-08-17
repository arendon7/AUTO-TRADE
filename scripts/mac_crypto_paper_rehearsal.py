from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from autotrade.brokers.alpaca_paper_crypto_asset import (
    CRYPTO_PAIR,
    AlpacaPaperCryptoAssetGateway,
    normalize_crypto_pair,
)
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    AlpacaPaperCryptoMarketDataConfig,
    AlpacaPaperCryptoMarketDataGateway,
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
STRATEGY_ID = "R6_CRYPTO_PAPER_REHEARSAL"
MAX_REHEARSAL_NOTIONAL = Decimal("25")
MAX_ACCOUNT_FRACTION = Decimal("0.001")


class CryptoPaperRehearsalError(RuntimeError):
    pass


class _NoBrokerSurface:
    def submit(self, **_kwargs):
        raise CryptoPaperRehearsalError("crypto rehearsal has no broker submission surface")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "24/7 canonical crypto-pair PAPER connectivity rehearsal. It refreshes the verified PAPER account, "
            "reads exact pair asset metadata, proves the account flat, reads live crypto market data, binds a "
            "ProductCapabilities profile, then runs Capital Safety + OMS validation locally. No broker order is sent."
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
        raise CryptoPaperRehearsalError("PAPER Key + Secret are required for crypto rehearsal")
    return AlpacaPaperCredentials(key_id=key, secret_key=secret)


def _account_anchor(workspace: PaperOperationalWorkspace) -> str:
    path = workspace.account_attestation_path
    if path.is_symlink() or not path.is_file():
        raise CryptoPaperRehearsalError(
            "verified PAPER account is missing; open the main Control Center and confirm the PAPER account first"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoPaperRehearsalError("verified PAPER account evidence cannot be read") from exc
    if not isinstance(raw, dict) or raw.get("environment") != "PAPER":
        raise CryptoPaperRehearsalError("workspace account evidence is not PAPER")
    if raw.get("credentials_persisted") is not False:
        raise CryptoPaperRehearsalError("workspace account evidence violates credential policy")
    account_id = raw.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise CryptoPaperRehearsalError("workspace account ID is missing")
    return account_id.strip()


def _ceil(value: Decimal, increment: Decimal) -> Decimal:
    units = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return units * increment


def run(
    *,
    workspace_path: Path,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    symbol: str = CRYPTO_PAIR,
) -> dict[str, object]:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise CryptoPaperRehearsalError("crypto rehearsal refuses R6_EXTERNAL_PAPER_WRITE=ENABLED")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    canonical = normalize_crypto_pair(symbol)
    path = workspace_path.expanduser()
    if path.is_symlink() or not path.is_dir():
        raise CryptoPaperRehearsalError("existing non-symlink workspace is required")
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
    product_profile.require_order(order_type=BrokerOrderType.LIMIT, time_in_force=TimeInForce.GTC)
    product_profile.require_margin(uses_margin=False)
    product_profile.require_opening_short(opening_short=False)

    flat = AlpacaPaperFlatAccountGateway(config=gateway_config).attest_flatness(
        credentials=credentials,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=account.credential_reference,
        now=instant,
    )
    if not flat.clean_for_first_canary:
        raise CryptoPaperRehearsalError(
            f"PAPER account must be flat; positions={flat.position_count}, open_orders={flat.open_order_count}"
        )
    market_attestation = AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True)
    ).attest_snapshot(credentials=credentials, now=instant, symbol=canonical)
    market = market_attestation.market
    if market.symbol != asset.symbol:
        raise CryptoPaperRehearsalError("crypto asset and market-data pair identity mismatch")

    portfolio_value = account.portfolio_value
    buying_power = account.buying_power
    effective_cap = min(MAX_REHEARSAL_NOTIONAL, portfolio_value * MAX_ACCOUNT_FRACTION, buying_power)
    if effective_cap <= 0:
        raise CryptoPaperRehearsalError("PAPER account has no positive rehearsal capacity")
    quantity = asset.min_order_size
    limit_price = _ceil(market.ask, asset.price_increment)
    notional = quantity * limit_price
    if notional > effective_cap:
        raise CryptoPaperRehearsalError(
            f"minimum {canonical} notional {notional} exceeds conservative rehearsal cap {effective_cap}"
        )
    if quantity % asset.min_trade_increment != 0:
        raise CryptoPaperRehearsalError(f"minimum {canonical} quantity violates Alpaca trade increment")

    base_currency, quote_currency = canonical.split("/", 1)
    snapshot = PortfolioSnapshot(
        snapshot_id=f"crypto-paper-rehearsal:{account.account_id[:12]}:{canonical}",
        equity=portfolio_value,
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
        intent_id=f"crypto-paper-rehearsal:{canonical}:{int(instant.timestamp() * 1000)}",
        idempotency_key=(
            f"crypto-paper-rehearsal:{canonical}:{product_profile.fingerprint}:"
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

    with TemporaryDirectory(prefix="auto-trade-r6-crypto-") as temp:
        runtime = SQLiteRuntime(Path(temp) / "crypto-rehearsal.sqlite3")
        ledger = SQLiteEventLedger(runtime)
        safety_state = SQLiteSafetyStateStore(runtime)
        versioned = SQLitePortfolioStore(runtime).initialize(snapshot, now=instant)
        rules = AuthoritativeInstrumentRules(
            venue=product_profile.venue,
            symbol=canonical,
            base_currency=base_currency,
            quote_currency=quote_currency,
            version=1,
            price_tick=asset.price_increment,
            quantity_step=asset.min_trade_increment,
            min_quantity=asset.min_order_size,
            max_quantity=asset.min_order_size,
            min_notional=None,
            max_notional=effective_cap,
            trading_status=InstrumentTradingStatus.TRADING,
            source="ALPACA_PAPER_CRYPTO_ASSET",
            source_version=f"asset:{asset.fingerprint}:profile:{product_profile.fingerprint}:rehearsal-v2",
            source_payload_sha256=asset.response_sha256,
            observed_at=asset.observed_at,
            valid_until=instant + timedelta(minutes=5),
        )
        rules.validate_candidate(quantity=quantity, price=limit_price)
        SQLiteInstrumentMaster(runtime).publish(rules, now=instant)
        limits = SafetyLimits(
            limits_version="r6-crypto-paper-rehearsal-v2",
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
            portfolio=snapshot,
            now=instant,
        )
        if decision.status is not RiskDecisionStatus.APPROVED:
            raise CryptoPaperRehearsalError(
                f"Capital Safety rejected {canonical} rehearsal: {decision.reason_code}: {decision.reason_detail}"
            )
        order = OrderManagementSystem(
            broker=_NoBrokerSurface(),
            ledger=ledger,
            order_store=SQLiteOrderStore(runtime),
            safety_state_store=safety_state,
        ).validate_for_external_submission(
            intent=intent,
            decision=decision,
            market=market,
            now=instant,
        )
        if versioned.version != 1 or order.status.value != "VALIDATED":
            raise CryptoPaperRehearsalError("crypto rehearsal local OMS state is not fresh VALIDATED")
        if not ledger.verify_integrity():
            raise CryptoPaperRehearsalError("crypto rehearsal Event Ledger integrity failed")

    return {
        "status": "CRYPTO_PAPER_REHEARSAL_PASS",
        "environment": "PAPER",
        "symbol": canonical,
        "asset_class": asset.asset_class,
        "exchange": asset.exchange,
        "market_location": market_attestation.location,
        "product_profile_fingerprint": product_profile.fingerprint,
        "product_protection_model": product_profile.protection_model.value,
        "market_hours_model": product_profile.market_hours_model.value,
        "bid": str(market.bid),
        "ask": str(market.ask),
        "last": str(market.last),
        "quantity": str(quantity),
        "limit_price": str(limit_price),
        "rehearsal_notional": str(notional),
        "effective_notional_cap": str(effective_cap),
        "capital_safety": decision.status.value,
        "oms_status": order.status.value,
        "account_flat": True,
        "broker_reads": 6,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "external_order_submitted": False,
        "persistent_crypto_candidate_created": False,
        "crypto_bracket_supported": False,
        "capital_authority": "NONE",
        "profitability_claim": False,
        "live_trading": "BLOCKED",
        "next_action": "CERTIFY_CRYPTO_WRITER_AND_PROTECTION_LIFECYCLE_BEFORE_ANY_POST",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_crypto_read:
        raise SystemExit("crypto rehearsal requires explicit --allow-paper-crypto-read")
    result = run(
        workspace_path=args.workspace,
        credentials=_credentials(),
        now=datetime.now(timezone.utc),
        symbol=args.symbol,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
