from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_execution_attempt.py"
USER_FACING = (
    ROOT / "scripts/mac_dashboard.py",
    ROOT / "scripts/mac_crypto_dashboard.py",
    ROOT / "scripts/mac_crypto_paper_rehearsal.py",
    ROOT / "web/mac_multi_asset.html",
    ROOT / "web/mac_crypto_dashboard.html",
    ROOT / "ABRIR_AUTO_TRADE.command",
    ROOT / "ABRIR_CRYPTO_PAPER.command",
)

FORBIDDEN_IMPORT_ROOTS = {
    "http",
    "socket",
    "ssl",
    "urllib",
    "requests",
    "aiohttp",
}
FORBIDDEN_IMPORT_FRAGMENTS = {
    "alpaca_paper_crypto_writer",
    "alpaca_paper_gateway",
    "alpaca_paper_crypto_reconciliation",
}
FORBIDDEN_CALL_NAMES = {
    "post",
    "request",
    "urlopen",
    "HTTPSConnection",
    "submit_once",
}
FORBIDDEN_TEXT = (
    "APCA-API-KEY-ID",
    "APCA-API-SECRET-KEY",
    "secret_key",
    "key_id",
    "CryptoPaperWriterConfig(enabled=True)",
)
USER_FACING_FORBIDDEN = (
    "alpaca_paper_crypto_execution_attempt",
    "SQLiteCryptoExecutionAttemptRegistry",
    "record_pre_consume(",
)


def fail(message: str) -> None:
    print(f"crypto execution attempt boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if not TARGET.is_file():
        fail("durable execution-attempt registry is missing")
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET))

    if "CryptoFinalWritePhase.PRE_CONSUME" not in source:
        fail("registry is not explicitly PRE_CONSUME-only")
    if "package_hash TEXT NOT NULL UNIQUE" not in source:
        fail("package uniqueness is not durable")
    if "preparation_hash TEXT NOT NULL UNIQUE" not in source:
        fail("preparation uniqueness is not durable")
    if "record_hash TEXT NOT NULL UNIQUE" not in source:
        fail("tamper-evident record hash is not durable")
    if "network authority" not in source:
        fail("registry does not document non-authorizing semantics")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    fail(f"network import forbidden: {alias.name}")
                if any(fragment in alias.name for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                    fail(f"execution/network authority import forbidden: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            if root in FORBIDDEN_IMPORT_ROOTS:
                fail(f"network import forbidden: {module}")
            if any(fragment in module for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                fail(f"execution/network authority import forbidden: {module}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in FORBIDDEN_CALL_NAMES:
                fail(f"network/writer call forbidden: {name}")

    for token in FORBIDDEN_TEXT:
        if token in source:
            fail(f"credential/network token forbidden: {token}")

    for path in USER_FACING:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in USER_FACING_FORBIDDEN:
            if token in text:
                fail(f"user-facing Mac surface leaked execution-attempt authority: {path.name}: {token}")

    print(
        "crypto execution attempt boundary: PASS — durable PRE_CONSUME checkpoint; "
        "same package/preparation single-attempt binding; tamper-evident; "
        "no credentials/network/writer authority; Mac remains disconnected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
