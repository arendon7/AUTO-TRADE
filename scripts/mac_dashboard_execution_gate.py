from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
import importlib.util
import json
from pathlib import Path
import secrets
import sys
from urllib.parse import urlparse


_BASE_PATH = Path(__file__).with_name("mac_dashboard_one_shot.py")
_SPEC = importlib.util.spec_from_file_location("autotrade_mac_dashboard_one_shot_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load certified one-shot approval Control Center")
approval = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = approval
_SPEC.loader.exec_module(approval)

from mac_crypto_execution_health_readiness import (  # noqa: E402
    EXECUTION_STRATEGY_ID,
    inspect_health_readiness,
)


DashboardError = approval.DashboardError
ROOT = approval.ROOT
WRITE_ENV = approval.WRITE_ENV

READINESS_SECTION = r'''
<section class="card" id="executionReadinessCard"><div class="head"><h2>5 · Execution Gate Readiness · Health R4</h2><span id="healthReadinessStatus" class="badge warn">SOLO LECTURA · NO POST</span></div><div class="body">
<div class="callout amber"><strong>NO POST / NO CONSUME.</strong> Este gate no reutiliza ni consume la aprobación UAT. Sólo inspecciona el <code>core.sqlite3</code> real del workspace para comprobar si existe Health R4 autoritativo y su bridge para la estrategia exacta del primer canary. Si falta Health, AUTO-TRADE debe bloquear: nunca se fabricará un estado NORMAL para pasar una demo.</div>
<div style="height:12px"></div><div class="actions"><button id="healthReadinessCheck" class="btn preview">Comprobar Health R4 · SOLO LECTURA</button></div><div style="height:14px"></div>
<div class="canaryGrid">
<div class="step"><h3>Core operativo</h3><p>Debe existir como evidencia durable real del workspace.</p><div class="code" id="healthCoreCode">Aún no comprobado.</div></div>
<div class="step"><h3>Strategy Health exacto</h3><p>Target fijo: R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION. Health HEALTHY + bridge NORMAL + multiplier 1.</p><div class="code" id="healthStrategyCode">Aún no comprobado.</div></div>
<div class="step"><h3>Portfolio Health canónico</h3><p>Debe existir exactamente una identidad PORTFOLIO autoritativa, con bridge íntegro y NORMAL.</p><div class="code" id="healthPortfolioCode">Aún no comprobado.</div></div>
<div class="step"><h3>Fronteras que permanecen cerradas</h3><div class="code" id="healthBoundaryCode">approval_consumed = false\noms_submitting = false\nlifecycle_unknown = false\nbroker_post = false\ncapital_authority = NONE\nLIVE = BLOCKED</div></div>
</div>
<div class="log" id="healthReadinessLog">Health R4 execution readiness no comprobado.</div>
</div></section>
'''

READINESS_JS = r'''
function healthText(entity){if(!entity)return "NO DISPONIBLE";return "entity_id = "+entity.entity_id+"\nhealth_state = "+entity.health_state+"\nhealth_version = "+entity.health_version+"\nhealth_updated_at = "+entity.health_updated_at+"\nhealth_fingerprint = "+entity.health_fingerprint+"\nbridge_mode = "+entity.bridge_mode+"\nbridge_version = "+entity.bridge_version+"\nbridge_multiplier = "+entity.bridge_risk_multiplier+"\nbridge_updated_at = "+entity.bridge_updated_at+"\nbridge_fingerprint = "+entity.bridge_fingerprint+"\nready = "+entity.ready}
async function checkHealthReadiness(){const b=$("healthReadinessCheck");b.disabled=true;$("healthReadinessStatus").textContent="COMPROBANDO HEALTH R4 · READ ONLY";$("healthReadinessStatus").className="badge warn";$("healthReadinessLog").textContent="Inspeccionando core.sqlite3 en modo read-only. No se envían Keys ni Secret a este endpoint.";try{const r=await fetch("/api/execution-health-readiness",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":CSRF},body:JSON.stringify({workspace:$("workspace").value}),cache:"no-store"});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||"Health readiness request failed");const h=d.readiness;$("healthCoreCode").textContent="workspace = "+h.workspace+"\ncore.sqlite3 = "+h.core_database+"\nread_only = "+h.read_only+"\ncredentials_read = "+h.credentials_read+"\nbroker_network_used = "+h.broker_network_used;$("healthStrategyCode").textContent=healthText(h.strategy);$("healthPortfolioCode").textContent=healthText(h.portfolio)+(h.portfolio_health_entity_id?"\nportfolio_health_entity_id = "+h.portfolio_health_entity_id:"");$("healthBoundaryCode").textContent="approval_consumed = "+h.approval_consumed+"\noms_submitting = "+h.oms_submitting+"\nlifecycle_unknown = "+h.lifecycle_unknown+"\nbroker_write_performed = "+h.broker_write_performed+"\nexternal_post_authorized = "+h.external_post_authorized+"\ncapital_authority = "+h.capital_authority+"\nLIVE = "+h.live_trading;if(h.status==="HEALTH_R4_EXECUTION_READINESS_PASS"){ $("healthReadinessStatus").textContent="HEALTH R4 READY · NO POST";$("healthReadinessStatus").className="badge good";$("healthReadinessLog").innerHTML='<span class="ok">HEALTH R4 EXECUTION READINESS PASS</span>\nStrategy + Portfolio Health autoritativos y bridges íntegros.\n\nSiguiente acción arquitectónica: Final Guard PRE_CONSUME UAT separado.\nApproval consumed: NO\nOMS SUBMITTING: NO\nLifecycle UNKNOWN: NO\nBroker POST: NO';}else{$("healthReadinessStatus").textContent="BLOQUEADO · HEALTH R4";$("healthReadinessStatus").className="badge warn";$("healthReadinessLog").innerHTML='<span class="err">HEALTH R4 NO ESTÁ LISTO PARA FINAL GUARD</span>\nBlockers: '+esc((h.blockers||[]).join(" | "))+'\nReason: '+esc(h.reason||"")+'\nNext action: '+esc(h.next_action||"")+'\n\nAUTO-TRADE mantiene el riesgo bloqueado; no se sintetiza Health NORMAL.';}}catch(e){$("healthReadinessStatus").textContent="BLOQUEADO · READINESS ERROR";$("healthReadinessStatus").className="badge warn";$("healthReadinessLog").innerHTML='<span class="err">READ-ONLY HEALTH INSPECTION FAILED CLOSED</span>\n'+esc(e.message)}finally{b.disabled=false}}
$("healthReadinessCheck").addEventListener("click",checkHealthReadiness);
'''


