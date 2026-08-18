from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/autotrade"
PRE_IO = SRC / "brokers/alpaca_paper_crypto_pre_io.py"
BRIDGE = SRC / "brokers/alpaca_paper_crypto_execution_bridge.py"
SIMULATION = SRC / "brokers/alpaca_paper_crypto_execution_simulation.py"
FIRST_CANARY_GATE = SRC / "first_canary_execution_gate.py"
WRITER = SRC / "brokers/alpaca_paper_crypto_writer.py"
MAC_SURFACES = (
    ROOT / "scripts/mac_dashboard.py",
    ROOT / "scripts/mac_crypto_dashboard.py",
    ROOT / "scripts/mac_crypto_paper_rehearsal.py",
    ROOT / "web/mac_multi_asset.html",
    ROOT / "web/mac_crypto_dashboard.html",
    ROOT / "ABRIR_AUTO_TRADE.command",
    ROOT / "ABRIR_CRYPTO_PAPER.command",
)
NETWORK_ROOTS = {"http", "urllib", "socket", "ssl", "requests", "httpx", "aiohttp"}


def fail(message: str) -> None:
    print(f"crypto execution simulation boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def main() -> int:
    for path in (PRE_IO, BRIDGE, SIMULATION, FIRST_CANARY_GATE, WRITER):
        if not path.is_file():
            fail(f"missing required file: {path.relative_to(ROOT)}")

    pre = PRE_IO.read_text(encoding="utf-8")
    bridge = BRIDGE.read_text(encoding="utf-8")
    sim = SIMULATION.read_text(encoding="utf-8")
    first_canary = FIRST_CANARY_GATE.read_text(encoding="utf-8")

    for path in (PRE_IO, BRIDGE, SIMULATION, FIRST_CANARY_GATE):
        imports = _imports(path)
        roots = {module.split(".", 1)[0] for module in imports if module}
        forbidden = roots & NETWORK_ROOTS
        if forbidden:
            fail(f"{path.name} imports direct network stack: {sorted(forbidden)}")

    required_pre = (
        "FinalGuardedCryptoEntryTransport",
        "DeterministicCryptoPaperSimulationTransport",
        "self._authorizer()",
        "CryptoFinalWritePhase.PRE_IO",
        "CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN",
        "attestation.entry_attempt_count != 1",
        "attestation.client_order_id != client_order_id",
        "self._delegate.post(",
        '"simulation-paper-key"',
        '"simulation-paper-secret"',
    )
    for token in required_pre:
        if token not in pre:
            fail(f"PRE_IO interlock missing contract token: {token}")
    if pre.find("self._authorizer()") > pre.find("self._delegate.post("):
        fail("PRE_IO authorizer is not before delegated transport")
    if "HttpsAlpacaPaperCryptoWriteTransport" in pre:
        fail("PRE_IO interlock may not construct direct HTTPS transport")

    required_bridge = (
        "class CryptoPaperExecutionBridge:",
        "checkpoint: CryptoExecutionAttemptCheckpoint",
        "operator_registry.consume(",
        "self._oms.stage_external_submission(",
        "checkpoint.package_hash != package.package_hash",
        "checkpoint.operator_decision_hash != operator_decision.decision_hash",
        "consume_instant > stage_instant",
    )
    for token in required_bridge:
        if token not in bridge:
            fail(f"crypto execution bridge missing contract token: {token}")
    for forbidden in (
        "AlpacaPaperCryptoWriter",
        "FinalGuardedCryptoEntryTransport",
        "HttpsAlpacaPaperCryptoWriteTransport",
        ".post(",
        "submit_once(",
    ):
        if forbidden in bridge:
            fail(f"crypto execution bridge contains forbidden write authority: {forbidden}")

    required_sim = (
        "PRE_CONSUME -> durable checkpoint -> execution bridge consumes human",
        "decision -> OMS SUBMITTING -> writer persists ENTRY_SUBMISSION_UNKNOWN ->",
        "PRE_IO -> one simulated POST",
        "self._attempts.record_pre_consume(pre_consume)",
        "self._execution_bridge.stage_after_checkpoint(",
        "DeterministicCryptoPaperSimulationTransport()",
        "FinalGuardedCryptoEntryTransport(",
        "AlpacaPaperCryptoWriterConfig(enabled=True)",
        "writer.submit_once(",
        "CryptoExecutionSimulationReconcileOnly",
        "already UNKNOWN; same-attempt restart is reconciliation-only",
    )
    for token in required_sim:
        if token not in sim:
            fail(f"simulation coordinator missing contract token: {token}")
    ordered = (
        sim.find("self._attempts.record_pre_consume(pre_consume)"),
        sim.find("stage_result = self._execution_bridge.stage_after_checkpoint("),
        sim.find("writer.submit_once("),
    )
    if any(index < 0 for index in ordered) or tuple(sorted(ordered)) != ordered:
        fail("simulation authority sequence is not checkpoint -> execution bridge -> writer")
    for forbidden in (
        "operator_registry.consume(",
        "self._oms.stage_external_submission(",
        "OrderManagementSystem",
        "HttpsAlpacaPaperCryptoWriteTransport",
        "os.environ",
        "os.getenv",
        "getenv(",
        "api.alpaca.markets",
    ):
        if forbidden in sim:
            fail(f"simulation coordinator contains forbidden direct authority/network source: {forbidden}")

    # The first-canary gate is an orchestration caller, not a raw transport. It
    # may invoke the audited writer exactly once, but its delegate is injected,
    # it owns no HTTP stack, it latches durable replay prevention before the
    # writer call, and every durable UNKNOWN path is reconciliation-only.
    required_first_canary = (
        "class FirstCanaryExecutionInputs:",
        "class FirstCanaryFinalEvidence:",
        "def execute_first_canary_once(",
        "inputs.attempt.assert_unexecuted()",
        "CryptoColdStartFinalWritePhase.PRE_CONSUME",
        "checkpoint_registry.record_pre_consume(pre_consume)",
        "bridge.stage_after_checkpoint(",
        "inputs.attempt.execution_started_path",
        '"retry_forbidden": True',
        "ColdStartFinalGuardedCryptoEntryTransport(",
        "delegate=delegate",
        "AlpacaPaperCryptoWriterConfig(enabled=True)",
        "writer.submit_once(",
        "UNKNOWN_RECONCILIATION_REQUIRED",
        "reconciler.reconcile(",
        '"retry_post": False',
    )
    for token in required_first_canary:
        if token not in first_canary:
            fail(f"first-canary orchestration missing contract token: {token}")
    sequence = (
        first_canary.find("checkpoint_registry.record_pre_consume(pre_consume)"),
        first_canary.find("stage = bridge.stage_after_checkpoint("),
        first_canary.find("inputs.attempt.write_once(\n        path=inputs.attempt.execution_started_path"),
        first_canary.find("writer.submit_once("),
        first_canary.find("reconciler.reconcile("),
    )
    if any(index < 0 for index in sequence) or tuple(sorted(sequence)) != sequence:
        fail(
            "first-canary sequence must be PRE_CONSUME checkpoint -> OMS stage -> durable replay latch -> writer -> reconciliation"
        )
    for forbidden in (
        "HttpsAlpacaPaperCryptoWriteTransport",
        "http.client",
        "urllib",
        "requests",
        "socket",
        ".post(",
        "operator_registry.consume(",
        "stage_external_submission(",
        "R6_EXTERNAL_PAPER_WRITE",
        "APCA_API_SECRET_KEY",
    ):
        if forbidden in first_canary:
            fail(f"first-canary orchestration contains forbidden direct authority/network source: {forbidden}")
    if first_canary.count("writer.submit_once(") != 1:
        fail("first-canary orchestration must contain exactly one audited writer invocation call site")

    # Enabled crypto writer callers are deliberately closed. The legacy
    # deterministic simulator and the new simulation-first first-canary
    # orchestrator are the only callers at this stage; neither constructs raw
    # network transport. A real Mac delegate injector needs its own later gate.
    allowed_writer_callers = {SIMULATION.resolve(), FIRST_CANARY_GATE.resolve()}
    for path in SRC.rglob("*.py"):
        if path.resolve() == WRITER.resolve():
            continue
        text = path.read_text(encoding="utf-8")
        if "AlpacaPaperCryptoWriter(" in text and path.resolve() not in allowed_writer_callers:
            fail(f"unexpected production crypto writer caller: {path.relative_to(ROOT)}")

    for path in MAC_SURFACES:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in (
            "alpaca_paper_crypto_execution_bridge",
            "alpaca_paper_crypto_pre_io",
            "alpaca_paper_crypto_execution_simulation",
            "first_canary_execution_gate",
            "CryptoPaperExecutionBridge",
            "CryptoPaperExecutionSimulationCoordinator",
            "FirstCanaryExecutionInputs",
            "execute_first_canary_once",
            "FinalGuardedCryptoEntryTransport",
            "AlpacaPaperCryptoWriter",
        ):
            if token in text:
                fail(f"generic Mac/user-facing surface leaked simulation/write authority: {path.name}: {token}")

    print(
        "crypto execution simulation boundary: PASS — legacy deterministic simulator + isolated first-canary "
        "orchestrator only; durable replay latch before writer; UNKNOWN -> reconciliation; injected delegate only; "
        "no raw network transport; generic Mac remains disconnected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
