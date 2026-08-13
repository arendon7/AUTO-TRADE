from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_protection_final_guard.py"
MAC_SURFACES = (
    ROOT / "scripts/mac_dashboard.py",
    ROOT / "scripts/mac_crypto_dashboard.py",
    ROOT / "scripts/mac_crypto_paper_rehearsal.py",
    ROOT / "web/mac_multi_asset.html",
    ROOT / "web/mac_crypto_dashboard.html",
)
NETWORK_ROOTS = {"http", "urllib", "socket", "ssl", "requests", "httpx", "aiohttp", "websockets"}
REQUIRED = (
    "class CryptoProtectionFinalWritePhase(str, Enum):",
    'PRE_CONSUME = "PRE_CONSUME"',
    'PRE_IO = "PRE_IO"',
    "state.status is not CryptoProtectionOperatorDecisionStatus.ISSUED",
    "snapshot.state.status is not CryptoLifecycleStatus.PROTECTION_PREPARED",
    "snapshot.state.protection_attempt_count != 0",
    "oms_order.status is not OrderStatus.VALIDATED",
    "decision_state.status is not CryptoProtectionOperatorDecisionStatus.CONSUMED",
    "snapshot.state.status is not CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN",
    "snapshot.state.protection_attempt_count != 1",
    "oms_order.status is not OrderStatus.SUBMITTING",
    "position.quantity != package.confirmed_net_long_quantity",
    "previous_attestation.attestation_hash",
)
FORBIDDEN = (
    "alpaca_paper_crypto_writer",
    "alpaca_paper_crypto_pre_io",
    "HttpsAlpacaPaperCryptoWriteTransport",
    "AlpacaPaperCryptoWriter",
    "CryptoPaperWriterConfig",
    "APCA-API-KEY-ID",
    "APCA-API-SECRET-KEY",
    "api.alpaca.markets",
    "record_operator_approval(",
)


def fail(message: str) -> None:
    print(f"crypto protection final guard boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if not TARGET.is_file():
        fail("protection Final Freshness guard is missing")
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET))
    for token in REQUIRED:
        if token not in source:
            fail(f"required protection Final Freshness anchor missing: {token}")
    for token in FORBIDDEN:
        if token in source:
            fail(f"protection Final Freshness contains forbidden authority token: {token}")
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        for module in modules:
            if module.split(".", 1)[0] in NETWORK_ROOTS:
                fail(f"protection Final Freshness imports network stack: {module}")
        if isinstance(node, ast.Call) and _call_name(node.func) in {
            "post", "send", "write", "submit_once", "stage_external_submission", "urlopen"
        }:
            fail(f"protection Final Freshness contains execution call: {_call_name(node.func)}")
    for path in MAC_SURFACES:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "alpaca_paper_crypto_protection_final_guard" in text or "CryptoPaperProtectionFinalGuard" in text:
            fail(f"Mac leaked protection Final Freshness authority: {path.name}")
    print(
        "crypto protection final guard boundary: PASS — PRE_CONSUME requires ISSUED/PREPARED/VALIDATED; "
        "PRE_IO requires CONSUMED/UNKNOWN/SUBMITTING attempt=1; exact fresh position; no network/Mac authority"
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
