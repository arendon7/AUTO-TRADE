from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
import importlib.util
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import urlparse


_BASE_PATH = Path(__file__).with_name("mac_dashboard_health_commissioning.py")
_SPEC = importlib.util.spec_from_file_location("autotrade_mac_dashboard_health_commissioning_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load certified Health R4 commissioning Control Center")
commissioning = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = commissioning
_SPEC.loader.exec_module(commissioning)


DashboardError = commissioning.DashboardError
ROOT = commissioning.ROOT
WRITE_ENV = commissioning.WRITE_ENV
_base = commissioning.readiness.approval._base

COLD_START_SECTION = r'''
<section class="card" id="coldStartPortfolioCard"><div class="head"><h2>7 · Cold-Start Core Bootstrap · Portfolio broker-grounded</h2><span id="coldStartPortfolioStatus" class="badge warn">PAPER GET + LOCAL STATE · NO POST</span></div><div class="body">
<div class="callout amber"><strong>3 GET PAPER / LOCAL WRITE / NO ORDER.</strong> Este gate vuelve a comprobar la cuenta PAPER, posiciones y órdenes abiertas. Sólo si la cuenta está realmente plana inicializa <code>Portfolio State v1</code> en el <code>core.sqlite3</code> durable. <strong>El kill switch sigue activo y Health sigue ausente.</strong> No consume aprobación, no abre Final Guard y no existe broker POST.</div>
<div style="height:12px"></div><div class="actions"><button id="coldStartPortfolio" class="btn preview">Inicializar Portfolio durable desde cuenta plana · NO POST</button></div><div style="height:14px"></div>
<div class="canaryGrid">
<div class="step"><h3>Evidencia PAPER fresca</h3><div class="code" id="coldStartBrokerCode">Aún no comprobada.</div></div>
<div class="step"><h3>Portfolio State durable</h3><div class="code" id="coldStartPortfolioCode">Aún no inicializado.</div></div>
<div class="step"><h3>Health / Safety</h3><div class="code" id="coldStartHealthCode">kill_switch = DEBE SEGUIR ACTIVO\nHealth rows = 0\nBridge rows = 0</div></div>
<div class="step"><h3>Fronteras externas</h3><div class="code" id="coldStartBoundaryCode">credentials_persisted = false\nbroker_write_performed = false\nexternal_post_authorized = false\napproval_consumed = false\noms_submitting = false\nlifecycle_unknown = false\ncapital_authority = NONE\nLIVE = BLOCKED</div></div>
</div>
<div class="log" id="coldStartPortfolioLog">Cold-start Portfolio State todavía no comprobado.</div>
</div></section>
'''

