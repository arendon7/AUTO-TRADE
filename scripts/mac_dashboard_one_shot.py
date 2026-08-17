from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
    crypto_operator_confirmation_challenge,
)
from autotrade.persistence import SQLiteRuntime


_BASE_PATH = Path(__file__).with_name("mac_dashboard.py")
_BASE_SPEC = importlib.util.spec_from_file_location("autotrade_mac_dashboard_base", _BASE_PATH)
if _BASE_SPEC is None or _BASE_SPEC.loader is None:
    raise RuntimeError("cannot load certified base Mac Control Center")
_base = importlib.util.module_from_spec(_BASE_SPEC)
sys.modules[_BASE_SPEC.name] = _base
_BASE_SPEC.loader.exec_module(_base)

ROOT = _base.ROOT
PYTHON = _base.PYTHON
HTML_PATH = _base.HTML_PATH
HUB_HTML_PATH = _base.HUB_HTML_PATH
CRYPTO_HTML_PATH = _base.CRYPTO_HTML_PATH
WRITE_ENV = _base.WRITE_ENV
KEY_ENV = _base.KEY_ENV
SECRET_ENV = _base.SECRET_ENV
SAFE_ACTIONS = _base.SAFE_ACTIONS
DashboardError = _base.DashboardError
ActionSpec = _base.ActionSpec

# Existing Mac dashboard static-contract anchors remain explicit here because this
# wrapper is the process launched by Finder for the one-shot approval UAT build.
BASE_SAFE_SURFACES = ("scripts/mac_safe_console.py", "scripts/mac_crypto_paper_rehearsal.py")
INHERITED_SECURITY_HEADERS = ("X-CSRF-Token", "Cache-Control", "Content-Security-Policy")
PRIMARY_ROUTES = {"/equities": HTML_PATH, "/crypto": CRYPTO_HTML_PATH}
MAC_BROWSER_OPEN = "/usr/bin/open"
_WRAPPER_META = {
    "order_execution_from_dashboard": False,
    "crypto_execution_from_dashboard": False,
    "equity_execution_from_dashboard": False,
    "native_multi_asset_control_center": True,
    "asset_classes": ["US_EQUITY", "CRYPTO"],
}

APPROVAL_ID_RE = re.compile(r"^[0-9a-f]{32}$")
APPROVAL_RESULT_TTL_SECONDS = 120
MAX_APPROVAL_RESULTS = 16
APPROVAL_DB_DIR = "qualification_uat"
APPROVAL_DB_NAME = "crypto_one_shot_approval_uat.sqlite3"
APPROVAL_PREPARE_SCRIPT = "scripts/mac_crypto_approval_prepare.py"

APPROVAL_SECTION = r'''
<section class="card" id="approvalGateCard"><div class="head"><h2>4 · Aprobación humana de un solo uso · UAT</h2><span id="approvalStatus" class="badge warn">BLOQUEADO HASTA PREVIEW PASS</span></div><div class="body">
<div class="callout amber"><strong>NO POST.</strong> Este gate vuelve a generar evidencia y una candidata fresca. Después exige escribir exactamente el challenge y registra una aprobación humana tamper-evident de vida corta. Este build NO puede consumir esa aprobación, abrir Final Guard ni enviar una orden.</div>
<div style="height:12px"></div><div class="actions"><button id="approvalPrepare" class="btn preview" disabled>Preparar aprobación fresca · NO POST</button></div><div style="height:14px"></div>
<div class="canaryGrid">
<div class="step"><h3>Paquete fresco para aprobación</h3><p>Se genera después del preview. No reutiliza el paquete anterior.</p><div class="code" id="approvalEntryCode">Aún no preparado.</div></div>
<div class="step"><h3>Challenge exacto</h3><p>Debe copiarse exactamente. La ventana de UAT es corta y nunca supera el deadline del paquete.</p><div class="code" id="approvalChallengeCode">Aún no preparado.</div></div>
<div class="step"><h3>Registrar decisión humana</h3><div class="field"><label>Operator ID</label><input id="operatorId" class="input" autocomplete="off" spellcheck="false" placeholder="operator-001" maxlength="128"></div><div class="field"><label>Escribe exactamente el challenge</label><input id="approvalPhrase" class="input" autocomplete="off" spellcheck="false"></div><div class="actions"><button id="approvalRecord" class="btn primary" disabled>Registrar aprobación · NO POST</button></div></div>
<div class="step"><h3>Receipt de autoridad</h3><p>La decisión queda ISSUED, nunca CONSUMED, y no es reutilizable para ejecución real.</p><div class="code" id="approvalReceiptCode">No existe aprobación registrada.</div></div>
</div>
<div class="log" id="approvalLog">Gate de aprobación no ejecutado.</div>
</div></section>
'''