def _build_meta() -> dict[str, object]:
    meta = approval._build_meta()
    meta.update(
        {
            "crypto_execution_health_readiness": True,
            "crypto_execution_health_readiness_read_only": True,
            "crypto_execution_strategy_id": EXECUTION_STRATEGY_ID,
            "crypto_execution_final_guard_uat": False,
            "crypto_execution_approval_consumption": False,
            "crypto_execution_oms_staging": False,
            "crypto_execution_lifecycle_unknown": False,
            "crypto_execution_broker_post": False,
        }
    )
    return meta


def _crypto_page(token: str) -> bytes:
    page = approval._crypto_page(token).decode("utf-8")
    frontier = '<section class="card"><div class="body"><div class="callout red"><strong>Frontera actual:</strong> esta interfaz puede registrar una aprobación humana UAT de un solo uso, pero NO puede consumirla, abrir Final Guard ni enviar una orden.'
    if frontier not in page:
        raise DashboardError("execution readiness frontier anchor drifted; refusing unsafe partial injection")
    page = page.replace(frontier, READINESS_SECTION + frontier, 1)
    closing = "</script></body></html>"
    if closing not in page:
        raise DashboardError("execution readiness script anchor is missing")
    page = page.replace(closing, READINESS_JS + "\n</script></body></html>", 1)
    return page.encode("utf-8")


class DashboardServer(approval.DashboardServer):
    def __init__(self, address: tuple[str, int], csrf_token: str) -> None:
        super().__init__(address, csrf_token)
        self.RequestHandlerClass = DashboardHandler


class DashboardHandler(approval.DashboardHandler):
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
        if urlparse(self.path).path != "/api/execution-health-readiness":
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
            result = inspect_health_readiness(
                workspace_path=Path(workspace_raw),
                now=datetime.now(timezone.utc),
                strategy_id=EXECUTION_STRATEGY_ID,
            )
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "readiness": result,
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
                f"AUTO-TRADE execution Health readiness diagnostic {diagnostic_id}: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
            self._json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "error": f"read-only execution Health readiness failed closed [{diagnostic_id}]",
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
    approval._base._enable_line_buffered_console()
    args = approval._base._parser().parse_args(argv)
    try:
        approval._base._require_safe_runtime()
        server = _start_server(args.host, args.port)
    except DashboardError as exc:
        print(f"AUTO-TRADE DASHBOARD BLOCKED: {exc}", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{server.server_port}/"
    print("AUTO-TRADE R6 — CRYPTO PAPER EXECUTION GATE READINESS / NO POST")
    print(f"Hub: {url}")
    print(f"Crypto: {url}crypto")
    print(f"Execution strategy Health target: {EXECUTION_STRATEGY_ID}")
    print("Health inspection: READ ONLY")
    print("Approval consumption: UNAVAILABLE")
    print("OMS SUBMITTING: UNAVAILABLE")
    print("Lifecycle UNKNOWN: UNAVAILABLE")
    print("External PAPER write: DISABLED")
    print("LIVE trading: BLOCKED")
    if not args.no_browser:
        import threading
        threading.Timer(0.35, lambda: approval._base._open_browser(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
