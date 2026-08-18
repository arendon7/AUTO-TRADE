from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
import importlib.util
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    crypto_operator_confirmation_challenge,
)


_BASE_PATH = Path(__file__).with_name("mac_dashboard_cold_start_attestation.py")
_SPEC = importlib.util.spec_from_file_location("autotrade_mac_dashboard_cold_start_attestation_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load certified cold-start attestation Control Center")
base = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = base
_SPEC.loader.exec_module(base)

_ISSUER_PATH = Path(__file__).with_name("r6_issue_crypto_operator_decision_uat.py")
_ISSUER_SPEC = importlib.util.spec_from_file_location("autotrade_crypto_operator_uat_issuer_binding", _ISSUER_PATH)
if _ISSUER_SPEC is None or _ISSUER_SPEC.loader is None:
    raise RuntimeError("cannot load canonical crypto operator UAT issuer")
issuer = importlib.util.module_from_spec(_ISSUER_SPEC)
sys.modules[_ISSUER_SPEC.name] = issuer
_ISSUER_SPEC.loader.exec_module(issuer)

_BINDING_PATH = Path(__file__).with_name("mac_crypto_cold_start_final_guard_binding.py")
_BINDING_SPEC = importlib.util.spec_from_file_location("autotrade_cold_start_final_guard_binding", _BINDING_PATH)
if _BINDING_SPEC is None or _BINDING_SPEC.loader is None:
    raise RuntimeError("cannot load cold-start Final Guard binding module")
binding = importlib.util.module_from_spec(_BINDING_SPEC)
sys.modules[_BINDING_SPEC.name] = binding
_BINDING_SPEC.loader.exec_module(binding)

ROOT = base.ROOT
DashboardError = base.DashboardError
_base = base._base
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RESULT_TTL_SECONDS = 120
MAX_RESULTS = 16

BINDING_SECTION = r'''
<section class="card" id="coldStartFinalGuardBindingCard"><div class="head"><h2>9 · Cold-Start Final Guard Binding · UAT / NO EXECUTION</h2><span id="coldStartBindingStatus" class="badge warn">NO POST · NO CONSUME</span></div><div class="body">
<div class="callout amber"><strong>15 GET PAPER / LOCAL BINDING / NO ORDER.</strong> Este gate regenera una attestation cold-start y un paquete NUEVO de aprobación, los liga al Portfolio v1 y exige un challenge humano nuevo. <strong>No abre el Final Guard normal, no ignora Health, no resetea el kill switch y no consume la aprobación.</strong></div>
<div style="height:12px"></div><div class="actions"><button id="coldStartBindingPrepare" class="btn preview">Preparar binding fresco · 15 GET PAPER / NO POST</button></div><div style="height:14px"></div>
<div class="canaryGrid">
<div class="step"><h3>Binding preparado</h3><div class="code" id="coldStartBindingPackageCode">Aún no preparado.</div></div>
<div class="step"><h3>Challenge exacto</h3><div class="code" id="coldStartBindingChallengeCode">Aún no preparado.</div></div>
<div class="step"><h3>Sellar binding UAT</h3><div class="field"><label>Operator ID</label><input id="coldStartBindingOperator" class="input" autocomplete="off" spellcheck="false" placeholder="operator-001" maxlength="128"></div><div class="field"><label>Escribe exactamente el challenge</label><input id="coldStartBindingPhrase" class="input" autocomplete="off" spellcheck="false"></div><div class="actions"><button id="coldStartBindingSeal" class="btn primary" disabled>Sellar binding UAT · NO POST</button></div></div>
<div class="step"><h3>Receipt de binding</h3><div class="code" id="coldStartBindingReceiptCode">No existe binding sellado.</div></div>
</div>
<div class="log" id="coldStartBindingLog">El binding cold-start todavía no se ha preparado.</div>
</div></section>
'''

