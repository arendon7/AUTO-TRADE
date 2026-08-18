from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "scripts/mac_first_canary_real_paper_dashboard.py"
PAGE = ROOT / "web/mac_first_canary_real_paper.html"
LAUNCHER = ROOT / "ABRIR_PRIMER_CANARY_REAL_PAPER.command"
SAFE_DASHBOARD = ROOT / "scripts/mac_first_canary_dashboard.py"
SAFE_PAGE = ROOT / "web/mac_first_canary.html"


def fail(message: str) -> None:
    print(f"first-canary real PAPER Mac surface: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def require(text: str, tokens: tuple[str, ...], label: str) -> None:
    for token in tokens:
        if token not in text:
            fail(f"{label} missing safety/UX anchor: {token}")


def main() -> int:
    for path in (DASHBOARD, PAGE, LAUNCHER, SAFE_DASHBOARD, SAFE_PAGE):
        if not path.is_file():
            fail(f"missing required file: {path.relative_to(ROOT)}")

    dashboard = DASHBOARD.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    launcher = LAUNCHER.read_text(encoding="utf-8")
    safe_dashboard = SAFE_DASHBOARD.read_text(encoding="utf-8")
    safe_page = SAFE_PAGE.read_text(encoding="utf-8")

    require(
        dashboard,
        (
            'HTML = ROOT / "web/mac_first_canary_real_paper.html"',
            'EXECUTE_SCRIPT = ROOT / "scripts/mac_crypto_first_canary_execute_real_paper.py"',
            'PREPARED_EVIDENCE_FILENAME = "prepared_evidence.json"',
            'CONSENT_FILENAME = "external_post_consent.json"',
            'os.environ.get(WRITE_ENV) == "ENABLED"',
            '"PREPARE_IN_SAFE_GATE_FIRST"',
            '"RESTART_SAFE_PREPARATION_REQUIRED"',
            '"EXECUTION_APPROVAL_REQUIRED_IN_SAFE_GATE"',
            '"READY_FOR_SECOND_EXACT_POST_CONFIRMATION"',
            '"POST_CONSENT_OR_ATTEMPT_BURNED_RECOVERY_ONLY"',
            '"RESOLVED_NO_MORE_POST"',
            "external_post_challenge(",
            'if not Decimal("1") <= notional <= Decimal("5"):',
            'def _discover_ready_attempt(*, workspace: Path)',
            'ATTEMPT_ID_RE.fullmatch(child.name)',
            '"EXACT_ONE_READY"',
            '"AMBIGUOUS_MULTIPLE_READY"',
            '"NO_READY_ATTEMPT"',
            'package_deadline <= now or approval_deadline <= now',
            '"automatic_attempt_discovery": "EXACTLY_ONE_FRESH_READY_ONLY"',
            'discovery = _discover_ready_attempt(workspace=workspace)',
            'discovery.get("selection_status") != "EXACT_ONE_READY"',
            '"execution requires exactly one fresh approved unstarted attempt and the selected Attempt ID must match it"',
            'if confirmation != status.get("external_post_challenge"):',
            '"scripts/mac_crypto_first_canary_execute_real_paper.py"',
            '"--allow-exact-paper-post"',
            'env=_safe_env(credentials)',
            'input=json.dumps({"confirmation": confirmation}',
            '"execution process timed out; POST outcome is treated as ambiguous: never retry POST, use GET-only recovery"',
            'if parsed.path == "/api/discover":',
            'path not in {"/api/execute", "/api/recover"}',
            'expected_origin = f"http://127.0.0.1:{self.canary_server.server_port}"',
            'self.headers.get("X-CSRF-Token") != self.canary_server.csrf_token',
            'if host != "127.0.0.1":',
            '"generic_control_center_write_enabled": False',
            '"credentials_persisted": False',
            '"retry_post": False',
            '"live_trading": "BLOCKED"',
        ),
        "execution-only localhost dashboard",
    )
    for forbidden in (
        '"/api/prepare"',
        '"/api/approve"',
        "HttpsAlpacaPaperCryptoWriteTransport",
        "AlpacaPaperCryptoWriter",
        "paper-api.alpaca.markets",
        "api.alpaca.markets",
        "http.client",
        "urllib.request",
        ".post(",
    ):
        if forbidden in dashboard:
            fail(f"real PAPER dashboard bypasses execution-only/audited boundary: {forbidden}")

    status_sequence = (
        dashboard.find('prepared_evidence = _safe_document(attempt.attempt_root / PREPARED_EVIDENCE_FILENAME)'),
        dashboard.find('approval = _safe_document(attempt.approval_receipt_path)'),
        dashboard.find('consent = _safe_document(attempt.attempt_root / CONSENT_FILENAME)'),
        dashboard.find('started = _safe_document(attempt.execution_started_path)'),
    )
    if any(index < 0 for index in status_sequence) or tuple(sorted(status_sequence)) != status_sequence:
        fail("dashboard must inspect restart-safe evidence + approval before consent/execution state")

    require(
        page,
        (
            "Esta pantalla sí puede enviar exactamente una orden PAPER a Alpaca.",
            "PAPER ONLY",
            "BTC/USD",
            "BUY LIMIT IOC",
            "USD 1–5",
            "LIVE BLOCKED",
            "después de consentir o iniciar, nunca vuelvas a presionar ejecutar",
            "Solo en ese caso lo carga automáticamente.",
            "Detectar / revisar estado",
            "async function discover()",
            "'/api/discover?'",
            "d.selection_status === 'EXACT_ONE_READY'",
            "d.selection_status === 'AMBIGUOUS_MULTIPLE_READY'",
            "Vuelve a PREPARAR y crea/aprueba un intento nuevo.",
            "Segundo consentimiento exacto",
            "EJECUTAR UNA VEZ EN PAPER",
            'id="recover" disabled',
            "RECUPERAR / RECONCILIAR GET-ONLY",
            "const discovery = await discover();",
            "discovery.selection_status !== 'EXACT_ONE_READY'",
            "currentStatus.ready_for_real_post !== true",
            "currentStatus.recovery_get_only !== true",
            "$('recover').disabled = !(s && s.recovery_get_only === true);",
            "$('confirmation').value !== exact",
            "NO REINTENTAR POST. Usa recuperación GET-only solo si el estado la habilita.",
        ),
        "real PAPER HTML",
    )
    for forbidden in ("LIVE ENABLED", "auto execute", "setInterval(", "paper-api.alpaca.markets", "api.alpaca.markets"):
        if forbidden in page:
            fail(f"real PAPER HTML contains forbidden autonomous/network surface: {forbidden}")

    require(
        launcher,
        (
            'SOURCE_ROOT="$(cd "$(dirname "$0")" && pwd -P)"',
            'INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"',
            'real_paper_surface=SEPARATE_EXACT_ONE_SHOT',
            'EXPECTED_HEAD="$(read_source_head "$SOURCE_ROOT")"',
            'INSTALLED_HEAD="$(read_source_head "$INSTALL_ROOT")"',
            'bash "$SOURCE_ROOT/INSTALAR_AUTO_TRADE.command"',
            'ROOT="$INSTALL_ROOT"',
            'DASHBOARD="$ROOT/scripts/mac_first_canary_real_paper_dashboard.py"',
            'PAGE="$ROOT/web/mac_first_canary_real_paper.html"',
            'if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]',
            "export R6_EXTERNAL_PAPER_WRITE=DISABLED",
            "unset APCA_API_KEY_ID || true",
            "unset APCA_API_SECRET_KEY || true",
            "Puede enviar UNA orden BTC/USD PAPER ya preparada y aprobada, máximo USD 5.",
            "NO REINTENTAR POST; usar recuperación GET-only.",
            "LIVE: BLOCKED",
        ),
        "Finder launcher",
    )
    for forbidden in (
        "APCA_API_SECRET_KEY=",
        "R6_EXTERNAL_PAPER_WRITE=ENABLED",
        "mac_crypto_first_canary_execute_real_paper.py",
        "paper-api.alpaca.markets",
    ):
        if forbidden in launcher:
            fail(f"Finder launcher bypasses localhost execution gate: {forbidden}")

    if 'parsed_path not in {"/api/prepare", "/api/approve", "/api/recover"}' not in safe_dashboard:
        fail("PR41 safe dashboard route contract changed")
    if 'real_execution_enabled": False' not in safe_dashboard:
        fail("PR41 safe dashboard no longer has real execution disabled")
    if "EJECUTAR UNA VEZ EN PAPER" in safe_page:
        fail("PR41 safe page acquired the real execution action")

    print(
        "first-canary real PAPER Mac surface: PASS — downloaded Finder launcher relocates/reuses exact certified "
        "installed head; separate execution-only localhost UI; exactly-one fresh approved attempt auto-discovery "
        "is re-enforced at execute boundary and freshly rechecked by the UI at click time; ambiguous/expired selection "
        "fails closed; restart-safe preparation + approval required; exact second challenge; recovery stays disabled "
        "until consent/start; no direct broker stack; launcher carries no credentials and keeps generic write disabled; "
        "LIVE blocked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())