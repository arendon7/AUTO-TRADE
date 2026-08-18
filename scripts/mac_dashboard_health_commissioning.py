from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
import importlib.util
import json
from pathlib import Path
import secrets
import sys
from urllib.parse import urlparse


_BASE_PATH = Path(__file__).with_name("mac_dashboard_execution_gate.py")
_SPEC = importlib.util.spec_from_file_location("autotrade_mac_dashboard_execution_gate_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load certified execution Health readiness Control Center")
readiness = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = readiness
_SPEC.loader.exec_module(readiness)

from mac_crypto_health_commissioning import (  # noqa: E402
    COMMISSIONING_KILL_REASON,
    PORTFOLIO_HEALTH_ENTITY_ID,
    commission_health_core,
)


DashboardError = readiness.DashboardError
ROOT = readiness.ROOT
WRITE_ENV = readiness.WRITE_ENV

COMMISSIONING_SECTION = r'''
<section class="card" id="healthCommissioningCard"><div class="head"><h2>6 · Health R4 Commissioning · Core durable</h2><span id="healthCommissioningStatus" class="badge warn">SCHEMA ONLY · NO POST</span></div><div class="body">
<div class="callout amber"><strong>LOCAL WRITE / NO BROKER WRITE.</strong> Este paso crea únicamente el <code>core.sqlite3</code> durable y los esquemas R4 necesarios. Activa el kill switch mientras falte evidencia. <strong>No crea Strategy Health, Portfolio Health ni bridge NORMAL.</strong> Nunca convierte ausencia de evidencia en HEALTHY.</div>
<div style="height:12px"></div><div class="actions"><button id="healthCommissionCore" class="btn preview">Commissionar core R4 · LOCAL ONLY / NO POST</button></div><div style="height:14px"></div>
<div class="canaryGrid">
<div class="step"><h3>Core durable</h3><div class="code" id="healthCommissionCoreCode">Aún no commissionado.</div></div>
<div class="step"><h3>Safety fail-closed</h3><div class="code" id="healthCommissionSafetyCode">kill_switch = pendiente</div></div>
<div class="step"><h3>Evidencia Health</h3><div class="code" id="healthCommissionEvidenceCode">Strategy Health: NO CREADO\nPortfolio Health: NO CREADO\nBridge NORMAL: NO CREADO</div></div>
<div class="step"><h3>Autoridad externa</h3><div class="code" id="healthCommissionBoundaryCode">broker_network_used = false\nbroker_write_performed = false\nexternal_post_authorized = false\napproval_consumed = false\ncapital_authority = NONE\nLIVE = BLOCKED</div></div>
</div>
<div class="log" id="healthCommissioningLog">El commissioning estructural aún no se ha ejecutado.</div>
</div></section>
'''

COMMISSIONING_JS = r'''
async function commissionHealthCore(){const b=$("healthCommissionCore");b.disabled=true;$("healthCommissioningStatus").textContent="COMMISSIONING CORE · LOCAL ONLY";$("healthCommissioningStatus").className="badge warn";$("healthCommissioningLog").textContent="Creando/validando core.sqlite3 local y activando kill switch. No se envían Keys ni Secret.";try{const r=await fetch("/api/health-r4-commission-core",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":CSRF},body:JSON.stringify({workspace:$("workspace").value}),cache:"no-store"});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||"Health R4 commissioning failed");const c=d.commissioning;$("healthCommissionCoreCode").textContent="workspace = "+c.workspace+"\ncore.sqlite3 = "+c.core_database+"\nmanifest = "+c.manifest+"\nmanifest_hash = "+c.manifest_hash;$("healthCommissionSafetyCode").textContent="kill_switch_active = "+c.kill_switch_active+"\nkill_switch_reason = "+c.kill_switch_reason+"\nsafety_state_version = "+c.safety_state_version;$("healthCommissionEvidenceCode").textContent="strategy_id = "+c.strategy_id+"\nportfolio_health_entity_id = "+c.portfolio_health_entity_id+"\nhealth_state_rows_created = "+c.health_state_rows_created+"\nhealth_bridge_rows_created = "+c.health_bridge_rows_created+"\nfabricated_health = "+c.fabricated_health+"\nreadiness_blockers = "+(c.readiness_blockers||[]).join(" | ");$("healthCommissionBoundaryCode").textContent="broker_network_used = "+c.broker_network_used+"\ncredentials_read = "+c.credentials_read+"\nlocal_state_write_performed = "+c.local_state_write_performed+"\nbroker_write_performed = "+c.broker_write_performed+"\nexternal_post_authorized = "+c.external_post_authorized+"\napproval_consumed = "+c.approval_consumed+"\noms_submitting = "+c.oms_submitting+"\nlifecycle_unknown = "+c.lifecycle_unknown+"\ncapital_authority = "+c.capital_authority+"\nLIVE = "+c.live_trading;$("healthCommissioningStatus").textContent="CORE COMMISSIONED · EVIDENCE REQUIRED";$("healthCommissioningStatus").className="badge warn";$("healthCommissioningLog").innerHTML='<span class="ok">CORE R4 COMMISSIONED IN FAIL-CLOSED MODE</span>\nNo Health fue fabricado. Kill switch permanece activo.\n\nSiguiente acción: producir y validar evidencia real de Strategy + Portfolio Health.\n\nAhora se vuelve a comprobar la sección 5.';await checkHealthReadiness();}catch(e){$("healthCommissioningStatus").textContent="BLOQUEADO · COMMISSIONING ERROR";$("healthCommissioningStatus").className="badge warn";$("healthCommissioningLog").innerHTML='<span class="err">HEALTH R4 CORE COMMISSIONING FAILED CLOSED</span>\n'+esc(e.message)}finally{b.disabled=false}}
$("healthCommissionCore").addEventListener("click",commissionHealthCore);
'''