BINDING_JS = r'''
let coldStartBindingPrepareId=null,coldStartBindingChallenge=null,coldStartBindingPrepared=false,coldStartBindingBusy=false;
function coldStartBindingRequestId(){return previewRequestId()}
async function recoverColdStartBinding(kind,id){for(let i=0;i<120;i++){try{const r=await fetch("/api/cold-start-final-guard-"+kind+"-result?request_id="+encodeURIComponent(id),{cache:"no-store"});if(r.status===202){await wait(500);continue}const d=await r.json();if(r.ok&&d.state==="COMPLETE"&&d.result)return d.result;if(r.status===404){await wait(500);continue}throw new Error(d.error||"No se pudo recuperar el mismo intento.")}catch(e){if(i===119)throw e;await wait(500)}}throw new Error("El resultado del mismo intento no estuvo disponible antes del timeout.")}
function renderColdStartBindingPrepared(d,id){if(!(d&&d.ok&&d.binding))throw new Error(d?.error||"No se recibió binding preparado.");const b=d.binding;coldStartBindingPrepareId=id;coldStartBindingChallenge=b.operator_challenge;coldStartBindingPrepared=true;$("coldStartBindingStatus").textContent="BINDING PREPARADO · REQUIERE CHALLENGE";$("coldStartBindingStatus").className="badge good";$("coldStartBindingPackageCode").textContent="broker_reads = "+b.broker_reads+"\nqualification_attestation_hash = "+b.qualification_attestation_hash+"\nbinding_package_hash = "+b.binding_package_hash+"\npayload_hash = "+b.binding_payload_hash+"\nclient_order_id = "+b.binding_client_order_id+"\nnotional = $"+b.binding_notional+"\nhard_cap = $"+b.binding_safety_hard_cap+"\nportfolio_version = "+b.portfolio_version+"\nkill_switch_active = "+b.kill_switch_active+"\nHealth missing expected = "+b.health_missing_expected+"\nnormal Final Guard opened = "+b.normal_final_guard_opened+"\nexecution_authority = "+b.execution_authority+"\nvalid_until = "+b.valid_until;$("coldStartBindingChallengeCode").textContent=b.operator_challenge+"\n\noperator_attempt_id = "+b.operator_attempt_id+"\noperator_preparation_hash = "+b.operator_preparation_hash+"\napproval_recorded = "+b.operator_decision_recorded+"\napproval_consumed = "+b.operator_decision_consumed;$("coldStartBindingSeal").disabled=false;$("coldStartBindingLog").innerHTML='<span class="ok">COLD-START FINAL GUARD BINDING PREPARED</span>\nEl paquete de binding es nuevo y distinto de la attestation de qualification.\nHealth sigue MISSING y el kill switch sigue activo.\nNormal Final Guard: CERRADO.\nBroker POST: NO.'}
async function prepareColdStartBinding(){if(coldStartBindingBusy)return;coldStartBindingBusy=true;const id=coldStartBindingRequestId();coldStartBindingPrepareId=id;$("coldStartBindingPrepare").disabled=true;$("coldStartBindingSeal").disabled=true;$("coldStartBindingStatus").textContent="REGENERANDO · 15 GET PAPER";$("coldStartBindingStatus").className="badge warn";$("coldStartBindingLog").textContent="Regenerando attestation + paquete de aprobación para un único binding UAT. No hay replay ni POST.";const body={workspace:$("workspace").value,paper_key:$("key").value,paper_secret:$("secret").value,binding_request_id:id};try{let d;try{const r=await fetch("/api/cold-start-final-guard-prepare",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":CSRF},body:JSON.stringify(body),cache:"no-store"});d=await r.json()}catch(networkError){$("coldStartBindingStatus").textContent="RECUPERANDO MISMO PREPARE · NO REPLAY";d=await recoverColdStartBinding("prepare",id)}renderColdStartBindingPrepared(d,id)}catch(e){coldStartBindingPrepared=false;$("coldStartBindingStatus").textContent="BLOQUEADO · BINDING";$("coldStartBindingStatus").className="badge warn";$("coldStartBindingLog").innerHTML='<span class="err">BINDING PREPARE FAILED CLOSED</span>\n'+esc(e.message)}finally{coldStartBindingBusy=false;$("coldStartBindingPrepare").disabled=false}}
function renderColdStartBindingReceipt(d){if(!(d&&d.ok&&d.receipt))throw new Error(d?.error||"No se recibió receipt de binding.");const r=d.receipt;$("coldStartBindingStatus").textContent="BINDING UAT SELLADO · NO EXECUTION";$("coldStartBindingStatus").className="badge good";$("coldStartBindingReceiptCode").textContent="binding_status = "+r.status+"\nbinding_receipt_hash = "+r.binding_receipt_hash+"\noperator_id = "+r.operator_id+"\noperator_decision_status = "+r.operator_decision_status+"\noperator_decision_consumed = "+r.operator_decision_consumed+"\nnormal_final_guard_opened = "+r.normal_final_guard_opened+"\nfinal_guard_pre_consume_authorized = "+r.final_guard_pre_consume_authorized+"\nhealth_override_authorized = "+r.health_override_authorized+"\nkill_switch_active = "+r.kill_switch_active+"\nnew_execution_approval_required = "+r.new_execution_approval_required+"\nexecution_authority = "+r.execution_authority+"\ncapital_authority = "+r.capital_authority+"\nPOST = "+r.external_post_authorized+"\nLIVE = "+r.live_trading;$("coldStartBindingLog").innerHTML='<span class="ok">COLD-START FINAL GUARD BINDING UAT PASS</span>\nBinding exacto + decisión humana ISSUED quedaron ligados.\nLa decisión NO fue consumida.\nFinal Guard normal NO fue abierto.\nExecution authority: NONE.\nBroker POST: NO.';$("coldStartBindingSeal").disabled=true}
async function sealColdStartBinding(){if(coldStartBindingBusy||!coldStartBindingPrepared||!coldStartBindingPrepareId)return;const op=$("coldStartBindingOperator").value.trim(),confirmation=$("coldStartBindingPhrase").value;if(!op){$("coldStartBindingLog").innerHTML='<span class="err">Falta Operator ID.</span>';return}if(confirmation!==coldStartBindingChallenge){$("coldStartBindingLog").innerHTML='<span class="err">El challenge no coincide exactamente. No se selló ningún binding.</span>';return}coldStartBindingBusy=true;const id=coldStartBindingRequestId();$("coldStartBindingSeal").disabled=true;$("coldStartBindingStatus").textContent="SELLANDO BINDING UAT";$("coldStartBindingStatus").className="badge warn";const body={binding_request_id:coldStartBindingPrepareId,seal_request_id:id,operator_id:op,confirmation:confirmation};try{let d;try{const r=await fetch("/api/cold-start-final-guard-seal",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":CSRF},body:JSON.stringify(body),cache:"no-store"});d=await r.json()}catch(networkError){$("coldStartBindingStatus").textContent="RECUPERANDO MISMO SEAL · NO REPLAY";d=await recoverColdStartBinding("seal",id)}renderColdStartBindingReceipt(d)}catch(e){$("coldStartBindingStatus").textContent="SEAL BLOQUEADO";$("coldStartBindingStatus").className="badge warn";$("coldStartBindingLog").innerHTML='<span class="err">NO SE SELLÓ EL BINDING</span>\n'+esc(e.message);if(coldStartBindingPrepared)$("coldStartBindingSeal").disabled=false}finally{coldStartBindingBusy=false}}
$("coldStartBindingPrepare").addEventListener("click",prepareColdStartBinding);$("coldStartBindingSeal").addEventListener("click",sealColdStartBinding);
'''


