from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/autotrade"
PRE_IO = SRC / "brokers/alpaca_paper_crypto_pre_io.py"
SIMULATION = SRC / "brokers/alpaca_paper_crypto_execution_simulation.py"
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
    for path in (PRE_IO, SIMULATION, WRITER):
        if not path.is_file():
            fail(f"missing required file: {path.relative_to(ROOT)}")

    pre = PRE_IO.read_text(encoding="utf-8")
    sim = SIMULATION.read_text(encoding="utf-8")

    for path in (PRE_IO, SIMULATION):
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

    required_sim = (
        "PRE_CONSUME -> durable checkpoint -> human CONSUMED -> OMS SUBMITTING",
        "writer persists ENTRY_SUBMISSION_UNKNOWN -> PRE_IO -> one simulated POST",
        "self._attempts.record_pre_consume(pre_consume)",
        "operator_registry.consume(",
        "self._oms.stage_external_submission(",
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
        sim.find("operator_registry.consume("),
        sim.find("self._oms.stage_external_submission("),
        sim.find("writer.submit_once("),
    )
    if any(index < 0 for index in ordered) or tuple(sorted(ordered)) != ordered:
        fail("simulation authority sequence is not checkpoint -> consume -> stage -> writer")
    for forbidden in (
        "HttpsAlpacaPaperCryptoWriteTransport",
        "os.environ",
        "os.getenv",
        "getenv(",
        "api.alpaca.markets",
    ):
        if forbidden in sim:
            fail(f"simulation coordinator contains forbidden live/network source: {forbidden}")

    # The enabled crypto writer must have no production caller other than this
    # explicitly simulation-only coordinator until a separately certified real
    # PAPER execution interface is introduced.
    for path in SRC.rglob("*.py"):
        if path in (SIMULATION, WRITER):
            continue
        text = path.read_text(encoding="utf-8")
        if "AlpacaPaperCryptoWriter(" in text:
            fail(f"unexpected production crypto writer caller: {path.relative_to(ROOT)}")

    for path in MAC_SURFACES:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in (
            "alpaca_paper_crypto_pre_io",
            "alpaca_paper_crypto_execution_simulation",
            "CryptoPaperExecutionSimulationCoordinator",
            "FinalGuardedCryptoEntryTransport",
            "AlpacaPaperCryptoWriter",
        ):
            if token in text:
                fail(f"Mac/user-facing surface leaked simulation/write authority: {path.name}: {token}")

    print(
        "crypto execution simulation boundary: PASS — UNKNOWN -> PRE_IO -> delegated transport; "
        "checkpoint -> consume -> OMS stage ordering; deterministic in-memory delegate only; "
        "no environment credentials/direct network; Mac remains disconnected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
