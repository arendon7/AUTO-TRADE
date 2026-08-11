from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BROKER_DIR = ROOT / "src/autotrade/brokers"
BRACKET_FILE = "alpaca_paper_bracket.py"
TRADE_UPDATES_FILE = "alpaca_paper_trade_updates.py"


class ProductBoundaryViolation(RuntimeError):
    pass


def main() -> int:
    errors: list[str] = []
    bracket = BROKER_DIR / BRACKET_FILE
    updates = BROKER_DIR / TRADE_UPDATES_FILE
    if not bracket.is_file():
        errors.append(f"missing {BRACKET_FILE}")
    else:
        errors.extend(_validate_bracket_contract(bracket))
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


def _validate_trade_update_contract(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    required = {
        'if self.asset_class != "us_equity"': "trade_update event us_equity invariant is missing",
        'if asset_class != "us_equity"': "trade_update parser us_equity scope deny is missing",
        'raise PaperTradeUpdateScopeError("R6 trade_updates supports us_equity only")': "trade_update unsupported asset-class rejection is missing",
    }
    return [reason for needle, reason in required.items() if needle not in text]


def _validate_constructor_authority() -> list[str]:
    errors: list[str] = []
    constructor_calls: list[tuple[Path, int]] = []
    for path in sorted(BROKER_DIR.glob("alpaca_paper_*.py")):
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node.func) == "AlpacaEquityBracketRequest":
                constructor_calls.append((path, node.lineno))
                if path.name != BRACKET_FILE:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: AlpacaEquityBracketRequest construction is forbidden outside certified builder"
                    )
    bracket_calls = [item for item in constructor_calls if item[0].name == BRACKET_FILE]
    if len(bracket_calls) != 1:
        errors.append(
            f"{BRACKET_FILE}: expected exactly one production AlpacaEquityBracketRequest constructor, found {len(bracket_calls)}"
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
