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


_BASE_PATH = Path(__file__).with_name("mac_dashboard_cold_start.py")
_SPEC = importlib.util.spec_from_file_location("autotrade_mac_dashboard_cold_start_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load certified cold-start Portfolio Control Center")
cold_start = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = cold_start
_SPEC.loader.exec_module(cold_start)


DashboardError = cold_start.DashboardError
ROOT = cold_start.ROOT
WRITE_ENV = cold_start.WRITE_ENV
_base = cold_start._base

ATTESTATION_SECTION = r'''
<section class="card" id="coldStartAttestationCard"><div class="head"><h2>8 · Cold-Start Qualification Attestation · NO EXECUTION</h2><span id="coldStartAttestationStatus" class="badge warn">9 GET PAPER · NO POST</span></div><div class="body">
<div class="callout amber"><strong>9 GET PAPER / LOCAL ATTESTATION / NO ORDER.</strong> Este gate revalida el Portfolio v1 contra cuenta PAPER plana y ejecuta un preview certificado BTC/USD con Safety + OMS en runtime temporal. <strong>Health debe seguir ausente y el kill switch debe seguir activo.</strong> El resultado es sólo una attestation de candidato para el primer canary técnico; no consume aprobación, no abre Final Guard y no autoriza POST.</div>
<div style="height:12px"></div><div class="actions"><button id="coldStartAttestation" class="btn preview">Atestar candidato cold-start · 9 GET PAPER / NO POST</button></div><div style="height:14px"></div>
<div class="canaryGrid">
<div class="step"><h3>Portfolio + broker binding</h3><div class="code" id="coldStartAttestationPortfolioCode">Aún no atestado.</div></div>
<div class="step"><h3>Candidato BTC/USD</h3><div class="code" id="coldStartAttestationCandidateCode">Aún no construido.</div></div>
<div class="step"><h3>Health / Safety</h3><div class="code" id="coldStartAttestationHealthCode">Health = MISSING · EXPECTED\nkill_switch = DEBE SEGUIR ACTIVO\nhealth_override = false</div></div>
<div class="step"><h3>Autoridad</h3><div class="code" id="coldStartAttestationAuthorityCode">qualification_candidate = false\nqualification_completed = false\nnew_human_approval_required = true\napproval_consumed = false\nFinal Guard = unavailable\nexecution_authority = NONE\ncapital_authority = NONE\nPOST = false\nLIVE = BLOCKED</div></div>
</div>
<div class="log" id="coldStartAttestationLog">La attestation cold-start todavía no se ha generado.</div>
</div></section>
'''

ATTESTATION_JS = r'''
async function attestColdStartQualification(){const b=$("coldStartAttestation");b.disabled=true;$("coldStartAttestationStatus").textContent="REVALIDANDO · 9 GET PAPER";$("coldStartAttestationStatus").className="badge warn";$("coldStartAttestationLog").textContent="Revalidando Portfolio v1 + cuenta plana + preview BTC/USD. No se consume aprobación y no existe order POST.";try{const r=await fetch("/api/cold-start-qualification-attestation",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":CSRF},body:JSON.stringify({workspace:$("workspace").value,paper_key:$("key").value,paper_secret:$("secret").value}),cache:"no-store"});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||"cold-start qualification attestation failed");const a=d.attestation;$("coldStartAttestationPortfolioCode").textContent="broker_reads = "+a.broker_reads+"\naccount_reference = "+a.account_reference+"\naccount_fingerprint = "+a.fresh_account_fingerprint+"\nflat_fingerprint = "+a.fresh_flat_account_fingerprint+"\npositions = "+a.position_count+"\nopen_orders = "+a.open_order_count+"\nportfolio_version = "+a.portfolio_version+"\nsnapshot_id = "+a.portfolio_snapshot_id+"\nequity = $"+a.portfolio_equity+"\ngross_exposure = "+a.portfolio_gross_exposure+"\nnet_exposure = "+a.portfolio_net_exposure;$("coldStartAttestationCandidateCode").textContent="symbol = "+a.symbol+"\nscope = "+a.scope+"\npreview_status = "+a.preview_status+"\nnotional = $"+a.preview_notional+"\nhard_cap = $"+a.preview_safety_hard_cap+"\npackage_hash = "+a.preview_package_hash+"\npayload_hash = "+a.preview_payload_hash+"\nclient_order_id = "+a.preview_client_order_id+"\nprotection_required = "+a.protection_required_after_reconciled_fill+"\nambiguity = "+a.ambiguity_policy+"\nattestation_hash = "+a.attestation_hash+"\nvalid_until = "+a.valid_until;$("coldStartAttestationHealthCode").textContent="kill_switch_active = "+a.kill_switch_active+"\nkill_switch_reason = "+a.kill_switch_reason+"\nkill_switch_reset = "+a.kill_switch_reset+"\nStrategy Health rows = "+a.strategy_health_state_rows+"\nPortfolio Health rows = "+a.portfolio_health_state_rows+"\nbridge rows = "+a.health_bridge_rows+"\nHEALTH MISSING · EXPECTED = "+(a.strategy_health_expected_missing&&a.portfolio_health_expected_missing)+"\nhealth_override_authorized = "+a.health_override_authorized+"\nnormal_health_path_modified = "+a.health_normal_path_modified;$("coldStartAttestationAuthorityCode").textContent="qualification_candidate = "+a.qualification_candidate+"\nqualification_completed = "+a.qualification_completed+"\nprofitability_evidence = "+a.profitability_evidence+"\nnew_human_approval_required = "+a.new_human_approval_required_for_any_future_execution+"\napproval_consumed = "+a.approval_consumed+"\nfinal_guard_opened = "+a.final_guard_opened+"\noms_submitting = "+a.oms_submitting+"\nlifecycle_unknown = "+a.lifecycle_unknown+"\ncredentials_persisted = "+a.credentials_persisted+"\nbroker_write_performed = "+a.broker_write_performed+"\nexternal_post_authorized = "+a.external_post_authorized+"\nexecution_authority = "+a.execution_authority+"\ncapital_authority = "+a.capital_authority+"\nreusable_for_real_execution = "+a.reusable_for_real_execution+"\nLIVE = "+a.live_trading;$("coldStartAttestationStatus").textContent="QUALIFICATION CANDIDATE · NO EXECUTION";$("coldStartAttestationStatus").className="badge good";$("coldStartAttestationLog").innerHTML='<span class="ok">COLD-START QUALIFICATION ATTESTATION PASS</span>\nEl primer candidato técnico quedó hash-bound y corto de vida.\n\nHealth sigue MISSING de forma explícita. Kill switch sigue activo.\nEsta attestation NO es permiso de ejecución.\nAprobación nueva será obligatoria en cualquier gate futuro.\nBroker POST: NO.';}catch(e){$("coldStartAttestationStatus").textContent="BLOQUEADO · ATTESTATION";$("coldStartAttestationStatus").className="badge warn";$("coldStartAttestationLog").innerHTML='<span class="err">COLD-START QUALIFICATION ATTESTATION FAILED CLOSED</span>\n'+esc(e.message)}finally{b.disabled=false}}
$("coldStartAttestation").addEventListener("click",attestColdStartQualification);
'''