def _build_meta() -> dict[str, object]:
    meta = base._build_meta()
    meta.update(
        {
            "crypto_cold_start_final_guard_binding_uat": True,
            "crypto_cold_start_final_guard_binding_broker_reads": 15,
            "crypto_cold_start_final_guard_binding_health_override": False,
            "crypto_cold_start_final_guard_binding_normal_guard_opened": False,
            "crypto_cold_start_final_guard_binding_approval_consumption": False,
            "crypto_cold_start_final_guard_binding_execution_authority": "NONE",
            "crypto_execution_broker_post": False,
        }
    )
    return meta


def _crypto_page(token: str) -> bytes:
    page = base._crypto_page(token).decode("utf-8")
    frontier = '<section class="card"><div class="body"><div class="callout red"><strong>Frontera actual:</strong>'
    if frontier not in page:
        raise DashboardError("binding frontier anchor drifted; refusing unsafe partial injection")
    page = page.replace(frontier, BINDING_SECTION + frontier, 1)
    closing = "</script></body></html>"
    if closing not in page:
        raise DashboardError("binding script anchor is missing")
    page = page.replace(closing, BINDING_JS + "\n</script></body></html>", 1)
    return page.encode("utf-8")


def _request_id(payload: dict[str, object], key: str) -> str:
    value = str(payload.get(key) or "").strip().lower()
    if not REQUEST_ID_RE.fullmatch(value):
        raise DashboardError(f"invalid {key}")
    return value