APPROVAL_JS = r'''
let approvalPreparedId=null,approvalChallenge=null,approvalPrepared=false,approvalBusy=false;
function approvalRequestId(){return previewRequestId()}
function resetApproval(){approvalPreparedId=null;approvalChallenge=null;approvalPrepared=false;approvalBusy=false;const p=$("approvalPrepare"),r=$("approvalRecord");if(p)p.disabled=true;if(r)r.disabled=true;if($("approvalStatus")){ $("approvalStatus").textContent="BLOQUEADO HASTA PREVIEW PASS";$("approvalStatus").className="badge warn" }if($("approvalEntryCode"))$("approvalEntryCode").textContent="Aún no preparado.";if($("approvalChallengeCode"))$("approvalChallengeCode").textContent="Aún no preparado.";if($("approvalReceiptCode"))$("approvalReceiptCode").textContent="No existe aprobación registrada.";if($("approvalLog"))$("approvalLog").textContent="Gate de aprobación no ejecutado.";if($("approvalPhrase"))$("approvalPhrase").value=""}
async function recoverApprovalPrepare(requestId){for(let attempt=0;attempt<120;attempt++){try{const r=await fetch("/api/canary-approval-prepare-result?request_id="+encodeURIComponent(requestId),{cache:"no-store"});if(r.status===202){await wait(500);continue}const d=await r.json();if(r.ok&&d.state==="COMPLETE"&&d.result)return d.result;if(r.status===404){await wait(500);continue}throw new Error(d.error||"No se pudo recuperar la preparación de aprobación.")}catch(e){if(attempt===119)throw e;await wait(500)}}throw new Error("La preparación de aprobación no estuvo disponible antes del timeout.")}
async function recoverApprovalRecord(requestId){for(let attempt=0;attempt<80;attempt++){try{const r=await fetch("/api/canary-approval-record-result?request_id="+encodeURIComponent(requestId),{cache:"no-store"});if(r.status===202){await wait(250);continue}const d=await r.json();if(r.ok&&d.state==="COMPLETE"&&d.result)return d.result;if(r.status===404){await wait(250);continue}throw new Error(d.error||"No se pudo recuperar el receipt de aprobación.")}catch(e){if(attempt===79)throw e;await wait(250)}}throw new Error("El receipt de aprobación no estuvo disponible antes del timeout.")}
function renderApprovalPrepared(d,requestId){if(!(d&&d.ok&&d.json))throw new Error(friendly(d)+" · "+technicalSummary(d));const j=d.json,e=j.entry,o=j.operator;if(j.status!=="CRYPTO_PAPER_ONE_SHOT_APPROVAL_PREPARED")throw new Error("La preparación de aprobación no devolvió el contrato esperado.");approvalPreparedId=requestId;approvalChallenge=o.approval_challenge;approvalPrepared=true;$("approvalStatus").textContent="CHALLENGE LISTO · NO POST";$("approvalStatus").className="badge good";$("approvalEntryCode").textContent=JSON.stringify(e.payload,null,2)+"\n\nnotional = $"+e.notional+"\nclient_order_id = "+e.dry_run_client_order_id+"\npackage_hash = "+e.package_hash+"\napproval_attempt_id = "+o.approval_attempt_id;$("approvalChallengeCode").textContent=o.approval_challenge+"\n\nCaduca paquete: "+o.execution_deadline+"\nUAT only = "+o.uat_only+"\nreusable_for_real_execution = "+o.reusable_for_real_execution;$("approvalRecord").disabled=false;$("approvalLog").innerHTML='<span class="ok">APROBACIÓN PREPARADA · AÚN NO REGISTRADA</span>\nBroker reads: '+esc(j.broker_reads)+'\nCapital Safety: '+esc(j.capital_safety)+'\nBroker write performed: NO\nExternal POST authorized: NO\nDecision consumed: NO\nLIVE: BLOCKED\n\nEscribe exactamente el challenge para registrar la decisión UAT.'}
async function prepareApproval(){if(approvalBusy||lastPass!=="BTC/USD"||pair()!=="BTC/USD"||$("previewStatus").textContent.indexOf("PREVIEW PASS")!==0)return;approvalBusy=true;const requestId=approvalRequestId(),body=requestBody();body.approval_request_id=requestId;$("approvalPrepare").disabled=true;$("approvalRecord").disabled=true;$("approvalStatus").textContent="REGENERANDO EVIDENCIA FRESCA";$("approvalStatus").className="badge warn";$("approvalLog").textContent="Generando un paquete NUEVO para aprobación. El preview anterior no se reutiliza.\napproval_request_id = "+requestId;try{let d;try{const r=await fetch("/api/canary-approval-prepare",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":CSRF},body:JSON.stringify(body),cache:"no-store"});d=await r.json()}catch(networkError){$("approvalStatus").textContent="RECUPERANDO MISMA PREPARACIÓN · NO REPLAY";d=await recoverApprovalPrepare(requestId)}renderApprovalPrepared(d,requestId)}catch(e){approvalPrepared=false;$("approvalStatus").textContent="APROBACIÓN BLOQUEADA";$("approvalStatus").className="badge warn";$("approvalLog").innerHTML='<span class="err">BLOQUEADO · SEGURO</span>\n'+esc(e.message)}finally{approvalBusy=false;if(!approvalPrepared&&lastPass==="BTC/USD"&&$("previewStatus").textContent.indexOf("PREVIEW PASS")===0)$("approvalPrepare").disabled=false}}
function renderApprovalReceipt(d){if(!(d&&d.ok&&d.receipt))throw new Error(d?.error||"No se recibió receipt de aprobación.");const r=d.receipt;$("approvalStatus").textContent="APROBACIÓN UAT REGISTRADA · NO POST";$("approvalStatus").className="badge good";$("approvalReceiptCode").textContent="status = "+r.decision_status+"\noperator_id = "+r.operator_id+"\ndecision_hash = "+r.decision_hash+"\npreparation_hash = "+r.preparation_hash+"\nevent_hash = "+r.event_hash+"\nissued_at = "+r.issued_at+"\nexpires_at = "+r.expires_at+"\nconsumed = "+r.decision_consumed+"\nreusable_for_real_execution = "+r.reusable_for_real_execution;$("approvalLog").innerHTML='<span class="ok">HUMAN APPROVAL UAT RECORDED</span>\nDecision status: '+esc(r.decision_status)+'\nDecision consumed: NO\nBroker write performed: NO\nExternal POST authorized: NO\nExecution authority: NONE\nLIVE: BLOCKED\n\nEl siguiente build real deberá regenerar todo y pedir una NUEVA aprobación.';$("approvalRecord").disabled=true;$("approvalPrepare").disabled=true;$("approvalPhrase").disabled=true;$("operatorId").disabled=true;const badge=$("approvalAuthorityBadge");if(badge){badge.textContent="UAT APPROVAL · RECORDED / NOT EXECUTABLE";badge.className="badge warn"}}
async function recordApproval(){if(approvalBusy||!approvalPrepared||!approvalPreparedId)return;const operatorId=$("operatorId").value.trim(),confirmation=$("approvalPhrase").value;if(!operatorId){$("approvalLog").innerHTML='<span class="err">Falta Operator ID.</span>';return}if(confirmation!==approvalChallenge){$("approvalLog").innerHTML='<span class="err">El challenge no coincide exactamente. No se registró ninguna aprobación.</span>';return}approvalBusy=true;const recordId=approvalRequestId(),body={approval_request_id:approvalPreparedId,record_request_id:recordId,operator_id:operatorId,confirmation:confirmation};$("approvalRecord").disabled=true;$("approvalStatus").textContent="REGISTRANDO DECISIÓN HUMANA";$("approvalStatus").className="badge warn";try{let d;try{const r=await fetch("/api/canary-approval-record",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":CSRF},body:JSON.stringify(body),cache:"no-store"});d=await r.json()}catch(networkError){$("approvalStatus").textContent="RECUPERANDO MISMO REGISTRO · NO REPLAY";d=await recoverApprovalRecord(recordId)}renderApprovalReceipt(d)}catch(e){$("approvalStatus").textContent="REGISTRO BLOQUEADO";$("approvalStatus").className="badge warn";$("approvalLog").innerHTML='<span class="err">NO SE REGISTRÓ AUTORIDAD</span>\n'+esc(e.message);if(approvalPrepared)$("approvalRecord").disabled=false}finally{approvalBusy=false}}
const _previewReset=resetPreview;resetPreview=function(){_previewReset();resetApproval()};
const _previewRender=renderPreview;renderPreview=function(d){_previewRender(d);resetApproval();if(d&&d.ok&&d.json&&d.json.status==="CRYPTO_PAPER_QUALIFICATION_PREVIEW_PASS"){$("approvalPrepare").disabled=false;$("approvalStatus").textContent="LISTO PARA PREPARAR APROBACIÓN · NO POST";$("approvalStatus").className="badge good"}};
$("approvalPrepare").addEventListener("click",prepareApproval);$("approvalRecord").addEventListener("click",recordApproval);$("approvalPhrase").addEventListener("input",()=>{if(approvalPrepared)$("approvalRecord").disabled=false});$("symbol").addEventListener("input",resetApproval);
'''


