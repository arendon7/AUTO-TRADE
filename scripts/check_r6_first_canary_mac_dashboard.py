from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts/mac_first_canary_dashboard.py"
HTML = ROOT / "web/mac_first_canary.html"
LAUNCHER = ROOT / "ABRIR_PRIMER_CANARY_PAPER.command"
GENERIC_SURFACES = (
    ROOT / "scripts/mac_dashboard.py",
    ROOT / "scripts/mac_crypto_dashboard.py",
    ROOT / "ABRIR_AUTO_TRADE.command",
    ROOT / "ABRIR_CRYPTO_PAPER.command",
)
DIRECT_NETWORK_ROOTS = {
    "httpx",
    "requests",
    "socket",
    "ssl",
    "urllib.request",
    "websocket",
    "websockets",
}
FORBIDDEN_WRITE_TOKENS = (
    "first_canary_execution_gate",
    "execute_first_canary_once",
    "AlpacaPaperCryptoWriter",
    "HttpsAlpacaPaperCryptoWriteTransport",
    "ColdStartFinalGuardedCryptoEntryTransport",
    "submit_once(",
    '"/api/execute"',
    "'/api/execute'",
    'connection.request("POST"',
    'method="POST"',
    "method='POST'",
)


def fail(message: str) -> None:
    print(f"first-canary Mac dashboard boundary: FAIL: {message}", file=sys.stderr)
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
    for path in (SERVER, HTML, LAUNCHER):
        if not path.is_file():
            fail(f"required Mac first-canary surface is missing: {path.name}")

    server = SERVER.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    launcher = LAUNCHER.read_text(encoding="utf-8")

    imports = _imports(SERVER)
    for module in imports:
        if module in DIRECT_NETWORK_ROOTS or module.startswith("urllib.request"):
            fail(f"dashboard imports forbidden direct external network stack: {module}")
    for token in FORBIDDEN_WRITE_TOKENS:
        if token in server:
            fail(f"dashboard contains forbidden execution/write authority: {token}")

    for anchor in (
        'WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"',
        'os.environ.get(WRITE_ENV) == "ENABLED"',
        'env[WRITE_ENV] = "DISABLED"',
        'env.pop(KEY_ENV, None)',
        'env.pop(SECRET_ENV, None)',
        'parsed_path not in {"/api/prepare", "/api/approve", "/api/recover"}',
        '"real_execution_enabled": False',
        '"generic_control_center_write_enabled": False',
        '"retry_post": False',
        '"credentials_persisted": False',
        '"live_trading": "BLOCKED"',
        'host != "127.0.0.1"',
        'self.headers.get("X-CSRF-Token") != self.canary_server.csrf_token',
        'self.headers.get("Origin") not in (None, expected_origin)',
        'FirstCanaryAttemptWorkspace.open(',
        'attempt.execution_started_path',
        'attempt.reconciliation_failure_path',
        'attempt.reconciliation_pending_path',
        'attempt.reconciliation_path',
        'attempt.recovery_resolution_path',
    ):
        if anchor not in server:
            fail(f"dashboard missing fail-closed anchor: {anchor}")

    for forbidden in (
        '"/api/execute"',
        "execute_first_canary_once",
        "AlpacaPaperCryptoWriter",
        "HttpsAlpacaPaperCryptoWriteTransport",
        "APCA_API_SECRET_KEY]",
    ):
        if forbidden in server:
            fail(f"dashboard leaked forbidden execution authority: {forbidden}")

    allowed_child_scripts = (
        "scripts/mac_crypto_first_canary_prepare.py",
        "scripts/mac_crypto_first_canary_approval.py",
        "scripts/mac_crypto_first_canary_reconcile.py",
    )
    for script in allowed_child_scripts:
        if script not in server:
            fail(f"dashboard is missing exact audited child: {script}")
    if server.count("subprocess.run(") != 2:
        # one generic child runner + one macOS browser opener
        fail("dashboard subprocess surface drifted from child-runner + browser-opener only")

    for anchor in (
        'id="executeBtn" disabled',
        'POST real bloqueado en esta build',
        '$("executeBtn").disabled=true',
        '"/api/prepare"',
        '"/api/approve"',
        '"/api/recover"',
        'localStorage.setItem("autotrade.firstCanary.workspace"',
        'localStorage.setItem("autotrade.firstCanary.attempt"',
        'crypto.getRandomValues',
        'first-canary-',
        'LIVE BLOCKED',
        'POST REAL: OFF',
    ):
        if anchor not in html:
            fail(f"Mac first-canary HTML missing safety/usability anchor: {anchor}")
    for forbidden in (
        'localStorage.setItem("paperKey"',
        'localStorage.setItem("paperSecret"',
        'localStorage.setItem("APCA',
        'sessionStorage.setItem("paperKey"',
        'sessionStorage.setItem("paperSecret"',
        'fetch("/api/execute"',
        "execute_first_canary_once",
    ):
        if forbidden in html:
            fail(f"Mac first-canary HTML persists credentials or exposes execution: {forbidden}")

    for anchor in (
        'export R6_EXTERNAL_PAPER_WRITE=DISABLED',
        'unset APCA_API_KEY_ID || true',
        'unset APCA_API_SECRET_KEY || true',
        'scripts/mac_first_canary_dashboard.py',
        'POST real: NO EXPUESTO EN ESTA BUILD',
        'LIVE: BLOCKED',
    ):
        if anchor not in launcher:
            fail(f"first-canary launcher missing isolation anchor: {anchor}")
    if "R6_EXTERNAL_PAPER_WRITE=ENABLED" not in launcher:
        fail("first-canary launcher does not refuse pre-enabled write environment")

    for path in GENERIC_SURFACES:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in (
            "mac_first_canary_dashboard",
            "ABRIR_PRIMER_CANARY_PAPER",
            "first_canary_execution_gate",
            "execute_first_canary_once",
        ):
            if token in text:
                fail(f"generic Mac surface acquired first-canary authority: {path.name}: {token}")

    print(
        "first-canary Mac dashboard boundary: PASS — separate localhost 4-step UX; durable attempt recovery; "
        "prepare/approve/GET-recover only; credentials never persisted; execute endpoint absent; real POST OFF; "
        "generic Control Center remains disconnected; LIVE BLOCKED"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
