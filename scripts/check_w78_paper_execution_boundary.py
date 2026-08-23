from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "brokers" / "paper_execution.py"
FORBIDDEN_IMPORT_ROOTS = {
    "http",
    "socket",
    "urllib",
    "requests",
    "aiohttp",
    "websockets",
}
FORBIDDEN_AUTOTRADE_IMPORT_FRAGMENTS = {
    "alpaca",
    "paper_close_writer",
    "external_paper",
    "real_paper",
}


def fail(message: str) -> None:
    raise SystemExit(f"W78 PAPER EXECUTION BOUNDARY FAIL: {message}")


def main() -> None:
    if not TARGET.is_file():
        fail("paper_execution.py is missing")
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in FORBIDDEN_IMPORT_ROOTS:
                    fail(f"forbidden network import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".", 1)[0] in FORBIDDEN_IMPORT_ROOTS:
                fail(f"forbidden network import: {module}")
            lowered = module.lower()
            if module.startswith("autotrade") and any(
                fragment in lowered for fragment in FORBIDDEN_AUTOTRADE_IMPORT_FRAGMENTS
            ):
                fail(f"paper execution model imported broker-write authority: {module}")

    required_text = (
        "class DeterministicPaperExecutionBroker",
        "class PaperExecutionConfig",
        "no-network PAPER execution broker",
    )
    for text in required_text:
        if text not in source:
            fail(f"required fail-closed marker missing: {text}")

    forbidden_text = (
        "HTTPSConnection",
        "http://",
        "https://",
        "APCA-API-KEY-ID",
        "APCA-API-SECRET-KEY",
        "R7_CLOSE_PAPER_WRITE",
        "LIVE_TRADING",
    )
    for text in forbidden_text:
        if text in source:
            fail(f"forbidden execution-authority marker present: {text}")

    print("W78 PAPER EXECUTION BOUNDARY PASS")


if __name__ == "__main__":
    try:
        main()
    except SyntaxError as exc:
        print(f"W78 PAPER EXECUTION BOUNDARY FAIL: syntax error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