COLD_START_JS = r'''
async function bootstrapColdStartPortfolio(){const b=$("coldStartPortfolio");b.disabled=true;$("coldStartPortfolioStatus").textContent="COMPROBANDO CUENTA PLANA · PAPER GET";$("coldStartPortfolioStatus").className="badge warn";$("coldStartPortfolioLog").textContent="GET cuenta + posiciones + órdenes abiertas. Keys efímeras; no se guardan. No hay order POST.";try{const r=await fetch("/api/cold-start-portfolio-bootstrap",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":CSRF},body:JSON.stringify({workspace:$("workspace").value,paper_key:$("key").value,paper_secret:$("secret").value}),cache:"no-store"});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||"cold-start portfolio bootstrap failed");const c=d.bootstrap;$("coldStartBrokerCode").textContent="broker_reads = "+c.broker_reads+"\naccount_reference = "+c.account_reference+"\naccount_fingerprint = "+c.fresh_account_fingerprint+"\nflat_fingerprint = "+c.fresh_flat_account_fingerprint+"\npositions = "+c.position_count+"\nopen_orders = "+c.broker_open_order_count;$("coldStartPortfolioCode").textContent="created = "+c.portfolio_created+"\nversion = "+c.portfolio_version+"\nsnapshot_id = "+c.portfolio_snapshot_id+"\nequity = $"+c.portfolio_equity+"\ngross_exposure = "+c.gross_exposure+"\nnet_exposure = "+c.net_exposure+"\nopen_orders = "+c.open_orders+"\nreconciliation_ok = "+c.reconciliation_ok+"\nbroker_state_known = "+c.broker_state_known+"\nmanifest_hash = "+c.manifest_hash;$("coldStartHealthCode").textContent="kill_switch_active = "+c.kill_switch_active+"\nkill_switch_reason = "+c.kill_switch_reason+"\nkill_switch_reset = "+c.kill_switch_reset+"\nhealth_state_rows = "+c.health_state_rows+"\nhealth_bridge_rows = "+c.health_bridge_rows;$("coldStartBoundaryCode").textContent="credentials_read = "+c.credentials_read+"\ncredentials_persisted = "+c.credentials_persisted+"\nbroker_write_performed = "+c.broker_write_performed+"\nexternal_post_authorized = "+c.external_post_authorized+"\napproval_consumed = "+c.approval_consumed+"\noms_submitting = "+c.oms_submitting+"\nlifecycle_unknown = "+c.lifecycle_unknown+"\ncapital_authority = "+c.capital_authority+"\nLIVE = "+c.live_trading;$("coldStartPortfolioStatus").textContent="PORTFOLIO V1 BOOTSTRAPPED · HEALTH STILL BLOCKED";$("coldStartPortfolioStatus").className="badge good";$("coldStartPortfolioLog").innerHTML='<span class="ok">COLD-START PORTFOLIO BOOTSTRAP PASS</span>\nPortfolio State durable ya está anclado a cuenta PAPER plana real.\n\nHealth NO fue creado. Kill switch NO fue reseteado.\nBroker POST: NO.\n\nSiguiente gate: cold-start qualification attestation, todavía sin ejecución.';}catch(e){$("coldStartPortfolioStatus").textContent="BLOQUEADO · COLD-START";$("coldStartPortfolioStatus").className="badge warn";$("coldStartPortfolioLog").innerHTML='<span class="err">COLD-START PORTFOLIO BOOTSTRAP FAILED CLOSED</span>\n'+esc(e.message)}finally{b.disabled=false}}
$("coldStartPortfolio").addEventListener("click",bootstrapColdStartPortfolio);
'''


def _build_meta() -> dict[str, object]:
    meta = commissioning._build_meta()
    meta.update(
        {
            "crypto_cold_start_portfolio_bootstrap": True,
            "crypto_cold_start_broker_reads": 3,
            "crypto_cold_start_portfolio_version": 1,
            "crypto_cold_start_health_created": False,
            "crypto_cold_start_kill_switch_reset": False,
            "crypto_execution_final_guard_uat": False,
            "crypto_execution_approval_consumption": False,
            "crypto_execution_oms_staging": False,
            "crypto_execution_lifecycle_unknown": False,
            "crypto_execution_broker_post": False,
        }
    )
    return meta


def _crypto_page(token: str) -> bytes:
    page = commissioning._crypto_page(token).decode("utf-8")
    frontier = '<section class="card"><div class="body"><div class="callout red"><strong>Frontera actual:</strong> esta interfaz puede registrar una aprobación humana UAT de un solo uso, pero NO puede consumirla, abrir Final Guard ni enviar una orden.'
    if frontier not in page:
        raise DashboardError("cold-start frontier anchor drifted; refusing unsafe partial injection")
    page = page.replace(frontier, COLD_START_SECTION + frontier, 1)
    closing = "</script></body></html>"
    if closing not in page:
        raise DashboardError("cold-start script anchor is missing")
    page = page.replace(closing, COLD_START_JS + "\n</script></body></html>", 1)
    return page.encode("utf-8")


def _run_bootstrap(*, workspace: str, credentials: tuple[str, str]) -> dict[str, object]:
    argv = [
        str(_base.PYTHON),
        "scripts/mac_crypto_cold_start_portfolio_bootstrap.py",
        "--workspace",
        workspace,
        "--allow-paper-crypto-read",
    ]
    started = time.monotonic()
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        env=_base._safe_env(paper_credentials=credentials),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    stdout = _base._redact(completed.stdout, credentials)
    stderr = _base._redact(completed.stderr, credentials)
    parsed = _base._extract_json(stdout)
    error = ""
    if completed.returncode != 0:
        if isinstance(parsed, dict) and isinstance(parsed.get("reason"), str):
            error = str(parsed["reason"]).strip()[:1000]
        if not error:
            lines = [line.strip() for line in stderr.splitlines() if line.strip()]
            error = lines[-1][:1000] if lines else f"cold-start child returncode={completed.returncode}"
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "json": parsed,
        "error": error,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "broker_write_performed": False,
        "external_post_authorized": False,
        "approval_consumed": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "at": datetime.now(timezone.utc).isoformat(),
    }