def _safe_env(*, paper_credentials: tuple[str, str] | None = None) -> dict[str, str]:
    env = _base._safe_env(paper_credentials=paper_credentials)
    env[WRITE_ENV] = "DISABLED"
    return env


def _build_meta() -> dict[str, object]:
    meta = _base._build_meta()
    meta.update(_WRAPPER_META)
    meta.update(
        {
            "one_shot_human_approval_uat": True,
            "one_shot_human_approval_consumption": False,
            "one_shot_human_approval_execution": False,
            "one_shot_human_approval_symbol": "BTC/USD",
            "one_shot_human_approval_write_authority": False,
        }
    )
    return meta


def _crypto_page(token: str) -> bytes:
    page = CRYPTO_HTML_PATH.read_text(encoding="utf-8")
    replacements = (
        (
            '<div class="muted"><span id="titlePair">BTC/USD</span> · rehearsal + qualification preview</div>',
            '<div class="muted"><span id="titlePair">BTC/USD</span> · rehearsal + preview + one-shot human approval UAT</div>',
        ),
        (
            '<span class="badge good">APPROVAL AUTHORITY · NONE</span>',
            '<span id="approvalAuthorityBadge" class="badge good">APPROVAL AUTHORITY · NONE</span>',
        ),
        (
            'Esta consola separa tres cosas: leer el broker, preparar un dry-run exacto y, en una versión posterior, autorizar un único intento PAPER. Este build sólo contiene las dos primeras.',
            'Esta consola separa cuatro cosas: leer el broker, preparar un dry-run exacto, ensayar una aprobación humana de un solo uso y, sólo en un build posterior, ejecutar PAPER. Este build llega hasta registrar la aprobación UAT; no puede consumirla ni enviar una orden.',
        ),
        (
            '<strong>NO POST.</strong> El preview ejecuta Safety + OMS + el coordinador crypto dentro de una base temporal que se destruye al terminar. No registra aprobación humana y no crea autoridad reutilizable.',
            '<strong>NO POST.</strong> El preview sigue siendo temporal. El gate adicional puede registrar una aprobación humana UAT ligada a un paquete nuevo, pero ese paquete pierde su runtime de ejecución y esta interfaz no puede consumir la aprobación ni alcanzar broker I/O.',
        ),
        (
            '<strong>Frontera actual:</strong> todavía no existe en esta interfaz ningún botón que pueda enviar una orden. El próximo gate será una versión separada y recertificada con aprobación humana de un solo uso. Tampoco hay afirmación de rentabilidad.',
            '<strong>Frontera actual:</strong> esta interfaz puede registrar una aprobación humana UAT de un solo uso, pero NO puede consumirla, abrir Final Guard ni enviar una orden. El próximo gate de ejecución será separado y recertificado. Tampoco hay afirmación de rentabilidad.',
        ),
    )
    for old, new in replacements:
        if old not in page:
            raise DashboardError("crypto approval UAT page anchor drifted; refusing unsafe partial injection")
        page = page.replace(old, new, 1)
    frontier = '<section class="card"><div class="body"><div class="callout red"><strong>Frontera actual:</strong>'
    if frontier not in page:
        raise DashboardError("crypto approval UAT frontier anchor is missing")
    page = page.replace(frontier, APPROVAL_SECTION + frontier, 1)
    closing = "</script></body></html>"
    if closing not in page:
        raise DashboardError("crypto approval UAT script anchor is missing")
    page = page.replace(closing, APPROVAL_JS + "\n</script></body></html>", 1)
    return page.replace("__CSRF_TOKEN__", token).encode("utf-8")


