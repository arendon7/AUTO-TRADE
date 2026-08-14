from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_protection_coordinator.py"
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
FORBIDDEN_IMPORT_FRAGMENTS = {
    "alpaca_paper_crypto_writer",
    "alpaca_paper_crypto_pre_io",
    "alpaca_paper_crypto_execution_bridge",
    "alpaca_paper_crypto_execution_simulation",
    "alpaca_paper_crypto_operator_decision",
    "openai",
    "anthropic",
    "autotrade.research",
}
FORBIDDEN_CALLS = {
    "post",
    "send",
    "write",
    "submit_once",
    "stage_external_submission",
    "record_operator_approval",
    "urlopen",
}
REQUIRED = (
    "class PreparedCryptoProtectionPackage:",
    "class CryptoPaperProtectionCoordinator:",
    "state.status is not CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED",
    "not broker.terminal or broker.filled_quantity <= 0",
    "position.quantity != state.confirmed_net_long_quantity",
    "intent.quantity != confirmed_net_long",
    "decision.risk_reducing is not True",
    "requested_protection_quantity=confirmed_net_long",
    "broker_order.quantity != confirmed_net_long",
    "broker_order.stop_price != stop_price or broker_order.limit_price != limit_price",
    "lifecycle.prepare_protection(",
    "lifecycle_state.status is not CryptoLifecycleStatus.PROTECTION_PREPARED",
    "lifecycle_state.protection_attempt_count != 0",
    '"network_write_authorized": False',
    '"next_action": "OPERATOR_DECISION_REQUIRED"',
)


def fail(message: str) -> None:
    print(f"crypto protection boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if not TARGET.is_file():
        fail("offline protection coordinator is missing")
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET))

    for anchor in REQUIRED:
        if anchor not in source:
            fail(f"required exact-position protection anchor missing: {anchor}")

    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        for module in modules:
            root = module.split(".", 1)[0]
            if root in NETWORK_ROOTS:
                fail(f"offline protection coordinator imports network stack: {module}")
            if any(fragment in module for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                fail(f"offline protection coordinator imports execution/AI authority: {module}")
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                fail(f"offline protection coordinator contains forbidden authority call: {name}")

    for token in (
        "APCA-API-KEY-ID",
        "APCA-API-SECRET-KEY",
        "secret_key",
        "key_id",
        "CryptoPaperWriterConfig(enabled=True)",
        "HttpsAlpacaPaperCryptoWriteTransport",
        "api.alpaca.markets",
    ):
        if token in source:
            fail(f"offline protection coordinator contains credential/network token: {token}")

    for path in MAC_SURFACES:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in (
            "alpaca_paper_crypto_protection_coordinator",
            "CryptoPaperProtectionCoordinator",
            "PreparedCryptoProtectionPackage",
            "prepare_protection(",
        ):
            if token in text:
                fail(f"Mac/user-facing surface leaked protection preparation authority: {path.name}: {token}")

    print(
        "crypto protection boundary: PASS — terminal entry reconciliation required; "
        "SELL STOP_LIMIT quantity equals confirmed net long exactly; OMS remains VALIDATED; "
        "operator decision required; no credentials/network/writer; Mac remains disconnected"
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
