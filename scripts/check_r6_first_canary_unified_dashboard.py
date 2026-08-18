from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts/mac_first_canary_unified_dashboard.py"
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


def main() -> int:
    launcher_path = _launcher()
    for path in (SERVER, HTML, launcher_path):
        if not path.is_file():
            fail(f"missing required unified surface: {path.relative_to(ROOT)}")

    server = SERVER.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    launcher = launcher_path.read_text(encoding="utf-8")

    roots = {module.split(".", 1)[0] for module in imports(SERVER) if module}
    forbidden_network = roots & NETWORK_ROOTS
    if forbidden_network:
        fail(f"unified server owns forbidden external network stack: {sorted(forbidden_network)}")
    for token in FORBIDDEN_DIRECT_AUTHORITY:
        if token in server:
            fail(f"unified server contains direct execution authority: {token}")

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

    for token in (
        "R6_EXTERNAL_PAPER_WRITE=DISABLED",
        "unset APCA_API_KEY_ID",
        "unset APCA_API_SECRET_KEY",
        "first_canary_unified_surface=ONE_APP",
        "mac_first_canary_unified_dashboard.py",
        "UNA SOLA APP",
        "LIVE BLOCKED",
        "RETRY POST FALSE",
    ):
        if token not in launcher:
            fail(f"unified launcher missing fail-closed anchor: {token}")
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
        "two explicit human actions with invisible one-shot click bindings; existing certified prepare/approval/execute/recovery authority only; "
        "no direct broker transport; credentials memory-only; retry POST false; LIVE blocked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
