from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
ATTEMPT = ROOT / "src/autotrade/paper_close_attempt.py"
OPERATOR = ROOT / "src/autotrade/paper_close_operator.py"
DASHBOARD = ROOT / "scripts/mac_r7_paper_operations_dashboard.py"
NETWORK_ROOTS = {"urllib", "requests", "httpx", "aiohttp", "socket", "ssl", "websocket", "websockets"}


def fail(message: str) -> None:
    print(f"R7 PAPER close operator boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


def main() -> int:
    for path in (ATTEMPT, OPERATOR, DASHBOARD):
        if not path.is_file():
            fail(f"missing required R7 close surface: {path.relative_to(ROOT)}")

    attempt = ATTEMPT.read_text(encoding="utf-8")
    operator = OPERATOR.read_text(encoding="utf-8")
    dashboard = DASHBOARD.read_text(encoding="utf-8")

    attempt_roots = {name.split(".", 1)[0] for name in imports(ATTEMPT) if name}
    forbidden_attempt_network = attempt_roots & NETWORK_ROOTS
    if forbidden_attempt_network:
        fail(f"restart-safe attempt workspace owns network stack: {sorted(forbidden_attempt_network)}")
    for token in (
        "PaperCloseWriter",
        "PaperCloseExecutionBridge",
        "AlpacaPaperCloseReconciliationGateway",
        ".post(",
        "submit_once(",
    ):
        if token in attempt:
            fail(f"restart-safe attempt workspace contains write/network authority: {token}")
    for token in (
        'CLOSE_ATTEMPT_DIR = "r7_paper_close"',
        'DATABASE_FILENAME = "close.sqlite3"',
        'WRITE_RECEIPT_FILENAME = "write_receipt.json"',
        'mode=ro&immutable=1',
        'PRAGMA query_only=ON',
        "_verify_event_chain(state, events)",
        "state.submission_attempt_count == 0",
        "state.submission_attempt_count != 1",
        "PaperCloseLifecycleStatus.FLAT_RECONCILED",
        "PaperCloseLifecycleStatus.TERMINAL_RECONCILED",
    ):
        if token not in attempt:
            fail(f"restart-safe attempt workspace missing boundary anchor: {token}")

    operator_roots = {name.split(".", 1)[0] for name in imports(OPERATOR) if name}
    forbidden_operator_network = operator_roots & NETWORK_ROOTS
    if forbidden_operator_network:
        fail(f"close operator owns raw network stack: {sorted(forbidden_operator_network)}")
    for token in (
        'CLOSE_WRITE_ENV = "R7_CLOSE_PAPER_WRITE"',
        'os.environ.get(CLOSE_WRITE_ENV, "DISABLED") != "ENABLED"',
        "ReadOnlyCanonicalSafetyStateStore",
        "read_paper_safety_snapshot(",
        "R7RiskReducingOrderManagementSystem(",
        "prepare_paper_close_control_plane(",
        'confirmation="CERRAR PAPER"',
        "stage_risk_reducing_external_submission(",
        "bind_paper_close_execution_authority(",
        "PaperCloseExecutionBridge(writer=writer)",
        "receipt = bridge.execute_once(",
        '"UNKNOWN_AFTER_DURABLE_PRE_IO"',
        '"RECONCILE_GET_ONLY_NEVER_RETRY_POST"',
        '"STOP_AND_CERTIFY_RESIDUAL_EXPOSURE"',
        '"retry_post": False',
        '"live_trading": "BLOCKED"',
    ):
        if token not in operator:
            fail(f"close operator missing fail-closed anchor: {token}")
    for forbidden in (
        "UrllibAlpacaPaperWriteTransport",
        "urllib.request",
        "http.client",
        "mark_submission_unknown(",
        "R6_EXTERNAL_PAPER_WRITE=ENABLED",
    ):
        if forbidden in operator:
            fail(f"close operator bypasses certified writer/authority boundary: {forbidden}")

    # Before the Mac close UI is integrated, the dashboard must remain incapable
    # of reaching low-level close write authority. After integration it may import
    # only the high-level facade; this checker intentionally forbids every lower layer.
    for forbidden in (
        "paper_close_writer",
        "paper_close_execution_bridge",
        "paper_close_reconciliation",
        "PaperCloseWriter",
        "PaperCloseExecutionBridge",
        "AlpacaPaperCloseReconciliationGateway",
        "submit_once(",
        "mark_submission_unknown(",
    ):
        if forbidden in dashboard:
            fail(f"Mac dashboard contains direct close authority: {forbidden}")

    print(
        "R7 PAPER close operator boundary: PASS — restart-safe burned-attempt discovery is immutable/read-only; "
        "canonical Safety is read-through/no-mutation; FULL BTC/USD close composes Safety + OMS + human decision + "
        "execution bridge; only the certified writer owns PRE_IO/POST; ambiguity becomes GET-only recovery; "
        "residual exposure cannot auto-repost; Mac dashboard has no low-level close authority; LIVE blocked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