def _build_meta() -> dict[str, object]:
    meta = cold_start._build_meta()
    meta.update(
        {
            "crypto_cold_start_qualification_attestation": True,
            "crypto_cold_start_attestation_broker_reads": 9,
            "crypto_cold_start_attestation_first_canary_only": True,
            "crypto_cold_start_attestation_health_override": False,
            "crypto_cold_start_attestation_execution_authority": "NONE",
            "crypto_execution_final_guard_uat": False,
            "crypto_execution_approval_consumption": False,
            "crypto_execution_oms_staging": False,
            "crypto_execution_lifecycle_unknown": False,
            "crypto_execution_broker_post": False,
        }
    )
    return meta


def _crypto_page(token: str) -> bytes:
    page = cold_start._crypto_page(token).decode("utf-8")
    frontier = '<section class="card"><div class="body"><div class="callout red"><strong>Frontera actual:</strong> esta interfaz puede registrar una aprobación humana UAT de un solo uso, pero NO puede consumirla, abrir Final Guard ni enviar una orden.'
    if frontier not in page:
        raise DashboardError("attestation frontier anchor drifted; refusing unsafe partial injection")
    page = page.replace(frontier, ATTESTATION_SECTION + frontier, 1)
    closing = "</script></body></html>"
    if closing not in page:
        raise DashboardError("attestation script anchor is missing")
    page = page.replace(closing, ATTESTATION_JS + "\n</script></body></html>", 1)
    return page.encode("utf-8")


def _run_attestation(*, workspace: str, credentials: tuple[str, str]) -> dict[str, object]:
    argv = [
        str(_base.PYTHON),
        "scripts/mac_crypto_cold_start_qualification_attestation.py",
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
        timeout=90,
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
            error = lines[-1][:1000] if lines else f"cold-start attestation child returncode={completed.returncode}"
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "json": parsed,
        "error": error,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "broker_write_performed": False,
        "external_post_authorized": False,
        "approval_consumed": False,
        "final_guard_opened": False,
        "execution_authority": "NONE",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "at": datetime.now(timezone.utc).isoformat(),
    }


class DashboardServer(cold_start.DashboardServer):
    def __init__(self, address: tuple[str, int], csrf_token: str) -> None:
        super().__init__(address, csrf_token)
        self.RequestHandlerClass = DashboardHandler


class DashboardHandler(cold_start.DashboardHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/crypto":
            self._write_body(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                _crypto_page(self.dashboard_server.csrf_token),
            )
            return
        if parsed.path == "/api/meta":
            self._json(HTTPStatus.OK, {"ok": True, "meta": _build_meta()})
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/cold-start-qualification-attestation":
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
            result = _run_attestation(workspace=workspace, credentials=credentials)
            if not result.get("ok"):
                raise DashboardError(str(result.get("error") or "cold-start qualification attestation blocked"))
            attestation = result.get("json")
            if not isinstance(attestation, dict):
                raise DashboardError("cold-start attestation child returned no structured result")
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "attestation": attestation,
                    "broker_write_performed": False,
                    "external_post_authorized": False,
                    "approval_consumed": False,
                    "final_guard_opened": False,
                    "execution_authority": "NONE",
                    "capital_authority": "NONE",
                    "live_trading": "BLOCKED",
                },
            )
        except Exception as exc:
            diagnostic_id = secrets.token_hex(8)
            print(
                f"AUTO-TRADE cold-start qualification attestation diagnostic {diagnostic_id}: {type(exc).__name__}",
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
                    "final_guard_opened": False,
                    "execution_authority": "NONE",
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
    print("AUTO-TRADE R6 — COLD-START QUALIFICATION ATTESTATION / NO EXECUTION")
    print(f"Hub: {url}")
    print(f"Crypto: {url}crypto")
    print("Cold-start attestation: 9 GET PAPER + LOCAL HASH-BOUND EVIDENCE")
    print("Health state: MISSING / EXPECTED / NO OVERRIDE")
    print("Commissioning kill switch reset: UNAVAILABLE")
    print("Approval consumption: UNAVAILABLE")
    print("Final Guard: UNAVAILABLE")
    print("OMS SUBMITTING: UNAVAILABLE")
    print("Lifecycle UNKNOWN: UNAVAILABLE")
    print("Execution authority: NONE")
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