def _approval_id(payload: dict[str, object], key: str) -> str:
    value = str(payload.get(key) or "").strip().lower()
    if not APPROVAL_ID_RE.fullmatch(value):
        raise DashboardError(f"invalid {key}")
    return value


def _run_approval_prepare(payload: dict[str, object]) -> dict[str, object]:
    workspace = _base._workspace(payload)
    symbol = _base._crypto_symbol(payload)
    if symbol != "BTC/USD":
        raise DashboardError("first one-shot approval UAT is fixed to BTC/USD")
    credentials = _base._paper_credentials(payload)
    argv = [
        str(PYTHON),
        APPROVAL_PREPARE_SCRIPT,
        "--workspace", workspace,
        "--symbol", symbol,
        "--allow-paper-crypto-read",
    ]
    started = time.monotonic()
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        env=_safe_env(paper_credentials=credentials),
        text=True,
        capture_output=True,
        timeout=120,
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
            error = lines[-1][:1000] if lines else f"approval prepare child returncode={completed.returncode}"
    return {
        "ok": completed.returncode == 0,
        "action": "crypto_one_shot_approval_prepare",
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "error": error,
        "json": parsed,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "broker_write_performed": False,
        "external_post_authorized": False,
        "operator_approval_authority": "PREPARED_NOT_RECORDED" if completed.returncode == 0 else "NONE",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "at": datetime.now(timezone.utc).isoformat(),
    }


