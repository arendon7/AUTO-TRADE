from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/paper_runtime_market_truth.py"
ALLOWED_BROKER_IMPORTS = {
    "autotrade.brokers.alpaca_paper_crypto_asset",
    "autotrade.brokers.alpaca_paper_crypto_market_data",
    "autotrade.brokers.alpaca_paper_gateway",
    "autotrade.brokers.alpaca_paper_market_data",
}
FORBIDDEN_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "socket",
    "urllib",
    "websocket",
    "websockets",
    "sqlite3",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.health_bridge",
    "autotrade.execution",
    "autotrade.paper_writer",
    "autotrade.brokers.alpaca_paper_writer",
    "autotrade.brokers.alpaca_paper_crypto_writer",
    "autotrade.brokers.alpaca_paper_submission",
)
FORBIDDEN_CALL_NAMES = {
    "post",
    "put",
    "patch",
    "delete",
    "submit",
    "submit_once",
    "place_order",
    "cancel_order",
    "replace_order",
    "stage_external_submission",
    "reserve",
    "reserve_capital",
    "evaluate_order",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        print("ERROR: missing W86 PAPER runtime market-truth module", file=sys.stderr)
        return 1

    source = TARGET.read_text(encoding="utf-8")
    required = (
        'PAPER_RUNTIME_MARKET_TRUTH_VERSION = "W86_PAPER_RUNTIME_MARKET_TRUTH_V1"',
        "class PaperRuntimeMarketTruthPolicy:",
        "class PaperRuntimeMarketTruthProof:",
        "def read_and_bind_paper_runtime_market_truth(",
        "def bind_paper_runtime_market_truth(",
        "AlpacaPaperCryptoMarketDataGateway(",
        ").attest_snapshot(",
        "LATEST_QUOTE_PATH",
        "LATEST_TRADE_PATH",
        "crypto_exact_query(pair)",
        "candidate.symbol != expected_symbol",
        "asset.canonical_broker_pair != pair",
        "market.market.symbol != pair",
        "market.location != CRYPTO_LOCATION",
        "market.source_host != ALPACA_MARKET_DATA_HOST",
        "market.market.observed_at.astimezone(timezone.utc) != market.received_at.astimezone(",
        "receipt_time < asset_time",
        "max_asset_market_skew_seconds",
        "receipt_time > process_time",
        "max_market_receipt_age_seconds",
        "quote_age < 0",
        "trade_age < 0",
        "max_quote_age_seconds",
        "max_trade_age_seconds",
        '"quote_fresh": True',
        '"trade_fresh": True',
        '"both_sides_fresh": True',
        '"market_truth_verified": True',
        '"read_only_market_truth": True',
        '"network_write_performed": False',
        '"paper_runtime_ready": False',
        '"paper_execution_authorized": False',
        '"external_execution_authorized": False',
        '"runtime_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        "candidate_module._payload(candidate, include_hash=False)",
        "broker_module._proof_payload(broker, include_hash=False)",
        "asset_module._proof_payload(asset, include_hash=False)",
        "return bind_paper_runtime_market_truth(",
    )
    for anchor in required:
        if anchor not in source:
            errors.append(f"W86 market-truth contract missing: {anchor}")

    for forbidden in (
        "OrderIntent(",
        "PaperCanaryExecutionBridge",
        "AlpacaPaperSingleShotWriter",
        "AlpacaPaperCryptoOrderWriter",
        "paper-api.alpaca.markets",
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
        "CREATE TABLE",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
    ):
        if forbidden in source:
            errors.append(f"W86 market-truth contains forbidden surface: {forbidden}")

    try:
        tree = ast.parse(source, filename=str(TARGET))
    except SyntaxError as exc:
        errors.append(f"W86 market-truth syntax error: {exc}")
        tree = None

    if tree is not None:
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            for module in modules:
                if module.startswith("autotrade.brokers.") and module not in ALLOWED_BROKER_IMPORTS:
                    errors.append(
                        f"W86 market-truth imports unapproved broker surface at line {node.lineno}: {module}"
                    )
                if any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in FORBIDDEN_IMPORT_PREFIXES
                ):
                    errors.append(
                        f"W86 market-truth imports forbidden authority/network surface at line {node.lineno}: {module}"
                    )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr.lower() in FORBIDDEN_CALL_NAMES:
                    errors.append(
                        f"W86 market-truth contains forbidden mutating call at line {node.lineno}: {node.func.attr}"
                    )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id.lower() in FORBIDDEN_CALL_NAMES:
                    errors.append(
                        f"W86 market-truth contains forbidden mutating call at line {node.lineno}: {node.func.id}"
                    )

    if source.count(".attest_snapshot(") != 1:
        errors.append(
            "W86 market-truth must have exactly one controlled crypto quote/trade reader call site"
        )
    if source.count('"quote_fresh": True') != 1 or source.count('"trade_fresh": True') != 1:
        errors.append("W86 market-truth freshness booleans must be minted only by the validated binder")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "AUTO-TRADE W86 PAPER runtime market truth boundary: PASS "
        "(frozen crypto/USD pair; exact existing GET-only quote+trade reader; "
        "both quote and trade fresh; exact chain/hash/freshness binding; "
        "no readiness, execution, capital or LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
