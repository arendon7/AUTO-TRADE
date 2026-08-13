from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BROKER_DIR = ROOT / "src/autotrade/brokers"
SRC = ROOT / "src/autotrade"
BRACKET_FILE = "alpaca_paper_bracket.py"
OPERATIONAL_FILE = "alpaca_paper_operational.py"
TRADE_UPDATES_FILE = "alpaca_paper_trade_updates.py"
CRYPTO_ASSET_FILE = "alpaca_paper_crypto_asset.py"
CRYPTO_MARKET_FILE = "alpaca_paper_crypto_market_data.py"
CRYPTO_ORDER_FILE = "alpaca_paper_crypto_order.py"
CRYPTO_LIFECYCLE_FILE = "alpaca_paper_crypto_lifecycle.py"
PRODUCT_PROFILE = SRC / "product_profile.py"

CRYPTO_FILES = {
    CRYPTO_ASSET_FILE,
    CRYPTO_MARKET_FILE,
    CRYPTO_ORDER_FILE,
    CRYPTO_LIFECYCLE_FILE,
    "alpaca_paper_crypto_catalog.py",
}
CRYPTO_FORBIDDEN_IMPORT_FRAGMENTS = {
    "alpaca_paper_bracket",
    "alpaca_paper_writer",
    "alpaca_paper_final_guard",
    "connectivity_final_freshness",
    "connectivity_workspace_post",
}


class ProductBoundaryViolation(RuntimeError):
    pass


def main() -> int:
    errors: list[str] = []
    bracket = BROKER_DIR / BRACKET_FILE
    operational = BROKER_DIR / OPERATIONAL_FILE
    updates = BROKER_DIR / TRADE_UPDATES_FILE
    if not bracket.is_file():
        errors.append(f"missing {BRACKET_FILE}")
    else:
        errors.extend(_validate_bracket_contract(bracket))
        errors.extend(_validate_equity_does_not_import_crypto(bracket))
    if not operational.is_file():
        errors.append(f"missing {OPERATIONAL_FILE}")
    else:
        errors.extend(_validate_operational_rehydration_contract(operational))
    if not updates.is_file():
        errors.append(f"missing {TRADE_UPDATES_FILE}")
    else:
        errors.extend(_validate_trade_update_contract(updates))
    errors.extend(_validate_constructor_authority())
    errors.extend(_validate_product_profile())
    errors.extend(_validate_crypto_files())

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(
        "AUTO-TRADE R6 multi-asset product boundary: PASS "
        "(certified us_equity bracket preserved; crypto uses explicit ProductCapabilities, "
        "24/7 pair semantics and separate no-network protection lifecycle; no cross-product writer path)"
    )
    return 0