def _material_contract(material: dict[str, object]) -> tuple[dict[str, object], str, CryptoOperatorDecisionContext]:
    result = material.get("result")
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise DashboardError("approval material is not a successful fresh preparation")
    payload = result.get("json")
    if not isinstance(payload, dict) or payload.get("status") != "CRYPTO_PAPER_ONE_SHOT_APPROVAL_PREPARED":
        raise DashboardError("approval material contract is missing or blocked")
    operator = payload.get("operator")
    if not isinstance(operator, dict):
        raise DashboardError("approval operator material is missing")
    challenge = operator.get("approval_challenge")
    context_payload = operator.get("approval_context")
    if not isinstance(challenge, str) or not challenge:
        raise DashboardError("approval challenge is missing")
    if not isinstance(context_payload, dict):
        raise DashboardError("approval context is missing")
    context = CryptoOperatorDecisionContext.from_dict(context_payload)
    if context.symbol != "BTC/USD" or not context.attempt_id.startswith("approval-uat-"):
        raise DashboardError("approval context is not the exact BTC/USD UAT context")
    if crypto_operator_confirmation_challenge(context) != challenge:
        raise DashboardError("approval challenge does not match the cryptographic context")
    return payload, challenge, context


def _validate_confirmation(material: dict[str, object], *, operator_id: str, confirmation: str) -> None:
    operator = operator_id.strip()
    if not operator:
        raise DashboardError("Operator ID is required")
    if len(operator) > 128:
        raise DashboardError("Operator ID is unexpectedly long")
    _payload, challenge, _context = _material_contract(material)
    if not secrets.compare_digest(confirmation, challenge):
        raise DashboardError("human confirmation does not exactly match the one-shot challenge")


