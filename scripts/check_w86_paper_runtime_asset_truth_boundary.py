from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/paper_runtime_asset_truth.py"
ALLOWED_BROKER_IMPORTS = {
    "autotrade.brokers.alpaca_paper_crypto_asset",
    "autotrade.brokers.alpaca_paper_gateway",
}
FORBIDDEN_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "socket",
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
        print("ERROR: missing W86 PAPER runtime asset-truth module", file=sys.stderr)
        return 1

    source = TARGET.read_text(encoding="utf-8")
    required = (
        'PAPER_RUNTIME_ASSET_TRUTH_VERSION = "W86_PAPER_RUNTIME_ASSET_TRUTH_V1"',
        "class PaperRuntimeAssetTruthPolicy:",
        "class PaperRuntimeAssetTruthProof:",
        "def derive_alpaca_crypto_pair(",
        "def read_and_bind_paper_runtime_asset_truth(",
        "def bind_paper_runtime_asset_truth(",
        "AlpacaPaperCryptoAssetGateway(",
        ").attest_asset(",
        'expected_symbol = f"{candidate_identity.base_currency}-{candidate_identity.quote_currency}"',
        'f"{candidate_identity.base_currency}/{candidate_identity.quote_currency}"',
        "normalize_crypto_pair(",
        "value.symbol != pair",
        "candidate_identity.symbol != expected_symbol",
        "value.proof_hash != expected",
        "broker_module._proof_payload(value, include_hash=False)",
        "value.account_attestation_fingerprint != broker_truth.account_attestation_fingerprint",
        "value.credential_reference != broker_truth.credential_reference",
        "value.source_host != ALPACA_PAPER_TRADING_HOST",
        "value.source_path != crypto_asset_path(pair)",
        "max_asset_age_seconds",
        "max_broker_asset_skew_seconds",
        "asset_time < broker_time",
        "asset_time - broker_time > timedelta(",
        '"min_order_size"',
        '"min_trade_increment"',
        '"price_increment"',
        '"network_write_performed": False',
        '"paper_runtime_ready": False',
        '"paper_execution_authorized": False',
        '"external_execution_authorized": False',
        '"runtime_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        "return bind_paper_runtime_asset_truth(",
    )
    for anchor in required:
        if anchor not in source:
            errors.append(f"W86 asset-truth contract missing: {anchor}")

    for forbidden in (
        "OrderIntent(",
        "PaperCanaryExecutionBridge",
        "AlpacaPaperSingleShotWriter",
        "AlpacaPaperCryptoOrderWriter",
        "api.alpaca.markets",
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
            errors.append(f"W86 asset-truth contains forbidden surface: {forbidden}")

    try:
        tree = ast.parse(source, filename=str(TARGET))
    except SyntaxError as exc:
        errors.append(f"W86 asset-truth syntax error: {exc}")
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
                        f"W86 asset-truth imports unapproved broker surface at line {node.lineno}: {module}"
                    )
                if any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in FORBIDDEN_IMPORT_PREFIXES
                ):
                    errors.append(
                        f"W86 asset-truth imports forbidden authority/network surface at line {node.lineno}: {module}"
                    )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr.lower() in FORBIDDEN_CALL_NAMES:
                    errors.append(
                        f"W86 asset-truth contains forbidden mutating call at line {node.lineno}: {node.func.attr}"
                    )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id.lower() in FORBIDDEN_CALL_NAMES:
                    errors.append(
                        f"W86 asset-truth contains forbidden mutating call at line {node.lineno}: {node.func.id}"
                    )

    if source.count(".attest_asset(") != 1:
        errors.append("W86 asset-truth must have exactly one controlled asset GET reader call site")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE W86 PAPER runtime asset truth boundary: PASS "
        "(frozen BASE-QUOTE -> exact BASE/QUOTE mapping; one existing GET-only asset reader; "
        "account/credential/freshness binding; no readiness, execution, capital or LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