class DashboardServer(commissioning.DashboardServer):
    def __init__(self, address: tuple[str, int], csrf_token: str) -> None:
        super().__init__(address, csrf_token)
        self.RequestHandlerClass = DashboardHandler


class DashboardHandler(commissioning.DashboardHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/crypto":
            self._write_body(HTTPStatus.OK, "text/html; charset=utf-8", _crypto_page(self.dashboard_server.csrf_token))
            return
        if parsed.path == "/api/meta":
            self._json(HTTPStatus.OK, {"ok": True, "meta": _build_meta()})
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/cold-start-portfolio-bootstrap":
            super().do_POST()
            return
        if not self._require_local_origin():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "origin rejected"})
            return
        if self.headers.get("X-CSRF-Token") != self.dashboard_server.csrf_token:
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "csrf rejected"})
            return
        credentials: tuple[str, str] | None = None
        try:
            payload = self._read_payload()
            workspace = _base._workspace(payload)
            credentials = _base._paper_credentials(payload)
            result = _run_bootstrap(workspace=workspace, credentials=credentials)
            if not result.get("ok"):
                raise DashboardError(str(result.get("error") or "cold-start portfolio bootstrap blocked"))
            bootstrap = result.get("json")
            if not isinstance(bootstrap, dict):
                raise DashboardError("cold-start portfolio bootstrap child returned no structured result")
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "bootstrap": bootstrap,
                    "broker_write_performed": False,
                    "external_post_authorized": False,
                    "approval_consumed": False,
                    "capital_authority": "NONE",
                    "live_trading": "BLOCKED",
                },
            )
        except Exception as exc:
            diagnostic_id = secrets.token_hex(8)
            print(
                f"AUTO-TRADE cold-start Portfolio bootstrap diagnostic {diagnostic_id}: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
            message = str(exc)
            if credentials is not None:
                message = _base._redact(message, credentials)
            self._json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "error": f"{message[:1000]} [{diagnostic_id}]",
                    "credentials_persisted": False,
                    "broker_write_performed": False,
                    "external_post_authorized": False,
                    "approval_consumed": False,
                    "capital_authority": "NONE",
                    "live_trading": "BLOCKED",
                },
            )


def _start_server(host: str, port: int) -> DashboardServer:
    if host != "127.0.0.1":
        raise DashboardError("Dashboard may bind only to 127.0.0.1")
    if not 0 <= port <= 65535:
        raise DashboardError("invalid port")
    token = secrets.token_urlsafe(32)
    try:
        return DashboardServer((host, port), token)
    except OSError:
        if port == 0:
            raise
        return DashboardServer((host, 0), token)


def main(argv: list[str] | None = None) -> int:
    _base._enable_line_buffered_console()
    args = _base._parser().parse_args(argv)
    try:
        _base._require_safe_runtime()
        server = _start_server(args.host, args.port)
    except DashboardError as exc:
        print(f"AUTO-TRADE DASHBOARD BLOCKED: {exc}", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{server.server_port}/"
    print("AUTO-TRADE R6 — COLD-START PORTFOLIO BOOTSTRAP / NO POST")
    print(f"Hub: {url}")
    print(f"Crypto: {url}crypto")
    print("Cold-start broker reads: ACCOUNT + POSITIONS + OPEN ORDERS")
    print("Portfolio State: VERSION 1 / ZERO EXPOSURE ONLY")
    print("Health evidence creation: UNAVAILABLE")
    print("Commissioning kill switch reset: UNAVAILABLE")
    print("Approval consumption: UNAVAILABLE")
    print("Final Guard: UNAVAILABLE")
    print("OMS SUBMITTING: UNAVAILABLE")
    print("Lifecycle UNKNOWN: UNAVAILABLE")
    print("External PAPER write: DISABLED")
    print("LIVE trading: BLOCKED")
    if not args.no_browser:
        import threading
        threading.Timer(0.35, lambda: _base._open_browser(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