def _record_operator_approval(
    material: dict[str, object],
    *,
    operator_id: str,
    confirmation: str,
    now: datetime,
) -> dict[str, object]:
    _validate_confirmation(material, operator_id=operator_id, confirmation=confirmation)
    _payload, _challenge, context = _material_contract(material)
    instant = now.astimezone(timezone.utc)
    deadline = context.execution_deadline.astimezone(timezone.utc)
    if deadline <= instant + timedelta(seconds=5):
        raise DashboardError("approval package is too close to expiry; prepare a fresh challenge")
    expires_at = min(deadline, instant + timedelta(seconds=60))

    workspace_raw = material.get("workspace")
    if not isinstance(workspace_raw, str) or not workspace_raw:
        raise DashboardError("approval material workspace is missing")
    workspace = Path(workspace_raw).expanduser().resolve()
    if not workspace.is_dir() or workspace.is_symlink():
        raise DashboardError("approval workspace is unavailable or unsafe")
    evidence_dir = workspace / APPROVAL_DB_DIR
    if evidence_dir.exists() and evidence_dir.is_symlink():
        raise DashboardError("approval evidence directory may not be a symlink")
    evidence_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
    if evidence_dir.is_symlink():
        raise DashboardError("approval evidence directory became unsafe")
    database = evidence_dir / APPROVAL_DB_NAME
    if database.is_symlink():
        raise DashboardError("approval evidence database may not be a symlink")

    registry = SQLiteCryptoOperatorDecisionRegistry(SQLiteRuntime(database))
    state = registry.record_operator_approval(
        context=context,
        operator_id=operator_id.strip(),
        issued_at=instant,
        expires_at=expires_at,
    )
    verified = registry.get(context.preparation_hash)
    if verified != state or state.status is not CryptoOperatorDecisionStatus.ISSUED:
        raise DashboardError("durable approval registry did not verify exact ISSUED state")
    if state.consumed_at is not None or state.consumed_attempt_id is not None:
        raise DashboardError("UAT approval unexpectedly appears consumed")

    return {
        "status": "CRYPTO_PAPER_ONE_SHOT_APPROVAL_RECORDED_UAT",
        "decision_status": state.status.value,
        "operator_id": state.decision.operator_id,
        "attempt_id": context.attempt_id,
        "preparation_hash": context.preparation_hash,
        "decision_hash": state.decision.decision_hash,
        "event_hash": state.event_hash,
        "event_sequence": state.event_sequence,
        "issued_at": state.decision.issued_at.isoformat(),
        "expires_at": state.decision.expires_at.isoformat(),
        "decision_consumed": False,
        "approval_database": f"{APPROVAL_DB_DIR}/{APPROVAL_DB_NAME}",
        "uat_only": True,
        "reusable_for_real_execution": False,
        "execution_authority": "NONE",
        "broker_write_performed": False,
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "next_action": "BUILD_SEPARATE_RECERTIFIED_EXECUTION_GATE_WITH_NEW_FRESH_APPROVAL",
    }


