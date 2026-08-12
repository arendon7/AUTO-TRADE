from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BROKER_DIR = ROOT / "src/autotrade/brokers"
BRACKET_FILE = "alpaca_paper_bracket.py"
OPERATIONAL_FILE = "alpaca_paper_operational.py"
TRADE_UPDATES_FILE = "alpaca_paper_trade_updates.py"


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
    if not operational.is_file():
        errors.append(f"missing {OPERATIONAL_FILE}")
    else:
        errors.extend(_validate_operational_rehydration_contract(operational))
    if not updates.is_file():
        errors.append(f"missing {TRADE_UPDATES_FILE}")
    else:
        errors.extend(_validate_trade_update_contract(updates))
    errors.extend(_validate_constructor_authority())

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("AUTO-TRADE R6 unsupported-product boundary: PASS (us_equity bracket only)")
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


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
