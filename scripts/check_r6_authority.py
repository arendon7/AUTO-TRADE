from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BROKER_DIR = ROOT / "src/autotrade/brokers"
R6_PREFIX = "alpaca_paper_"
CURRENT_PHASE = "ATTESTATION_AND_AMBIGUITY_ONLY"

PAPER_HOST = "paper-api.alpaca.markets"
LIVE_HOST = "api.alpaca.markets"
APPROVED_NETWORK_FILE = "alpaca_paper_gateway.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "openai",
    "anthropic",
    "autotrade.research",
)
NETWORK_ROOTS = {
    "http",
    "httpx",
    "requests",
    "socket",
    "ssl",
    "urllib",
    "websocket",
    "websockets",
}
FORBIDDEN_CALLS_PRE_SUBMIT = {
    "post",
    "send",
    "submit",
    "submit_order",
    "place_order",
    "create_order",
    "replace_order",
    "cancel_order",
}
FORBIDDEN_EXECUTION_SYMBOLS = {
    "OpenAI",
    "Anthropic",
    "ChatCompletion",
}


def main() -> int:
    files = sorted(
        path
        for path in BROKER_DIR.glob(f"{R6_PREFIX}*.py")
        if path.is_file()
    )
    errors: list[str] = []
    if not files:
        errors.append("R6 authority checker found no alpaca_paper_* modules")
    for path in files:
        errors.extend(_scan(path))

    gateway = BROKER_DIR / APPROVED_NETWORK_FILE
    if gateway.is_file():
        text = gateway.read_text(encoding="utf-8")
        if f'ALPACA_PAPER_TRADING_HOST = "{PAPER_HOST}"' not in text:
            errors.append("gateway: exact PAPER host constant is missing")
        if f'ALPACA_LIVE_TRADING_HOST = "{LIVE_HOST}"' not in text:
            errors.append("gateway: explicit LIVE deny constant is missing")
        if "enabled: bool = False" not in text:
            errors.append("gateway: disabled-by-default config contract is missing")
        if text.count(LIVE_HOST) != 1:
            errors.append(
                "gateway: LIVE host literal must appear exactly once as the deny constant"
            )
    else:
        errors.append("gateway: approved PAPER attestation module is missing")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"AUTO-TRADE R6 PAPER authority boundary: PASS ({CURRENT_PHASE})")
    return 0


def _scan(path: Path) -> list[str]:
    errors: list[str] = []
    rel = path.relative_to(ROOT)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(rel))
    network_allowed = path.name == APPROVED_NETWORK_FILE

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_import(alias.name):
                    errors.append(f"{rel}:{node.lineno}: forbidden authority import {alias.name}")
                if not network_allowed and _network_import(alias.name):
                    errors.append(
                        f"{rel}:{node.lineno}: networking is forbidden outside {APPROVED_NETWORK_FILE}: {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_import(module):
                errors.append(f"{rel}:{node.lineno}: forbidden authority import {module}")
            if not network_allowed and _network_import(module):
                errors.append(
                    f"{rel}:{node.lineno}: networking is forbidden outside {APPROVED_NETWORK_FILE}: {module}"
                )
            for alias in node.names:
                if alias.name in FORBIDDEN_EXECUTION_SYMBOLS:
                    errors.append(
                        f"{rel}:{node.lineno}: forbidden AI/execution symbol {alias.name}"
                    )
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS_PRE_SUBMIT:
                errors.append(
                    f"{rel}:{node.lineno}: external write call {name} forbidden in {CURRENT_PHASE}"
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == LIVE_HOST:
                if not (
                    path.name == APPROVED_NETWORK_FILE
                    and isinstance(getattr(node, "parent", None), ast.Assign)
                ):
                    # Parent links aren't installed; exact literal-count validation
                    # below provides the structural gateway exception. Other files
                    # remain unconditionally forbidden here.
                    if path.name != APPROVED_NETWORK_FILE:
                        errors.append(
                            f"{rel}:{node.lineno}: LIVE Trading API host literal is forbidden"
                        )
            if node.value.startswith("https://") or node.value.startswith("wss://"):
                if path.name != APPROVED_NETWORK_FILE:
                    errors.append(
                        f"{rel}:{node.lineno}: endpoint literal forbidden outside approved gateway"
                    )
                elif LIVE_HOST in node.value:
                    errors.append(
                        f"{rel}:{node.lineno}: LIVE Trading API endpoint is forbidden"
                    )

    return errors


def _forbidden_import(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )


def _network_import(module: str) -> bool:
    return module.split(".")[0] in NETWORK_ROOTS


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
