from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts/mac_first_canary_unified_dashboard.py"
QUEUE = ROOT / "scripts/mac_first_canary_unified_queue.py"
AUTO_SETTLE = ROOT / "scripts/mac_first_canary_unified_auto_settle.py"
R7_OVERLAY = ROOT / "scripts/mac_r7_paper_operations_dashboard.py"
HTML = ROOT / "web/mac_first_canary_unified.html"
SOURCE_LAUNCHER = ROOT / "ABRIR_AUTO_TRADE_CANARY.command"
INSTALLED_LAUNCHER = ROOT / "ABRIR_AUTO_TRADE.command"
MANIFEST = ROOT / "MAC_STANDALONE_MANIFEST.txt"
NETWORK_ROOTS = {"urllib", "requests", "httpx", "aiohttp", "socket", "ssl", "websocket", "websockets"}
FORBIDDEN_DIRECT_AUTHORITY = (
    "HttpsAlpacaPaperCryptoWriteTransport",
    "AlpacaPaperCryptoWriter",
    "http.client",
    "connection.request(",
    ".post(",
    "submit_once(",
    "stage_external_submission(",
    "operator_registry.consume(",
)


def fail(message: str) -> None:
    print(f"unified first-canary Mac boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def _launcher() -> Path:
    if SOURCE_LAUNCHER.is_file():
        return SOURCE_LAUNCHER
    if MANIFEST.is_file() and "first_canary_unified_surface=ONE_APP" in MANIFEST.read_text(encoding="utf-8"):
        if INSTALLED_LAUNCHER.is_file():
            return INSTALLED_LAUNCHER
    return SOURCE_LAUNCHER


def _check_no_direct_authority(path: Path, source: str) -> None:
    roots = {module.split(".", 1)[0] for module in imports(path) if module}
    forbidden_network = roots & NETWORK_ROOTS
    if forbidden_network:
        fail(f"{path.name} owns forbidden external network stack: {sorted(forbidden_network)}")
    for token in FORBIDDEN_DIRECT_AUTHORITY:
        if token in source:
            fail(f"{path.name} contains direct execution authority: {token}")


def main() -> int:
    launcher_path = _launcher()
    for path in (SERVER, QUEUE, AUTO_SETTLE, HTML, launcher_path):
        if not path.is_file():
            fail(f"missing required unified surface: {path.relative_to(ROOT)}")

    server = SERVER.read_text(encoding="utf-8")
    queue = QUEUE.read_text(encoding="utf-8")
    auto_settle = AUTO_SETTLE.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    launcher = launcher_path.read_text(encoding="utf-8")
    launcher_uses_r7 = "mac_r7_paper_operations_dashboard.py" in launcher

    for path, source in ((SERVER, server), (QUEUE, queue), (AUTO_SETTLE, auto_settle)):
        _check_no_direct_authority(path, source)

    required_server = (
        "class UnifiedCanarySession:",
        "self.credentials: tuple[str, str] | None = None",
        'attempt_id = "first-canary-" + secrets.token_hex(16)',
        '"scripts/mac_crypto_first_canary_prepare_restart_safe.py"',
        '"scripts/mac_crypto_first_canary_approval.py"',
        '"confirmation": challenge',
        "real._discover_ready_attempt(workspace=self.workspace)",
        "real._run_execute(",
        "self._auto_recover_if_needed(post_status)",
        '"retry_post": False',
        '"credentials_persisted": False',
        '"live_trading": "BLOCKED"',
        "self._action_lock = threading.Lock()",
        "if not self.lock.acquire(blocking=False):",
        "self.review_token: str | None = None",
        "self.execute_token: str | None = None",
        "secrets.compare_digest(supplied_token, self.review_token)",
        "secrets.compare_digest(supplied_token, self.execute_token)",
        "self.execute_token = None",
    )
    for token in required_server:
        if token not in server:
            fail(f"unified server missing boundary anchor: {token}")

    required_queue = (
        "class QueuedRecoverySession(base.UnifiedCanarySession):",
        "candidates = self._ordered_recovery_candidates()",
        "self.active_attempt_id = candidates[0]",
        "base.safe._recover(",
        '"pending_recovery_count"',
        '"retry_post": False',
        '"credentials_persisted": False',
        '"live_trading": "BLOCKED"',
        "for attempt_id in candidates:",
        '"MANUAL_REVIEW" in status_text',
        '"HALTED" in status_text',
    )
    for token in required_queue:
        if token not in queue:
            fail(f"unified recovery queue missing boundary anchor: {token}")
    for forbidden in (
        "real._run_execute(",
        "EXECUTE_SCRIPT",
        "R6_EXTERNAL_PAPER_WRITE=ENABLED",
        "APCA_API_SECRET_KEY",
        "APCA_API_KEY_ID",
    ):
        if forbidden in queue:
            fail(f"GET-only recovery queue contains forbidden execution/credential authority: {forbidden}")

    required_auto_settle = (
        "class AutoSettlementSession(queue.QueuedRecoverySession):",
        "AUTO_SETTLE_DELAYS_SECONDS = (0.0, 1.0, 2.0, 4.0)",
        "base.safe._recover(",
        'sanitized["auto_settlement_attempts"] = index',
        'sanitized["auto_settlement_resolved"] = resolved',
        'last["auto_settlement_exhausted"] = True',
        'last["ok"] = False',
        '"retry_post": False',
        '"live_trading": "BLOCKED"',
        '"SETTLED_FLAT"',
        '"SETTLED_FILLED"',
        '"SETTLED_CANCELED"',
    )
    for token in required_auto_settle:
        if token not in auto_settle:
            fail(f"automatic settlement missing fail-closed anchor: {token}")
    for forbidden in (
        "real._run_execute(",
        "EXECUTE_SCRIPT",
        "R6_EXTERNAL_PAPER_WRITE=ENABLED",
        "APCA_API_SECRET_KEY",
        "APCA_API_KEY_ID",
    ):
        if forbidden in auto_settle:
            fail(f"automatic settlement contains forbidden execution/credential authority: {forbidden}")

    if launcher_uses_r7:
        if not R7_OVERLAY.is_file():
            fail("R7 launcher selected but operations overlay is missing")
        r7_overlay = R7_OVERLAY.read_text(encoding="utf-8")
        _check_no_direct_authority(R7_OVERLAY, r7_overlay)
        required_r7_chain = (
            "import mac_first_canary_unified_auto_settle as r6",
            "class PaperOperationsSession(r6.AutoSettlementSession):",
            "class PaperOperationsHandler(r6.base.UnifiedHandler):",
            "super()._assert_no_unresolved_recovery()",
            "snapshot.portfolio.positions or snapshot.portfolio.open_orders",
            '"broker_write_authorized": False',
            '"retry_post": False',
            '"credentials_persisted": False',
            '"live_trading": "BLOCKED"',
        )
        for token in required_r7_chain:
            if token not in r7_overlay:
                fail(f"R7 launcher chain missing inherited R6/fail-closed anchor: {token}")

    forbidden_html_inputs = (
        'id="attempt',
        'name="attempt',
        'id="challenge',
        'name="challenge',
        "Copiar challenge",
        "pega el challenge",
        "Attempt ID",
    )
    for token in forbidden_html_inputs:
        if token in html:
            fail(f"unified UI leaks internal operator plumbing: {token}")
    required_html = (
        "Una sola app",
        "Conectar Alpaca PAPER",
        "Preparar y revisar",
        "Aprobar preparación",
        "EJECUTAR UNA VEZ EN PAPER",
        "Reconciliar este intento",
        "review_confirmed:true",
        "review_token:token",
        "execute_confirmed:true",
        "execute_token:token",
        "state.reviewToken=null",
        "state.executeToken=null",
        "NO vuelvas a pulsar ejecutar",
        "LIVE BLOCKED",
    )
    for token in required_html:
        if token not in html:
            fail(f"unified UI missing operator anchor: {token}")

    common_launcher = (
        "R6_EXTERNAL_PAPER_WRITE=DISABLED",
        "unset APCA_API_KEY_ID",
        "unset APCA_API_SECRET_KEY",
        "first_canary_unified_surface=ONE_APP",
        "UNA SOLA APP",
        "LIVE BLOCKED",
        "RETRY POST FALSE",
    )
    for token in common_launcher:
        if token not in launcher:
            fail(f"unified launcher missing fail-closed anchor: {token}")

    if launcher_uses_r7:
        for token in (
            "r7_paper_operations_surface=GET_ONLY",
            "mac_r7_paper_operations_dashboard.py",
            "Portfolio y Safety: broker truth GET-only",
            "Cierre de posición: WRITE DISABLED",
            "USD 10-12 · TARGET ~USD 10.50",
        ):
            if token not in launcher:
                fail(f"R7 launcher missing fail-closed/inherited-authority anchor: {token}")
    else:
        for token in (
            "mac_first_canary_unified_dashboard.py",
            "mac_first_canary_unified_queue.py",
            "mac_first_canary_unified_auto_settle.py",
        ):
            if token not in launcher:
                fail(f"legacy unified launcher missing fail-closed anchor: {token}")

    for forbidden in (
        "APCA_API_SECRET_KEY=",
        "APCA_API_KEY_ID=",
        "ABRIR_PRIMER_CANARY_PREPARAR.command",
        "ABRIR_PRIMER_CANARY_REAL_PAPER.command",
    ):
        if forbidden in launcher:
            fail(f"unified launcher leaks old/manual flow or credentials: {forbidden}")

    print(
        "unified first-canary Mac boundary: PASS — one launcher/one window; hidden internal authority identifiers; "
        "two explicit human actions; multiple burned attempts drain only through certified GET recovery; "
        "post-execution auto-settlement is bounded GET-only; R7 launcher, when selected, is cryptographically/source-bound "
        "through AutoSettlementSession to the same R6 authority and adds only GET-only exposure interlock; "
        "credentials memory-only; retry POST false; LIVE blocked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
