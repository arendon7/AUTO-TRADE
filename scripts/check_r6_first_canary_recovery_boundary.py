from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/first_canary_recovery.py"
FEE_AWARE = ROOT / "src/autotrade/first_canary_fee_aware_recovery.py"
ROTATED_CREDENTIAL = ROOT / "src/autotrade/first_canary_rotated_credential_recovery.py"
LIFECYCLE = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_lifecycle.py"
COLD_START_UNKNOWN = (
    ROOT
    / "src/autotrade/brokers/alpaca_paper_crypto_cold_start_unknown_recovery.py"
)
CLI = ROOT / "scripts/mac_crypto_first_canary_reconcile.py"
GENERIC_MAC = (
    ROOT / "scripts/mac_dashboard.py",
    ROOT / "scripts/mac_crypto_dashboard.py",
    ROOT / "ABRIR_AUTO_TRADE.command",
    ROOT / "ABRIR_CRYPTO_PAPER.command",
)
DIRECT_NETWORK_ROOTS = {
    "http",
    "urllib",
    "socket",
    "ssl",
    "requests",
    "httpx",
    "aiohttp",
    "websocket",
    "websockets",
}
FORBIDDEN_WRITE_TOKENS = (
    "AlpacaPaperCryptoWriter",
    "HttpsAlpacaPaperCryptoWriteTransport",
    "GuardedAlpacaPaperCryptoWriteTransport",
    "ColdStartFinalGuardedCryptoEntryTransport",
    "submit_once(",
    ".post(",
    'method="POST"',
    "method='POST'",
    "stage_external_submission",
    "stage_cold_start_external_submission",
    "api.alpaca.markets",
)