def _fail_closed(message: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": message,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "operator_approval_authority": "NONE",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


class DashboardServer(_base.DashboardServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], csrf_token: str) -> None:
        self.csrf_token = csrf_token
        self._preview_lock = threading.RLock()
        self._preview_results: dict[str, dict[str, object]] = {}
        self._approval_lock = threading.RLock()
        self._approval_prepares: dict[str, dict[str, object]] = {}
        self._approval_records: dict[str, dict[str, object]] = {}
        _base.ThreadingHTTPServer.__init__(self, address, DashboardHandler)

    def _prune_approval_locked(self) -> None:
        cutoff = time.monotonic() - APPROVAL_RESULT_TTL_SECONDS
        for store in (self._approval_prepares, self._approval_records):
            expired = [key for key, value in store.items() if float(value["stored_at"]) < cutoff]
            for key in expired:
                store.pop(key, None)
            if len(store) > MAX_APPROVAL_RESULTS:
                ordered = sorted(store, key=lambda key: float(store[key]["stored_at"]))
                for key in ordered[: len(store) - MAX_APPROVAL_RESULTS]:
                    store.pop(key, None)

    def begin_approval_prepare(self, request_id: str, *, workspace: str) -> None:
        with self._approval_lock:
            self._prune_approval_locked()
            if request_id in self._approval_prepares:
                raise DashboardError("approval preparation request id already exists; no replay permitted")
            self._approval_prepares[request_id] = {
                "state": "IN_PROGRESS",
                "stored_at": time.monotonic(),
                "workspace": workspace,
            }

    def finish_approval_prepare(self, request_id: str, result: dict[str, object]) -> None:
        with self._approval_lock:
            record = self._approval_prepares.get(request_id)
            if record is None:
                return
            self._approval_prepares[request_id] = {
                "state": "COMPLETE",
                "stored_at": time.monotonic(),
                "workspace": record["workspace"],
                "result": result,
                "record_request_id": None,
            }
            self._prune_approval_locked()

    def approval_prepare_status(self, request_id: str) -> dict[str, object] | None:
        with self._approval_lock:
            self._prune_approval_locked()
            value = self._approval_prepares.get(request_id)
            return dict(value) if value is not None else None

    def begin_approval_record(self, record_id: str, *, approval_id: str) -> dict[str, object]:
        with self._approval_lock:
            self._prune_approval_locked()
            if record_id in self._approval_records:
                raise DashboardError("approval record request id already exists; no replay permitted")
            material = self._approval_prepares.get(approval_id)
            if material is None or material.get("state") != "COMPLETE":
                raise DashboardError("approval preparation is missing, expired or incomplete")
            if material.get("record_request_id") is not None:
                raise DashboardError("this approval preparation has already been claimed for recording")
            material["record_request_id"] = record_id
            material["stored_at"] = time.monotonic()
            self._approval_records[record_id] = {
                "state": "IN_PROGRESS",
                "stored_at": time.monotonic(),
                "approval_request_id": approval_id,
            }
            return dict(material)

    def finish_approval_record(self, record_id: str, result: dict[str, object]) -> None:
        with self._approval_lock:
            record = self._approval_records.get(record_id)
            if record is None:
                return
            self._approval_records[record_id] = {
                "state": "COMPLETE",
                "stored_at": time.monotonic(),
                "approval_request_id": record["approval_request_id"],
                "result": result,
            }
            self._prune_approval_locked()

    def approval_record_status(self, record_id: str) -> dict[str, object] | None:
        with self._approval_lock:
            self._prune_approval_locked()
            value = self._approval_records.get(record_id)
            return dict(value) if value is not None else None


