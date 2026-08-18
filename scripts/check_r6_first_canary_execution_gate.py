from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
ATTEMPT = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_first_canary_attempt.py"
ORCHESTRATOR = ROOT / "src/autotrade/first_canary_execution_gate.py"
PREPARE = ROOT / "scripts/mac_crypto_first_canary_prepare.py"
APPROVE = ROOT / "scripts/mac_crypto_first_canary_approval.py"
NETWORK_ROOTS = {"http", "urllib", "socket", "ssl", "requests", "httpx", "aiohttp", "websocket", "websockets"}
GENERIC_MAC = (
    ROOT / "scripts/mac_dashboard.py",
    ROOT / "scripts/mac_crypto_dashboard.py",
    ROOT / "web/mac_multi_asset.html",
    ROOT / "web/mac_crypto_dashboard.html",
    ROOT / "ABRIR_AUTO_TRADE.command",
    ROOT / "ABRIR_CRYPTO_PAPER.command",
)


def fail(message: str) -> None:
    print(f"first-canary execution gate: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def main() -> int:
    for path in (ATTEMPT, ORCHESTRATOR, PREPARE, APPROVE):
        if not path.is_file():
            fail(f"missing required gate file: {path.relative_to(ROOT)}")

    attempt = ATTEMPT.read_text(encoding="utf-8")
    orchestrator = ORCHESTRATOR.read_text(encoding="utf-8")
    prepare = PREPARE.read_text(encoding="utf-8")
    approve = APPROVE.read_text(encoding="utf-8")

    for path in (ATTEMPT, ORCHESTRATOR, APPROVE):
        roots = {module.split(".", 1)[0] for module in imports(path) if module}
        forbidden = roots & NETWORK_ROOTS
        if forbidden:
            fail(f"{path.name} imports forbidden direct network stack: {sorted(forbidden)}")

    for token in (
        'ATTEMPT_ID_RE = re.compile(r"^first-canary-[0-9a-f]{32}$")',
        'return self.attempt_root / "execution_started.json"',
        'return self.attempt_root / "execution_result.json"',
        'return self.attempt_root / "reconciliation_failure.json"',
        'return self.attempt_root / "reconciliation_pending.json"',
        'return self.attempt_root / "reconciliation.json"',
        "def assert_unexecuted(self) -> None:",
        "self.execution_started_path",
        "POST replay is forbidden",
        'path.open("x", encoding="utf-8")',
        "path.chmod(0o600)",
    ):
        if token not in attempt:
            fail(f"attempt workspace missing replay/integrity anchor: {token}")

    for token in (
        'TARGET_NOTIONAL = Decimal("2")',
        'MIN_NOTIONAL = Decimal("1")',
        'MAX_NOTIONAL = Decimal("5")',
        'EXPECTED_SYMBOL = CRYPTO_PAIR',
        'STRATEGY_ID = "R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION"',
        'os.environ.get(WRITE_ENV) == "ENABLED"',
        "SQLiteCryptoColdStartAuthorityProvider(core_runtime)",
        "authority_after.state_fingerprint != authority_before.state_fingerprint",
        "CryptoPaperCanaryCoordinator(oms=oms).prepare_entry(",
        '"operator_decision_recorded": False',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"credentials_persisted": False',
        '"live_trading": "BLOCKED"',
    ):
        if token not in prepare:
            fail(f"first-canary preparation missing fail-closed anchor: {token}")
    for forbidden in (
        "AlpacaPaperCryptoWriter",
        "HttpsAlpacaPaperCryptoWriteTransport",
        "submit_once(",
        ".post(",
        "api.alpaca.markets",
    ):
        if forbidden in prepare:
            fail(f"preparation leaked write/LIVE authority: {forbidden}")

    for token in (
        'ATTEMPT_PREFIX = "first-canary-"',
        'MAX_APPROVAL_TTL = timedelta(seconds=90)',
        'MIN_REMAINING_PACKAGE_LIFE = timedelta(seconds=5)',
        'EXPECTED_SYMBOL = "BTC/USD"',
        'os.environ.get(WRITE_ENV) == "ENABLED"',
        "secrets.compare_digest(confirmation, challenge)",
        "registry.record_operator_approval(",
        '"decision_consumed": False',
        '"reusable_for_uat": False',
        '"reusable_for_other_attempt": False',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"live_trading": "BLOCKED"',
    ):
        if token not in approve:
            fail(f"execution approval issuer missing isolation anchor: {token}")
    for forbidden in (
        "AlpacaPaperCredentials",
        "AlpacaPaperCryptoWriter",
        "HttpsAlpacaPaperCryptoWriteTransport",
        ".consume(",
        "submit_once(",
        ".post(",
    ):
        if forbidden in approve:
            fail(f"execution approval issuer leaked execution authority: {forbidden}")

    for token in (
        'MAX_NOTIONAL = Decimal("5")',
        'MIN_NOTIONAL = Decimal("1")',
        "inputs.attempt.assert_unexecuted()",
        "CryptoColdStartFinalWritePhase.PRE_CONSUME",
        "checkpoint_registry.record_pre_consume(pre_consume)",
        "bridge.stage_after_checkpoint(",
        '"operator_decision_consumed": True',
        '"retry_forbidden": True',
        "inputs.attempt.execution_started_path",
        "ColdStartFinalGuardedCryptoEntryTransport(",
        "delegate=delegate",
        "AlpacaPaperCryptoWriterConfig(enabled=True)",
        "writer.submit_once(",
        "CryptoPaperWriterAmbiguous",
        "reconciler.reconcile(",
        "def _execution_outcome_status(",
        '"RECONCILED_FINAL"',
        '"RECONCILIATION_PENDING_NO_RETRY"',
        '"CRYPTO_PAPER_FIRST_CANARY_RECONCILIATION_FAILURE_NO_RETRY"',
        '"CRYPTO_PAPER_FIRST_CANARY_RECONCILIATION_PENDING_ORDER_404_NO_RETRY"',
        '"CRYPTO_PAPER_FIRST_CANARY_RECONCILIATION_PENDING_ORDER_OPEN_NO_RETRY"',
        '"CRYPTO_PAPER_FIRST_CANARY_RECONCILED_FINAL_NO_RETRY"',
        "inputs.attempt.reconciliation_failure_path",
        "inputs.attempt.reconciliation_pending_path",
        "inputs.attempt.reconciliation_path",
        '"persisted_final_resolution": False',
        '"retry_post": False',
        '"reconciliation_retry_get_only": True',
        '"live_trading": "BLOCKED"',
    ):
        if token not in orchestrator:
            fail(f"first-canary orchestrator missing authority/recovery anchor: {token}")
    if orchestrator.count("writer.submit_once(") != 1:
        fail("first-canary orchestrator must have exactly one writer invocation call site")
    for forbidden in (
        "HttpsAlpacaPaperCryptoWriteTransport",
        "http.client",
        "api.alpaca.markets",
        ".post(",
        "R6_EXTERNAL_PAPER_WRITE",
        "APCA_API_SECRET_KEY",
    ):
        if forbidden in orchestrator:
            fail(f"first-canary orchestrator leaked raw transport/LIVE authority: {forbidden}")

    ordered = (
        orchestrator.find("checkpoint_registry.record_pre_consume(pre_consume)"),
        orchestrator.find("stage = bridge.stage_after_checkpoint("),
        orchestrator.find("path=inputs.attempt.execution_started_path"),
        orchestrator.find("writer.submit_once("),
        orchestrator.find("reconciler.reconcile("),
    )
    if any(index < 0 for index in ordered) or tuple(sorted(ordered)) != ordered:
        fail("required sequence is not PRE_CONSUME -> OMS -> replay latch -> writer -> reconciliation")

    final_status = orchestrator.find('"CRYPTO_PAPER_FIRST_CANARY_RECONCILED_FINAL_NO_RETRY"')
    final_path = orchestrator.find("inputs.attempt.reconciliation_path", final_status)
    pending_status = orchestrator.find('"CRYPTO_PAPER_FIRST_CANARY_RECONCILIATION_PENDING_ORDER_OPEN_NO_RETRY"')
    pending_path = orchestrator.find("inputs.attempt.reconciliation_pending_path", pending_status)
    if not 0 <= final_status < final_path:
        fail("terminal reconciliation is not bound to immutable reconciliation.json")
    if not 0 <= pending_status < pending_path:
        fail("open reconciliation is not bound to reconciliation_pending.json")

    for path in GENERIC_MAC:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in (
            "first_canary_execution_gate",
            "execute_first_canary_once",
            "AlpacaPaperCryptoWriter",
            "HttpsAlpacaPaperCryptoWriteTransport",
        ):
            if token in text:
                fail(f"generic Mac surface acquired first-canary write authority: {path.name}: {token}")

    print(
        "first-canary execution gate: PASS — PAPER BTC/USD USD1-5; bounded 90s human approval with 5s remaining-life floor; "
        "PRE_CONSUME -> OMS -> durable replay latch -> UNKNOWN/PRE_IO -> injected delegate -> reconciliation; "
        "FINAL/PENDING/FAILURE evidence separated; no raw network/LIVE authority; generic Mac remains write-disabled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