def _build_meta() -> dict[str, object]:
    meta = readiness._build_meta()
    meta.update(
        {
            "crypto_health_r4_core_commissioning": True,
            "crypto_health_r4_schema_only": True,
            "crypto_health_r4_portfolio_entity_id": PORTFOLIO_HEALTH_ENTITY_ID,
            "crypto_health_r4_commissioning_kill_reason": COMMISSIONING_KILL_REASON,
            "crypto_health_r4_fabricated_health": False,
            "crypto_execution_final_guard_uat": False,
            "crypto_execution_approval_consumption": False,
            "crypto_execution_oms_staging": False,
            "crypto_execution_lifecycle_unknown": False,
            "crypto_execution_broker_post": False,
        }
    )
    return meta


def _crypto_page(token: str) -> bytes:
    page = readiness._crypto_page(token).decode("utf-8")
    frontier = '<section class="card"><div class="body"><div class="callout red"><strong>Frontera actual:</strong> esta interfaz puede registrar una aprobación humana UAT de un solo uso, pero NO puede consumirla, abrir Final Guard ni enviar una orden.'
    if frontier not in page:
        raise DashboardError("Health commissioning frontier anchor drifted; refusing unsafe partial injection")
    page = page.replace(frontier, COMMISSIONING_SECTION + frontier, 1)
    closing = "</script></body></html>"
    if closing not in page:
        raise DashboardError("Health commissioning script anchor is missing")
    page = page.replace(closing, COMMISSIONING_JS + "\n</script></body></html>", 1)
    return page.encode("utf-8")


class DashboardServer(readiness.DashboardServer):
    def __init__(self, address: tuple[str, int], csrf_token: str) -> None:
        super().__init__(address, csrf_token)
        self.RequestHandlerClass = DashboardHandler


class DashboardHandler(readiness.DashboardHandler):
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
        if urlparse(self.path).path != "/api/health-r4-commission-core":
            super().do_POST()
            return
        if not self._require_local_origin():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "origin rejected"})
            return
        if self.headers.get("X-CSRF-Token") != self.dashboard_server.csrf_token:
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "csrf rejected"})
            return
        try:
            payload = self._read_payload()
            workspace_raw = str(payload.get("workspace") or "").strip()
            if not workspace_raw:
                raise DashboardError("workspace is required")
            result = commission_health_core(
                workspace_path=Path(workspace_raw),
                now=datetime.now(timezone.utc),
            )
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "commissioning": result,
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
                f"AUTO-TRADE Health R4 commissioning diagnostic {diagnostic_id}: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
            self._json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "error": f"Health R4 core commissioning failed closed [{diagnostic_id}]",
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
    readiness.approval._base._enable_line_buffered_console()
    args = readiness.approval._base._parser().parse_args(argv)
    try:
        readiness.approval._base._require_safe_runtime()
        server = _start_server(args.host, args.port)
    except DashboardError as exc:
        print(f"AUTO-TRADE DASHBOARD BLOCKED: {exc}", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{server.server_port}/"
    print("AUTO-TRADE R6 — HEALTH R4 CORE COMMISSIONING / NO POST")
    print(f"Hub: {url}")
    print(f"Crypto: {url}crypto")
    print("Health core commissioning: LOCAL SCHEMA WRITE ONLY")
    print(f"Commissioning kill switch: {COMMISSIONING_KILL_REASON}")
    print("Health evidence creation: UNAVAILABLE")
    print("Approval consumption: UNAVAILABLE")
    print("OMS SUBMITTING: UNAVAILABLE")
    print("Lifecycle UNKNOWN: UNAVAILABLE")
    print("External PAPER write: DISABLED")
    print("LIVE trading: BLOCKED")
    if not args.no_browser:
        import threading
        threading.Timer(0.35, lambda: readiness.approval._base._open_browser(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
