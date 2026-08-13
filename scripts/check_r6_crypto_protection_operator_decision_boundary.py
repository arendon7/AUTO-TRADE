from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_protection_operator_decision.py"
SRC = ROOT / "src/autotrade"
MAC_SURFACES = (
    ROOT / "scripts/mac_dashboard.py",
    ROOT / "scripts/mac_crypto_dashboard.py",
    ROOT / "scripts/mac_crypto_paper_rehearsal.py",
    ROOT / "web/mac_multi_asset.html",
    ROOT / "web/mac_crypto_dashboard.html",
    ROOT / "ABRIR_AUTO_TRADE.command",
    ROOT / "ABRIR_CRYPTO_PAPER.command",
)
NETWORK_ROOTS = {"http", "urllib", "socket", "ssl", "requests", "httpx", "aiohttp", "websockets"}
REQUIRED = (
    "class CryptoProtectionOperatorDecisionContext:",
    "PreparedCryptoProtectionPackage",
    "prepared_package_hash",
    "entry_reconciliation_fingerprint",
    "risk_decision_fingerprint",
    "attempt_id",
    "class SQLiteCryptoProtectionOperatorDecisionRegistry:",
    "record_operator_approval(",
    "CryptoProtectionOperatorDecisionStatus.ISSUED",
    "CryptoProtectionOperatorDecisionStatus.CONSUMED",
    "previous_event_hash",
    "event_hash",
    "protection preparation already has human authority",
    "protection decision attempt binding mismatch",
    "protection decision was consumed by another attempt",
)
FORBIDDEN = (
    "alpaca_paper_crypto_writer",
    "alpaca_paper_crypto_pre_io",
    "alpaca_paper_crypto_execution_bridge",
    "alpaca_paper_crypto_execution_simulation",
    "HttpsAlpacaPaperCryptoWriteTransport",
    "AlpacaPaperCryptoWriter",
    "CryptoPaperWriterConfig",
    "APCA-API-KEY-ID",
    "APCA-API-SECRET-KEY",
    "os.environ",
    "os.getenv",
    "api.alpaca.markets",
)


def fail(message: str) -> None:
    print(f"crypto protection operator boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if not TARGET.is_file():
        fail("protection operator authority module is missing")
    text = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(TARGET))

    for token in REQUIRED:
        if token not in text:
            fail(f"required protection human-authority anchor missing: {token}")
    for token in FORBIDDEN:
        if token in text:
            fail(f"protection human authority contains forbidden write/network token: {token}")

    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        for module in modules:
            if module.split(".", 1)[0] in NETWORK_ROOTS:
                fail(f"protection human authority imports network stack: {module}")
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in {"post", "send", "write", "submit_once", "stage_external_submission", "urlopen"}:
                fail(f"protection human authority contains execution call: {name}")

    # No production caller may mint this human authority until an explicit,
    # separately certified human interface exists.
    for path in SRC.rglob("*.py"):
        if path == TARGET:
            continue
        source = path.read_text(encoding="utf-8")
        if "alpaca_paper_crypto_protection_operator_decision" in source and "record_operator_approval(" in source:
            fail(f"unexpected production protection approval caller: {path.relative_to(ROOT)}")

    for path in MAC_SURFACES:
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        for token in (
            "alpaca_paper_crypto_protection_operator_decision",
            "SQLiteCryptoProtectionOperatorDecisionRegistry",
            "record_operator_approval(",
        ):
            if token in source:
                fail(f"Mac/user-facing surface leaked protection approval authority: {path.name}: {token}")

    print(
        "crypto protection operator boundary: PASS — separate immutable protection package/attempt binding; "
        "append-only tamper-evident ISSUED->CONSUMED human authority; no writer/network; Mac disconnected"
    )
    return 0


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
