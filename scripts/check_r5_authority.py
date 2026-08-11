from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
R5_FILES = (
    ROOT / "src/autotrade/research/streaming.py",
    ROOT / "src/autotrade/research/stream_transport.py",
    ROOT / "src/autotrade/research/shadow.py",
    ROOT / "src/autotrade/research/forward.py",
)

FORBIDDEN_ABSOLUTE_IMPORT_PREFIXES = (
    "autotrade.oms",
    "autotrade.engine",
    "autotrade.brokers",
    "autotrade.bootstrap",
    "autotrade.safety",
    "autotrade.state",
    "autotrade.persistence",
    "autotrade.reconciliation",
    "autotrade.portfolio_manager",
    "autotrade.risk_state",
    "autotrade.domain",
    "autotrade.research.splits",
    "autotrade.research.validation",
    "autotrade.research.trials",
    "autotrade.research.tournament",
    "autotrade.research.registry",
)
FORBIDDEN_RELATIVE_RESEARCH_MODULES = {
    "splits",
    "validation",
    "trials",
    "tournament",
    "registry",
}
FORBIDDEN_SYMBOLS = {
    "OrderIntent",
    "OrderRecord",
    "RiskDecision",
    "PortfolioSnapshot",
    "SafetyKernel",
    "OMS",
}
FORBIDDEN_CALLS = {
    "send",
    "submit",
    "submit_order",
    "place_order",
    "execute_order",
    "cancel_order",
    "replace_order",
    "send_order",
    "create_order",
}
FORBIDDEN_LITERAL_FRAGMENTS = {
    "FINAL_HOLDOUT",
    "paper-api.alpaca",
    "api.alpaca.markets",
    "/v2/orders",
    "trade_updates",
}
NETWORK_FORBIDDEN_OUTSIDE_STREAM = {
    "socket",
    "ssl",
    "http",
    "urllib",
    "websocket",
    "websockets",
}
STREAM_NETWORK_FILES = {"streaming.py", "stream_transport.py"}


def main() -> int:
    errors: list[str] = []
    for path in R5_FILES:
        if not path.is_file():
            errors.append(f"{path.relative_to(ROOT)}: required R5 module is missing")
            continue
        errors.extend(_scan_path(path))

    streaming_source = (ROOT / "src/autotrade/research/streaming.py").read_text(encoding="utf-8")
    if "data-stream.binance.vision" not in streaming_source:
        errors.append("streaming.py: exact Binance market-data-only host is missing")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("AUTO-TRADE R5 stream/shadow/forward authority boundary: PASS")
    return 0


def _scan_path(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        rel: Path | str = path.relative_to(ROOT)
    except ValueError:
        rel = path
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(rel))
    is_stream_network = path.name in STREAM_NETWORK_FILES

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_absolute_import(alias.name):
                    errors.append(f"{rel}:{node.lineno}: forbidden R5 import {alias.name}")
                if not is_stream_network and _network_import(alias.name):
                    errors.append(
                        f"{rel}:{node.lineno}: shadow/forward evidence cannot import network module {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and _forbidden_absolute_import(module):
                errors.append(f"{rel}:{node.lineno}: forbidden R5 import {module}")
            if node.level > 0 and module.split(".")[0] in FORBIDDEN_RELATIVE_RESEARCH_MODULES:
                errors.append(
                    f"{rel}:{node.lineno}: R5 cannot import holdout/research-selection module .{module}"
                )
            if not is_stream_network and _network_import(module):
                errors.append(
                    f"{rel}:{node.lineno}: shadow/forward evidence cannot import network module {module}"
                )
            for alias in node.names:
                if alias.name in FORBIDDEN_SYMBOLS:
                    errors.append(
                        f"{rel}:{node.lineno}: R5 references execution-capable symbol {alias.name}"
                    )
        elif isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name in FORBIDDEN_CALLS:
                errors.append(
                    f"{rel}:{node.lineno}: R5 calls execution-like/outbound method {call_name}"
                )
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_SYMBOLS:
            errors.append(f"{rel}:{node.lineno}: R5 references forbidden symbol {node.id}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for fragment in FORBIDDEN_LITERAL_FRAGMENTS:
                if fragment in node.value:
                    errors.append(
                        f"{rel}:{node.lineno}: R5 contains forbidden authority/holdout literal {fragment}"
                    )
            if not is_stream_network and ("wss://" in node.value or "https://" in node.value):
                errors.append(
                    f"{rel}:{node.lineno}: shadow/forward evidence cannot contain network endpoints"
                )

    return errors


def _forbidden_absolute_import(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_ABSOLUTE_IMPORT_PREFIXES
    )


def _network_import(module: str) -> bool:
    root = module.split(".")[0]
    return root in NETWORK_FORBIDDEN_OUTSIDE_STREAM


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