def _validate_bracket_contract(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    required = {
        'if self.asset_class != "us_equity"': "venue/request asset-class deny is missing",
        'raise ValueError("R6 bracket request supports us_equity only")': "request us_equity-only invariant is missing",
        '"order_class": "bracket"': "exact bracket order_class payload is missing",
        'if payload.get("order_class") != "bracket"': "request order_class self-check is missing",
        'if payload.get("type") != "limit"': "request LIMIT-only self-check is missing",
        'if payload.get("side") != "buy"': "request BUY-only self-check is missing",
        'if payload.get("time_in_force") != "day"': "request DAY-only self-check is missing",
        'if payload.get("extended_hours") is not False': "extended-hours deny is missing",
        'asset_class=venue_rules.asset_class': "builder does not bind authoritative venue asset class",
        'if frozenset(payload) != _BRACKET_PAYLOAD_KEYS': "exact request payload surface check is missing",
        'if self.payload_hash != calculated_hash': "request payload hash self-check is missing",
    }
    return [reason for needle, reason in required.items() if needle not in text]


def _validate_operational_rehydration_contract(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    required = {
        "def read_expected_bracket(path: Path) -> AlpacaEquityBracketRequest:": "canonical bracket artifact reader is missing",
        'if raw.get("schema_version") != 1 or raw.get("environment") != "PAPER"': "PAPER artifact header validation is missing",
        'raw.get("network_write_authorized") is not False': "artifact network-authority deny is missing",
        'raw.get("live_trading") != "BLOCKED"': "artifact LIVE deny is missing",
        "bracket = AlpacaEquityBracketRequest(": "artifact rehydration constructor is missing",
        "if expected_bracket_payload(bracket) != raw:": "artifact canonical roundtrip check is missing",
        'raise PaperOperationalIntegrityError("expected bracket artifact is not canonical")': "artifact noncanonical rejection is missing",
    }
    return [reason for needle, reason in required.items() if needle not in text]


def _validate_trade_update_contract(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    required = {
        'if self.asset_class != "us_equity"': "trade_update event us_equity invariant is missing",
        'if asset_class != "us_equity"': "trade_update parser us_equity scope deny is missing",
        'raise PaperTradeUpdateScopeError("R6 trade_updates supports us_equity only")': "trade_update unsupported asset-class rejection is missing",
    }
    return [reason for needle, reason in required.items() if needle not in text]


def _validate_equity_does_not_import_crypto(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = _imports(tree)
    return [
        f"{path.name}: equity bracket may not import crypto module {module}"
        for module in modules
        if "crypto" in module.lower()
    ]


def _validate_product_profile() -> list[str]:
    if not PRODUCT_PROFILE.is_file():
        return ["missing src/autotrade/product_profile.py"]
    text = PRODUCT_PROFILE.read_text(encoding="utf-8")
    required = {
        'US_EQUITY = "US_EQUITY"': "US_EQUITY AssetClass is missing",
        'CRYPTO = "CRYPTO"': "CRYPTO AssetClass is missing",
        'SESSION_CLOCKED = "SESSION_CLOCKED"': "equity session market-hours model is missing",
        'CONTINUOUS_24_7 = "CONTINUOUS_24_7"': "crypto 24/7 market-hours model is missing",
        'EQUITY_BRACKET = "EQUITY_BRACKET"': "equity bracket protection model is missing",
        'CRYPTO_STOP_LIMIT = "CRYPTO_STOP_LIMIT"': "crypto stop-limit protection model is missing",
        "if self.asset_class is AssetClass.CRYPTO:": "crypto profile branch is missing",
        "if self.asset_class is AssetClass.US_EQUITY:": "equity profile branch is missing",
        "crypto profile may not claim margin or short authority": "crypto leverage deny is missing",
        "crypto may not reuse the equity bracket protection model": "crypto/equity protection separation is missing",
        "source_fingerprint": "broker-evidence fingerprint binding is missing",
    }
    return [reason for needle, reason in required.items() if needle not in text]


def _validate_crypto_files() -> list[str]:
    errors: list[str] = []
    for name in sorted(CRYPTO_FILES):
        path = BROKER_DIR / name
        if not path.is_file():
            errors.append(f"missing crypto product module {name}")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imports(tree):
            if any(fragment in module for fragment in CRYPTO_FORBIDDEN_IMPORT_FRAGMENTS):
                errors.append(f"{name}: crypto module imports forbidden equity/write authority {module}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node.func) == "AlpacaEquityBracketRequest":
                errors.append(f"{name}:{node.lineno}: crypto may never construct AlpacaEquityBracketRequest")

    order = BROKER_DIR / CRYPTO_ORDER_FILE
    if order.is_file():
        text = order.read_text(encoding="utf-8")
        required = {
            "class CryptoOrderRole": "crypto order role separation is missing",
            'ENTRY = "ENTRY"': "crypto ENTRY role is missing",
            'PROTECTION = "PROTECTION"': "crypto PROTECTION role is missing",
            "build_crypto_entry_order": "crypto entry builder is missing",
            "build_crypto_long_protection_order": "crypto protection builder is missing",
            "ProductCapabilities": "crypto order is not bound to ProductCapabilities",
            "asset_attestation_fingerprint": "crypto order is not bound to asset attestation",
            "protection may not exceed confirmed net long position": "protection position bound is missing",
            "long sell protection requires limit_price <= stop_price": "stop-limit sell price invariant is missing",
        }
        errors.extend(
            f"{CRYPTO_ORDER_FILE}: {reason}"
            for needle, reason in required.items()
            if needle not in text
        )
        if '"order_class"' in text or "AlpacaEquityBracketRequest" in text:
            errors.append(f"{CRYPTO_ORDER_FILE}: crypto order contract may not expose equity bracket semantics")
        for network_marker in ("urllib", "requests", "httpx", "socket", ".write(", ".submit("):
            if network_marker in text:
                errors.append(f"{CRYPTO_ORDER_FILE}: no-network order contract contains forbidden marker {network_marker}")

    lifecycle = BROKER_DIR / CRYPTO_LIFECYCLE_FILE
    if lifecycle.is_file():
        text = lifecycle.read_text(encoding="utf-8")
        required = {
            'ENTRY_SUBMISSION_UNKNOWN = "ENTRY_SUBMISSION_UNKNOWN"': "entry UNKNOWN state is missing",
            'PROTECTION_SUBMISSION_UNKNOWN = "PROTECTION_SUBMISSION_UNKNOWN"': "protection UNKNOWN state is missing",
            'PROTECTION_AT_RISK = "PROTECTION_AT_RISK"': "stop-limit residual-risk state is missing",
            'HALTED_RECONCILIATION_REQUIRED = "HALTED_RECONCILIATION_REQUIRED"': "reconciliation halt state is missing",
            'return "RECONCILE_ONLY"': "restart reconciliation-only policy is missing",
            "entry submission may cross UNKNOWN exactly once": "entry duplicate-attempt block is missing",
            "protection submission may cross UNKNOWN exactly once": "protection duplicate-attempt block is missing",
            "remaining entry order must be terminal before opposing protection": "partial-entry/wash-trade boundary is missing",
            "first-canary protection must cover exactly the confirmed net long quantity": "full first-canary protection invariant is missing",
            "stop-limit trigger risk may be marked only from PROTECTED_OPEN": "trigger residual-risk transition is missing",
        }
        errors.extend(
            f"{CRYPTO_LIFECYCLE_FILE}: {reason}"
            for needle, reason in required.items()
            if needle not in text
        )
        for network_marker in ("urllib", "requests", "httpx", "socket", ".write(", ".submit("):
            if network_marker in text:
                errors.append(f"{CRYPTO_LIFECYCLE_FILE}: durable lifecycle contains forbidden network marker {network_marker}")
    return errors


def _constructor_lines_in_function(tree: ast.AST, function_name: str) -> list[int]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return [
                call.lineno
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and _call_name(call.func) == "AlpacaEquityBracketRequest"
            ]
    return []


def _validate_constructor_authority() -> list[str]:
    errors: list[str] = []
    constructor_calls: list[tuple[Path, int]] = []
    allowed_files = {BRACKET_FILE, OPERATIONAL_FILE}
    for path in sorted(BROKER_DIR.glob("alpaca_paper_*.py")):
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node.func) == "AlpacaEquityBracketRequest":
                constructor_calls.append((path, node.lineno))
                if path.name not in allowed_files:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: AlpacaEquityBracketRequest construction is forbidden outside certified builder/artifact reader"
                    )
        if path.name == OPERATIONAL_FILE:
            allowed_lines = set(_constructor_lines_in_function(tree, "read_expected_bracket"))
            for call_path, line in constructor_calls:
                if call_path == path and line not in allowed_lines:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{line}: operational bracket construction is allowed only inside read_expected_bracket"
                    )

    bracket_calls = [item for item in constructor_calls if item[0].name == BRACKET_FILE]
    operational_calls = [item for item in constructor_calls if item[0].name == OPERATIONAL_FILE]
    if len(bracket_calls) != 1:
        errors.append(
            f"{BRACKET_FILE}: expected exactly one production AlpacaEquityBracketRequest builder constructor, found {len(bracket_calls)}"
        )
    if len(operational_calls) != 1:
        errors.append(
            f"{OPERATIONAL_FILE}: expected exactly one canonical artifact rehydration constructor, found {len(operational_calls)}"
        )
    return errors


def _imports(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
