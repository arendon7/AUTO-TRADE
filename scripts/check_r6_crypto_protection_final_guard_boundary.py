from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_protection_final_guard.py"
ATTEMPT = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_protection_execution_attempt.py"
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
    "fresh_account: AlpacaPaperAccountAttestation",
    "account.account_reference != package.account_reference",
    "account.credential_reference != package.credential_reference",
    "account.source_host != ALPACA_PAPER_TRADING_HOST",
    "account.source_path != ALPACA_PAPER_ACCOUNT_PATH",
    "position.credential_reference != package.credential_reference",
    "position.credential_reference != account.credential_reference",
    '"fresh_account_fingerprint": fresh_account.fingerprint',
    '"position_credential_reference": fresh_position.credential_reference',
    "decision_state.status is not CryptoProtectionOperatorDecisionStatus.ISSUED",
    "snapshot.state.status is not CryptoLifecycleStatus.PROTECTION_PREPARED",
    "snapshot.state.protection_attempt_count != 0",
    "oms_order.status is not OrderStatus.VALIDATED",
    "decision_state.status is not CryptoProtectionOperatorDecisionStatus.CONSUMED",
    "snapshot.state.status is not CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN",
    "snapshot.state.protection_attempt_count != 1",
    "oms_order.status is not OrderStatus.SUBMITTING",
    "position.quantity != package.confirmed_net_long_quantity",
    "previous.account_reference != package.account_reference",
    "previous.credential_reference != package.credential_reference",
    "previous.position_credential_reference != package.credential_reference",
    "previous_attestation.attestation_hash",
)
ATTEMPT_REQUIRED = (
    "class CryptoProtectionExecutionAttemptCheckpoint:",
    "class SQLiteCryptoProtectionExecutionAttemptRegistry:",
    "CryptoProtectionFinalWritePhase.PRE_CONSUME",
    "CryptoLifecycleStatus.PROTECTION_PREPARED",
    "self.pre_consume.protection_attempt_count != 0",
    "self.pre_consume.oms_order_status is not OrderStatus.VALIDATED",
    '("account_reference", self.pre_consume.account_reference)',
    '("credential_reference", self.pre_consume.credential_reference)',
    '("fresh_account_fingerprint", self.pre_consume.fresh_account_fingerprint)',
    '("position_credential_reference", self.pre_consume.position_credential_reference)',
    '"account_reference": attestation.account_reference',
    '"credential_reference": attestation.credential_reference',
    '"fresh_account_fingerprint": attestation.fresh_account_fingerprint',
    '"position_credential_reference": attestation.position_credential_reference',
    "package_hash TEXT NOT NULL UNIQUE",
    "operator_decision_hash TEXT NOT NULL UNIQUE",
    "record_hash TEXT NOT NULL UNIQUE",
    "WHERE attempt_id = ? OR package_hash = ? OR operator_decision_hash = ?",
    '"kind": "R6_CRYPTO_PROTECTION_EXECUTION_ATTEMPT"',
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
ATTEMPT_FORBIDDEN = (
    "alpaca_paper_crypto_writer",
    "alpaca_paper_crypto_pre_io",
    "AlpacaPaperCryptoWriter",
    "HttpsAlpacaPaperCryptoWriteTransport",
    "AlpacaPaperCredentials",
    "APCA-API-KEY-ID",
    "APCA-API-SECRET-KEY",
    "stage_external_submission(",
    "submit_once(",
)


def fail(message: str) -> None:
    print(f"crypto protection final guard boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _assert_offline_source(path: Path, *, label: str, required: tuple[str, ...], forbidden: tuple[str, ...]) -> None:
    if not path.is_file():
        fail(f"{label} is missing")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for token in required:
        if token not in source:
            fail(f"required {label} anchor missing: {token}")
    for token in forbidden:
        if token in source:
            fail(f"{label} contains forbidden authority token: {token}")
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        for module in modules:
            if module.split(".", 1)[0] in NETWORK_ROOTS:
                fail(f"{label} imports network stack: {module}")
        if isinstance(node, ast.Call) and _call_name(node.func) in {
            "post", "send", "write", "submit_once", "stage_external_submission", "urlopen"
        }:
            fail(f"{label} contains execution call: {_call_name(node.func)}")


def main() -> int:
    _assert_offline_source(
        TARGET,
        label="protection Final Freshness",
        required=REQUIRED,
        forbidden=FORBIDDEN,
    )
    _assert_offline_source(
        ATTEMPT,
        label="protection PRE_CONSUME checkpoint",
        required=ATTEMPT_REQUIRED,
        forbidden=ATTEMPT_FORBIDDEN,
    )
    for path in MAC_SURFACES:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "alpaca_paper_crypto_protection_final_guard" in text or "CryptoPaperProtectionFinalGuard" in text:
            fail(f"Mac leaked protection Final Freshness authority: {path.name}")
        if "alpaca_paper_crypto_protection_execution_attempt" in text or "SQLiteCryptoProtectionExecutionAttemptRegistry" in text:
            fail(f"Mac leaked protection execution checkpoint authority: {path.name}")
    print(
        "crypto protection final guard boundary: PASS — PRE_CONSUME requires ISSUED/PREPARED/VALIDATED plus fresh same-account/credential evidence and durable no-network checkpoint; "
        "PRE_IO requires CONSUMED/UNKNOWN/SUBMITTING attempt=1 with same account-bound position; no network/Mac authority"
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