class DashboardHandler(_base.DashboardHandler):
    def _approval_result(self, store: str, request_id: str) -> None:
        record = (
            self.dashboard_server.approval_prepare_status(request_id)
            if store == "prepare"
            else self.dashboard_server.approval_record_status(request_id)
        )
        if record is None:
            value = _fail_closed("approval request id is unknown or expired")
            value.update({"state": "UNKNOWN", "request_id": request_id})
            self._json(HTTPStatus.NOT_FOUND, value)
            return
        if record["state"] == "IN_PROGRESS":
            value = _fail_closed("approval operation still in progress")
            value.update({"ok": True, "state": "IN_PROGRESS", "request_id": request_id})
            self._json(HTTPStatus.ACCEPTED, value)
            return
        self._json(
            HTTPStatus.OK,
            {
                "ok": True,
                "state": "COMPLETE",
                "request_id": request_id,
                "result": record["result"],
                "broker_write_performed": False,
                "external_post_authorized": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
            },
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/crypto":
            self._write_body(HTTPStatus.OK, "text/html; charset=utf-8", _crypto_page(self.dashboard_server.csrf_token))
            return
        if parsed.path == "/api/meta":
            self._json(HTTPStatus.OK, {"ok": True, "meta": _build_meta()})
            return
        if parsed.path in {"/api/canary-approval-prepare-result", "/api/canary-approval-record-result"}:
            request_id = parse_qs(parsed.query, keep_blank_values=True).get("request_id", [""])[0].strip().lower()
            if not APPROVAL_ID_RE.fullmatch(request_id):
                self._json(HTTPStatus.BAD_REQUEST, _fail_closed("invalid approval request id"))
                return
            self._approval_result("prepare" if "prepare" in parsed.path else "record", request_id)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed_path = urlparse(self.path).path
        if parsed_path not in {"/api/canary-approval-prepare", "/api/canary-approval-record"}:
            super().do_POST()
            return
        if not self._require_local_origin():
            self._json(HTTPStatus.FORBIDDEN, _fail_closed("origin rejected"))
            return
        if self.headers.get("X-CSRF-Token") != self.dashboard_server.csrf_token:
            self._json(HTTPStatus.FORBIDDEN, _fail_closed("csrf rejected"))
            return

        if parsed_path == "/api/canary-approval-prepare":
            request_id: str | None = None
            try:
                payload = self._read_payload()
                request_id = _approval_id(payload, "approval_request_id")
                workspace = _base._workspace(payload)
                self.dashboard_server.begin_approval_prepare(request_id, workspace=workspace)
                result = _run_approval_prepare(payload)
                self.dashboard_server.finish_approval_prepare(request_id, result)
            except subprocess.TimeoutExpired:
                value = _fail_closed("approval preparation timed out")
                if request_id is not None:
                    self.dashboard_server.finish_approval_prepare(request_id, value)
                self._json(HTTPStatus.REQUEST_TIMEOUT, value)
                return
            except (DashboardError, OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                value = _fail_closed(str(exc))
                if request_id is not None:
                    self.dashboard_server.finish_approval_prepare(request_id, value)
                self._json(HTTPStatus.BAD_REQUEST, value)
                return
            except Exception as exc:
                diagnostic_id = secrets.token_hex(8)
                print(f"AUTO-TRADE approval prepare diagnostic {diagnostic_id}: {type(exc).__name__}", file=sys.stderr, flush=True)
                value = _fail_closed(f"local approval preparation failed closed [{diagnostic_id}]")
                if request_id is not None:
                    self.dashboard_server.finish_approval_prepare(request_id, value)
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, value)
                return
            self._json(HTTPStatus.OK, result)
            return

        record_id: str | None = None
        try:
            payload = self._read_payload()
            approval_id = _approval_id(payload, "approval_request_id")
            record_id = _approval_id(payload, "record_request_id")
            operator_id = str(payload.get("operator_id") or "").strip()
            confirmation = str(payload.get("confirmation") or "")
            material = self.dashboard_server.approval_prepare_status(approval_id)
            if material is None:
                raise DashboardError("approval preparation is missing or expired")
            _validate_confirmation(material, operator_id=operator_id, confirmation=confirmation)
            material = self.dashboard_server.begin_approval_record(record_id, approval_id=approval_id)
            receipt = _record_operator_approval(
                material,
                operator_id=operator_id,
                confirmation=confirmation,
                now=datetime.now(timezone.utc),
            )
            result = {
                "ok": True,
                "receipt": receipt,
                "broker_write_performed": False,
                "external_post_authorized": False,
                "operator_approval_authority": "UAT_APPROVAL_RECORDED_NOT_EXECUTABLE",
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
            }
            self.dashboard_server.finish_approval_record(record_id, result)
        except (DashboardError, OSError, ValueError) as exc:
            value = _fail_closed(str(exc))
            if record_id is not None:
                self.dashboard_server.finish_approval_record(record_id, value)
            self._json(HTTPStatus.BAD_REQUEST, value)
            return
        except Exception as exc:
            diagnostic_id = secrets.token_hex(8)
            print(f"AUTO-TRADE approval record diagnostic {diagnostic_id}: {type(exc).__name__}", file=sys.stderr, flush=True)
            value = _fail_closed(f"local approval recording failed closed [{diagnostic_id}]")
            if record_id is not None:
                self.dashboard_server.finish_approval_record(record_id, value)
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, value)
            return
        self._json(HTTPStatus.OK, result)


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
    print("AUTO-TRADE R6 — NATIVE MULTI-ASSET CONTROL CENTER + ONE-SHOT APPROVAL UAT")
    print(f"Hub: {url}")
    print(f"Equities: {url}equities")
    print(f"Crypto: {url}crypto")
    print("External PAPER write: DISABLED")
    print("Human approval consumption: UNAVAILABLE")
    print("LIVE trading: BLOCKED")
    print("Order execution from dashboard: UNAVAILABLE")
    print("Keep this terminal open while using the dashboard. Ctrl+C closes it.")
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