def _run_prepare(payload: dict[str, object]) -> dict[str, object]:
    workspace = _base._workspace(payload)
    credentials = _base._paper_credentials(payload)
    argv = [
        str(_base.PYTHON),
        "scripts/mac_crypto_cold_start_final_guard_binding.py",
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
        timeout=180,
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
            error = lines[-1][:1000] if lines else f"binding prepare child returncode={completed.returncode}"
    return {
        "ok": completed.returncode == 0,
        "binding": parsed if completed.returncode == 0 else None,
        "error": error,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "broker_write_performed": False,
        "external_post_authorized": False,
        "execution_authority": "NONE",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


def _validate_preparation(material: dict[str, object]) -> tuple[dict[str, object], str, CryptoOperatorDecisionContext]:
    result = material.get("result")
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise DashboardError("binding preparation is missing or blocked")
    prepared = result.get("binding")
    if not isinstance(prepared, dict) or prepared.get("status") != "CRYPTO_COLD_START_FINAL_GUARD_BINDING_PREPARED_NO_EXECUTION":
        raise DashboardError("binding preparation contract is missing")
    context_payload = prepared.get("operator_context")
    challenge = prepared.get("operator_challenge")
    if not isinstance(context_payload, dict) or not isinstance(challenge, str):
        raise DashboardError("binding operator context/challenge is missing")
    context = CryptoOperatorDecisionContext.from_dict(context_payload)
    if crypto_operator_confirmation_challenge(context) != challenge:
        raise DashboardError("binding challenge/context mismatch")
    return prepared, challenge, context


def _fail_closed(message: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": message,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "approval_consumed": False,
        "normal_final_guard_opened": False,
        "execution_authority": "NONE",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


class DashboardServer(base.DashboardServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], csrf_token: str) -> None:
        super().__init__(address, csrf_token)
        self.RequestHandlerClass = DashboardHandler
        self._binding_lock = threading.RLock()
        self._binding_prepares: dict[str, dict[str, object]] = {}
        self._binding_seals: dict[str, dict[str, object]] = {}

    def _prune(self) -> None:
        cutoff = time.monotonic() - RESULT_TTL_SECONDS
        for store in (self._binding_prepares, self._binding_seals):
            for key in [k for k, v in store.items() if float(v["stored_at"]) < cutoff]:
                store.pop(key, None)
            if len(store) > MAX_RESULTS:
                ordered = sorted(store, key=lambda k: float(store[k]["stored_at"]))
                for key in ordered[: len(store) - MAX_RESULTS]:
                    store.pop(key, None)

    def begin_prepare(self, request_id: str, workspace: str) -> None:
        with self._binding_lock:
            self._prune()
            if request_id in self._binding_prepares:
                raise DashboardError("binding prepare request id already exists; no replay permitted")
            self._binding_prepares[request_id] = {"state": "IN_PROGRESS", "stored_at": time.monotonic(), "workspace": workspace}

    def finish_prepare(self, request_id: str, result: dict[str, object]) -> None:
        with self._binding_lock:
            current = self._binding_prepares.get(request_id)
            if current is None:
                return
            self._binding_prepares[request_id] = {"state": "COMPLETE", "stored_at": time.monotonic(), "workspace": current["workspace"], "result": result, "seal_request_id": None}
            self._prune()

    def prepare_status(self, request_id: str) -> dict[str, object] | None:
        with self._binding_lock:
            self._prune()
            value = self._binding_prepares.get(request_id)
            return dict(value) if value is not None else None

    def begin_seal(self, seal_id: str, prepare_id: str) -> dict[str, object]:
        with self._binding_lock:
            self._prune()
            if seal_id in self._binding_seals:
                raise DashboardError("binding seal request id already exists; no replay permitted")
            material = self._binding_prepares.get(prepare_id)
            if material is None or material.get("state") != "COMPLETE":
                raise DashboardError("binding preparation is missing, expired or incomplete")
            if material.get("seal_request_id") is not None:
                raise DashboardError("binding preparation has already been claimed for sealing")
            material["seal_request_id"] = seal_id
            material["stored_at"] = time.monotonic()
            self._binding_seals[seal_id] = {"state": "IN_PROGRESS", "stored_at": time.monotonic(), "prepare_id": prepare_id}
            return dict(material)

    def finish_seal(self, seal_id: str, result: dict[str, object]) -> None:
        with self._binding_lock:
            current = self._binding_seals.get(seal_id)
            if current is None:
                return
            self._binding_seals[seal_id] = {"state": "COMPLETE", "stored_at": time.monotonic(), "prepare_id": current["prepare_id"], "result": result}
            self._prune()

    def seal_status(self, seal_id: str) -> dict[str, object] | None:
        with self._binding_lock:
            self._prune()
            value = self._binding_seals.get(seal_id)
            return dict(value) if value is not None else None


class DashboardHandler(base.DashboardHandler):
    def _result(self, kind: str, request_id: str) -> None:
        record = self.dashboard_server.prepare_status(request_id) if kind == "prepare" else self.dashboard_server.seal_status(request_id)
        if record is None:
            value = _fail_closed("binding request id is unknown or expired")
            value.update({"state": "UNKNOWN", "request_id": request_id})
            self._json(HTTPStatus.NOT_FOUND, value)
            return
        if record["state"] == "IN_PROGRESS":
            value = _fail_closed("binding operation still in progress")
            value.update({"ok": True, "state": "IN_PROGRESS", "request_id": request_id})
            self._json(HTTPStatus.ACCEPTED, value)
            return
        self._json(HTTPStatus.OK, {"ok": True, "state": "COMPLETE", "request_id": request_id, "result": record["result"], "broker_write_performed": False, "external_post_authorized": False, "execution_authority": "NONE", "capital_authority": "NONE", "live_trading": "BLOCKED"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/crypto":
            self._write_body(HTTPStatus.OK, "text/html; charset=utf-8", _crypto_page(self.dashboard_server.csrf_token))
            return
        if parsed.path == "/api/meta":
            self._json(HTTPStatus.OK, {"ok": True, "meta": _build_meta()})
            return
        if parsed.path in {"/api/cold-start-final-guard-prepare-result", "/api/cold-start-final-guard-seal-result"}:
            request_id = parse_qs(parsed.query, keep_blank_values=True).get("request_id", [""])[0].strip().lower()
            if not REQUEST_ID_RE.fullmatch(request_id):
                self._json(HTTPStatus.BAD_REQUEST, _fail_closed("invalid binding request id"))
                return
            self._result("prepare" if "prepare" in parsed.path else "seal", request_id)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/api/cold-start-final-guard-prepare", "/api/cold-start-final-guard-seal"}:
            super().do_POST()
            return
        if not self._require_local_origin():
            self._json(HTTPStatus.FORBIDDEN, _fail_closed("origin rejected"))
            return
        if self.headers.get("X-CSRF-Token") != self.dashboard_server.csrf_token:
            self._json(HTTPStatus.FORBIDDEN, _fail_closed("csrf rejected"))
            return
        if path == "/api/cold-start-final-guard-prepare":
            request_id: str | None = None
            try:
                payload = self._read_payload()
                request_id = _request_id(payload, "binding_request_id")
                workspace = _base._workspace(payload)
                self.dashboard_server.begin_prepare(request_id, workspace)
                result = _run_prepare(payload)
                self.dashboard_server.finish_prepare(request_id, result)
                if not result.get("ok"):
                    raise DashboardError(str(result.get("error") or "binding prepare blocked"))
            except subprocess.TimeoutExpired:
                value = _fail_closed("binding preparation timed out")
                if request_id is not None:
                    self.dashboard_server.finish_prepare(request_id, value)
                self._json(HTTPStatus.REQUEST_TIMEOUT, value)
                return
            except Exception as exc:
                value = _fail_closed(str(exc))
                if request_id is not None:
                    self.dashboard_server.finish_prepare(request_id, value)
                self._json(HTTPStatus.BAD_REQUEST, value)
                return
            self._json(HTTPStatus.OK, result)
            return
        seal_id: str | None = None
        try:
            payload = self._read_payload()
            prepare_id = _request_id(payload, "binding_request_id")
            seal_id = _request_id(payload, "seal_request_id")
            operator_id = str(payload.get("operator_id") or "").strip()
            confirmation = str(payload.get("confirmation") or "")
            material = self.dashboard_server.prepare_status(prepare_id)
            if material is None:
                raise DashboardError("binding preparation is missing or expired")
            prepared, challenge, context = _validate_preparation(material)
            if not operator_id:
                raise DashboardError("Operator ID is required")
            if not secrets.compare_digest(confirmation, challenge):
                raise DashboardError("human confirmation does not exactly match binding challenge")
            material = self.dashboard_server.begin_seal(seal_id, prepare_id)
            workspace_raw = material.get("workspace")
            if not isinstance(workspace_raw, str) or not workspace_raw:
                raise DashboardError("binding workspace is missing")
            approval_receipt = issuer.issue(
                workspace_path=Path(workspace_raw),
                context_payload=context.to_dict(),
                operator_id=operator_id,
                confirmation=confirmation,
                now=datetime.now(timezone.utc),
            )
            sealed = binding.seal_binding(
                workspace_path=Path(workspace_raw),
                preparation=prepared,
                approval_receipt=approval_receipt,
                now=datetime.now(timezone.utc),
            )
            result = {"ok": True, "receipt": sealed, "broker_write_performed": False, "external_post_authorized": False, "approval_consumed": False, "normal_final_guard_opened": False, "execution_authority": "NONE", "capital_authority": "NONE", "live_trading": "BLOCKED"}
            self.dashboard_server.finish_seal(seal_id, result)
        except Exception as exc:
            value = _fail_closed(str(exc))
            if seal_id is not None:
                self.dashboard_server.finish_seal(seal_id, value)
            self._json(HTTPStatus.BAD_REQUEST, value)
            return
        self._json(HTTPStatus.OK, result)


def _start_server(host: str, port: int) -> DashboardServer:
    if host != "127.0.0.1":
        raise DashboardError("Dashboard may bind only to 127.0.0.1")
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
    print("AUTO-TRADE R6 — COLD-START FINAL GUARD BINDING UAT / NO EXECUTION")
    print(f"Crypto: {url}crypto")
    print("Binding prepare: 15 GET PAPER / NO POST")
    print("Human approval: ISSUED UAT ONLY / NEVER CONSUMED")
    print("Normal Final Guard: CLOSED")
    print("Health override: FORBIDDEN")
    print("Commissioning kill switch: MUST REMAIN ACTIVE")
    print("OMS SUBMITTING: UNAVAILABLE")
    print("Lifecycle UNKNOWN: UNAVAILABLE")
    print("Execution authority: NONE")
    print("External PAPER write: DISABLED")
    print("LIVE trading: BLOCKED")
    if not args.no_browser:
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