def fail(message: str) -> None:
    print(f"first-canary recovery boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def main() -> int:
    for path in (MODULE, FEE_AWARE, ROTATED_CREDENTIAL, LIFECYCLE, COLD_START_UNKNOWN, CLI):
        if not path.is_file():
            fail(f"missing required recovery surface: {path.relative_to(ROOT)}")

    for path in (MODULE, FEE_AWARE, ROTATED_CREDENTIAL, COLD_START_UNKNOWN, CLI):
        imports = _imports(path)
        roots = {module.split(".", 1)[0] for module in imports if module}
        forbidden_roots = roots & DIRECT_NETWORK_ROOTS
        if forbidden_roots:
            fail(
                f"{path.relative_to(ROOT)} imports direct network stack: {sorted(forbidden_roots)}"
            )
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_WRITE_TOKENS:
            if token in text:
                fail(
                    f"{path.relative_to(ROOT)} contains forbidden write authority: {token}"
                )

    module = MODULE.read_text(encoding="utf-8")
    for anchor in (
        "def recover_first_canary(",
        "attempt.execution_started_path",
        'started.get("retry_forbidden") is not True',
        'started.get("writer_invocation_permitted_once") is not True',
        'preparation.get("prepared_package")',
        '_required_text(package, "lifecycle_id")',
        "SQLiteCryptoColdStartExecutionAttemptRegistry(runtime)",
        "checkpoint.pre_consume.lifecycle_binding_hash != package_binding_hash",
        "AlpacaPaperCryptoReconciliationGateway(",
        "AlpacaPaperAccountGateway(",
        "AlpacaPaperFlatAccountGateway(",
        "CryptoColdStartUnknownRecoveryCoordinator().recover_entry(",
        "attempt.recovery_resolution_path",
        '"retry_post": False',
        '"recovery_get_only": True',
        '"credentials_persisted": False',
        '"live_trading": "BLOCKED"',
    ):
        if anchor not in module:
            fail(f"canonical recovery module missing anchor: {anchor}")
    sequence = (
        module.find("started = attempt.read(path=attempt.execution_started_path)"),
        module.find("checkpoint = checkpoint_registry.get(attempt_id)"),
        module.find("evidence = gateway.reconcile("),
    )
    if any(index < 0 for index in sequence) or tuple(sorted(sequence)) != sequence:
        fail(
            "recovery sequence must verify irreversible latch -> durable checkpoint -> GET reconciliation"
        )
    if module.count("gateway.reconcile(") != 1:
        fail("canonical recovery module must contain exactly one reconciliation GET call site")

    lifecycle = LIFECYCLE.read_text(encoding="utf-8")
    for anchor in (
        "def recover_entry_unknown_absence(",
        '"kind": "R6_CRYPTO_COLD_START_UNKNOWN_ORDER_404_RECOVERY"',
        "CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN",
        "state.entry_attempt_count != 1",
        "binding.entry_client_order_id != client_order_id",
        "CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED",
        "CryptoLifecycleStatus.FLAT_RECONCILED",
        '"retry_authorized": False',
        "return self._mutate(",
    ):
        if anchor not in lifecycle:
            fail(f"crypto lifecycle missing narrow UNKNOWN recovery transition: {anchor}")

    cold = COLD_START_UNKNOWN.read_text(encoding="utf-8")
    for anchor in (
        "class CryptoColdStartUnknownRecoveryCoordinator:",
        "CryptoColdStartExecutionAttemptCheckpoint",
        "CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN",
        "state.entry_attempt_count != 1",
        "CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED",
        "CryptoLifecycleStatus.FLAT_RECONCILED",
        "flat_account.clean_for_first_canary",
        "lifecycle.recover_entry_unknown_absence(",
        '"retry_authorized": False',
    ):
        if anchor not in cold:
            fail(f"cold-start UNKNOWN recovery missing anchor: {anchor}")
    if "._mutate(" in cold:
        fail(
            "cold-start UNKNOWN recovery may not call the lifecycle transaction primitive directly"
        )

    fee_aware = FEE_AWARE.read_text(encoding="utf-8")
    for anchor in (
        'MAX_RECEIVED_ASSET_FEE_RATE = Decimal("0.0025")',
        'POSITION_ROUNDING_TOLERANCE = Decimal("0.000000001")',
        "class FirstCanaryFeeAwareRecoveryLifecycle(base.SQLiteCryptoPaperLifecycle):",
        "confirmed_net_long_quantity > filled_quantity",
        "deficit > maximum",
        "canonical_recovery.SQLiteCryptoPaperLifecycle = FirstCanaryFeeAwareRecoveryLifecycle",
        "canonical_recovery.SQLiteCryptoPaperLifecycle = original",
    ):
        if anchor not in fee_aware:
            fail(f"fee-aware GET-only recovery missing fail-closed anchor: {anchor}")

    rotated = ROTATED_CREDENTIAL.read_text(encoding="utf-8")
    for anchor in (
        "class _SameAccountRecoveryCredentialAlias(AlpacaPaperCredentials):",
        "fresh_account = account_reader.attest_account(",
        "fresh_account.account_reference != checkpoint.pre_consume.account_reference",
        '"recovery_get_only": True',
        '"retry_post": False',
        '"capital_authority": "NONE"',
        '"credentials_persisted": False',
        '"live_trading": "BLOCKED"',
        "FirstCanaryCompactPositionReconciliationGateway(",
        "FirstCanaryRecoveryReadTransport(",
        "recover_first_canary_fee_aware(**kwargs)",
    ):
        if anchor not in rotated:
            fail(f"rotated-credential GET-only recovery missing anchor: {anchor}")
    if "AlpacaPaperCryptoReconciliationGateway(" in rotated:
        fail("rotated-credential recovery may not instantiate generic crypto reconciliation")

    cli = CLI.read_text(encoding="utf-8")
    for anchor in (
        "from autotrade.first_canary_rotated_credential_recovery import (",
        "recover_first_canary_with_safe_credential_rotation",
        'WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"',
        'os.environ.get(WRITE_ENV) == "ENABLED"',
        '"--allow-paper-recovery-read"',
        "recover_first_canary_with_safe_credential_rotation(",
        '"retry_post": False',
        '"recovery_get_only": True',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    ):
        if anchor not in cli:
            fail(f"GET-only recovery CLI missing anchor: {anchor}")
    for forbidden in (
        "AlpacaPaperCryptoReconciliationGateway",
        "recover_first_canary_fee_aware(",
    ):
        if forbidden in cli:
            fail(f"GET-only recovery CLI bypasses safe rotation/compact gateway: {forbidden}")

    for path in GENERIC_MAC:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in (
            "first_canary_recovery",
            "mac_crypto_first_canary_reconcile",
            "recover_first_canary",
        ):
            if token in text:
                fail(
                    f"generic Mac surface unexpectedly exposes recovery authority: {path.name}: {token}"
                )

    print(
        "first-canary recovery boundary: PASS — irreversible execution latch before GET truth; "
        "fee-aware adapter plus same-account rotated-key proof are limited to burned first-canary GET-only recovery; generic lifecycle remains strict; "
        "cold-start UNKNOWN resolves flat or halted with attempt=1; no writer/POST/raw network stack; "
        "recovery may repeat while pending and never authorizes POST retry"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
