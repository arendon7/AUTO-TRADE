from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT_JSON = ROOT / "knowledge/00_CANON/debt_register.json"
DEBT_MD = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
STATE = ROOT / "knowledge/00_CANON/ESTADO_ACTUAL.md"
TASK = ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md"
CONTEXT = ROOT / "knowledge/00_CANON/CONTEXTO_RAPIDO.md"
HANDOFF = ROOT / "knowledge/40_HANDOFF/HANDOFF_ACTUAL.md"
CERT = ROOT / "knowledge/60_EVIDENCE/R5_CERTIFICATION.json"

BASE_MAIN = "c294aa69f35b64559e3aea58a1c0661e66599db8"
BASIS_HEAD = "0d4f75d083a055b83646bb861f08731aecace560"
CORE_RUN = 31465755866
KNOWLEDGE_RUN = 31465755855
LIVE_RUN = 31465471204
LIVE_SOURCE_HEAD = "ba0ec1198c60234fa0f9b3f8184ad591aaa87c61"
CONTRACT_HASH = "ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785"
R5_IDS = [f"TD-R5-{index:03d}" for index in range(1, 7)]

RESOLUTIONS = {
    "TD-R5-001": (
        "Implemented a disabled-by-default closed-kline market-data-only WebSocket boundary "
        "with exact WSS host/path validation before I/O, pinned audited client dependency, "
        "proxy/compression disabled, bounded timeouts/message size/queue, receive-only adapter, "
        "strict closed-kline parsing and bounded live BTCUSDT 1s evidence from "
        "data-stream.binance.vision. No user-data, broker order or capital authority is exposed."
    ),
    "TD-R5-002": (
        "Implemented deterministic closed-kline identity and continuity: identical duplicates are "
        "idempotent no-ops; conflicting duplicates, out-of-order bars, timestamp/sequence gaps, "
        "malformed/open/future/stale evidence fail closed; no silent imputation or cursor advance occurs."
    ),
    "TD-R5-003": (
        "Implemented sticky DEGRADED lifecycle for timeout, EOF, socket/protocol failure and continuity "
        "integrity failure. DEGRADED state tears down the session and refuses reconnect/advance until "
        "an explicit future recovery design exists, preventing reconnect from hiding an unresolved gap."
    ),
    "TD-R5-004": (
        "Implemented research-only SQLite portfolio shadow evidence with exact Decimal frozen weights, "
        "exact synchronized timestamps, canonical component observations, recomputable weighted return/NAV, "
        "append-only SHA-256 record chain and separately hash-protected head/control anchor. Replay is "
        "idempotent and config/timestamp/hash/tail-deletion tampering fails closed."
    ),
    "TD-R5-005": (
        "Implemented append-only FORWARD_POST_ACTIVATION evidence sourced only from a fully verified shadow "
        "record hash and frozen policy/config fingerprints. Evidence predating activation, gaps, policy/config "
        "mismatch or conflicting replay fail closed. The API imports no split/validation/FINAL_HOLDOUT "
        "authority and cannot recalibrate frozen thresholds, weights or selection decisions."
    ),
    "TD-R5-006": (
        "Added a permanent Core Safety R5 authority scanner covering stream transport, streaming, shadow and "
        "forward modules. It forbids OMS/engine/broker/Safety/operational Portfolio State imports, order/send "
        "calls, Alpaca/PAPER/LIVE endpoints, FINAL_HOLDOUT/selection imports, and networking outside the "
        "approved stream surface. Adversarial checker tests prove the boundary fails closed."
    ),
}

EVIDENCE = {
    "TD-R5-001": [
        "src/autotrade/research/streaming.py",
        "src/autotrade/research/stream_transport.py",
        "tests/test_r5_closed_kline_stream.py",
        "tests/test_r5_stream_state_authority.py",
        "tests/test_r5_websocket_transport.py",
        "tests/test_r5_adversarial_edges_v2.py",
        "knowledge/60_EVIDENCE/R5_LIVE_CLOSED_KLINE_STREAM_EVIDENCE.json",
        "knowledge/60_EVIDENCE/R5_CERTIFICATION.json",
    ],
    "TD-R5-002": [
        "src/autotrade/research/streaming.py",
        "tests/test_r5_closed_kline_stream.py",
        "tests/test_r5_adversarial_edges_v2.py",
        "knowledge/60_EVIDENCE/R5_CERTIFICATION.json",
    ],
    "TD-R5-003": [
        "src/autotrade/research/streaming.py",
        "src/autotrade/research/stream_transport.py",
        "tests/test_r5_closed_kline_stream.py",
        "tests/test_r5_websocket_transport.py",
        "knowledge/60_EVIDENCE/R5_CERTIFICATION.json",
    ],
    "TD-R5-004": [
        "src/autotrade/research/shadow.py",
        "tests/test_r5_portfolio_shadow.py",
        "tests/test_r5_adversarial_edges_v2.py",
        "knowledge/60_EVIDENCE/R5_CERTIFICATION.json",
    ],
    "TD-R5-005": [
        "src/autotrade/research/forward.py",
        "tests/test_r5_forward_evidence.py",
        "tests/test_r5_adversarial_edges_v2.py",
        "knowledge/60_EVIDENCE/R5_CERTIFICATION.json",
    ],
    "TD-R5-006": [
        "scripts/check_r5_authority.py",
        ".github/workflows/core-tests.yml",
        "tests/test_r5_authority_checker.py",
        "knowledge/60_EVIDENCE/R5_CERTIFICATION.json",
    ],
}


def close_machine_debt() -> None:
    data = json.loads(DEBT_JSON.read_text())
    if data.get("certified_tracks") != ["R0", "R1", "R2", "R3", "R4"]:
        raise SystemExit(f"unexpected certified tracks: {data.get('certified_tracks')}")
    by_id = {item["id"]: item for item in data["items"]}
    missing = [debt_id for debt_id in R5_IDS if debt_id not in by_id]
    if missing:
        raise SystemExit(f"missing R5 debt: {missing}")
    extra = [item["id"] for item in data["items"] if item.get("track") == "R5" and item["id"] not in R5_IDS]
    if extra:
        raise SystemExit(f"unexpected R5 debt IDs: {extra}")
    for debt_id in R5_IDS:
        item = by_id[debt_id]
        if item.get("severity") not in {"P0", "P1", "P2"}:
            raise SystemExit(f"unexpected severity for {debt_id}: {item.get('severity')}")
        if item.get("status") != "OPEN":
            raise SystemExit(f"{debt_id} must be OPEN before closure")
        item["status"] = "CLOSED"
        item["resolution"] = RESOLUTIONS[debt_id]
        item["evidence"] = EVIDENCE[debt_id]
        item["next_action"] = ""
    data["certified_tracks"].append("R5")
    DEBT_JSON.write_text(json.dumps(data, indent=2) + "\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"matrix marker missing: {label}")
    return text.replace(old, new, 1)


def update_matrix() -> None:
    text = MATRIX.read_text()
    replacements = [
        (
            "| R5 | closed-kline read-only stream | v0.22 | TODO | disabled default + fixed host + stream state |",
            "| R5 | closed-kline read-only stream | v0.22 | PASS | disabled default + exact market-data-only WSS host/path + bounded receive-only transport + strict closed-kline validation + live 1s evidence (`TD-R5-001`) |",
            "R5 stream",
        ),
        (
            "| R5 | duplicate idempotency + gap fail-closed | v0.22 | TODO | no silent imputation |",
            "| R5 | duplicate idempotency + gap fail-closed | v0.22 | PASS | identical replay no-op; conflict/out-of-order/gap fail closed; no silent imputation or cursor advance (`TD-R5-002`) |",
            "R5 continuity",
        ),
        (
            "| R5 | socket termination -> DEGRADED | v0.22 | TODO | no reconnect that hides gaps |",
            "| R5 | socket termination -> DEGRADED | v0.22 | PASS | timeout/EOF/socket/integrity failure => sticky DEGRADED + session teardown; reconnect cannot hide unresolved continuity (`TD-R5-003`) |",
            "R5 degraded",
        ),
        (
            "| R5 | synchronized portfolio shadow | v0.25 | TODO | frozen weights + exact timestamps |",
            "| R5 | synchronized portfolio shadow | v0.25 | PASS | exact Decimal frozen weights + synchronized timestamps + recomputable components/NAV + append-only hash-chain + anchored head (`TD-R5-004`) |",
            "R5 shadow",
        ),
        (
            "| R5 | forward evidence without HOLDOUT | v0.25 | TODO | post-activation evidence separation |",
            "| R5 | forward evidence without HOLDOUT | v0.25 | PASS | verified-shadow-sourced append-only post-activation evidence + frozen policy/config + structural FINAL_HOLDOUT/selection separation (`TD-R5-005`,`TD-R5-006`) |",
            "R5 forward",
        ),
    ]
    for old, new, label in replacements:
        text = replace_once(text, old, new, label)

    marker = "## Active target — R5\n"
    if marker not in text:
        raise SystemExit("R5 active target marker missing")
    before, active = text.split(marker, 1)
    debt_policy_marker = "\n## Debt policy\n"
    if debt_policy_marker not in active:
        raise SystemExit("debt policy marker missing")
    _, after = active.split(debt_policy_marker, 1)
    r5_ledger = f"""\n### R5
Branch certification basis: `{BASIS_HEAD}` from exact post-R4-green `main` `{BASE_MAIN}`.
Certified closure evidence: **606 tests PASS / 86.49% branch coverage**, Contract Registry 10 PASS, Research/Advisory Authority PASS, R5 Authority Boundary PASS, Debt Register PASS and Knowledge Contract PASS.
Bounded live market-data-only WebSocket evidence: run `{LIVE_RUN}`, source `{LIVE_SOURCE_HEAD}`, `BTCUSDT` `1s`, exact `data-stream.binance.vision` endpoint, no application send surface.
Certification artifact: `knowledge/60_EVIDENCE/R5_CERTIFICATION.json`.

R5 adds no external PAPER/LIVE order authority and does not reuse FINAL_HOLDOUT for forward recalibration.

## Next target — R6
R6 is gated until PR #13 is merged and the exact resulting `main` SHA is recertified green. R6 will cover external Alpaca PAPER only, bounded canary qualification and broker-side protection evidence; LIVE remains blocked.
"""
    text = before + marker.replace("Active target — R5", "Certification closure — R5") + r5_ledger + debt_policy_marker + after
    MATRIX.write_text(text)


def write_certification() -> None:
    payload = {
        "schema_version": 1,
        "track": "R5",
        "reconstruction_target": "v0.28R",
        "status": "CERTIFIED_BRANCH_PENDING_PR_INTEGRATION",
        "base_main": BASE_MAIN,
        "certification_basis_head": BASIS_HEAD,
        "ci": {
            "core_safety_run_id": CORE_RUN,
            "knowledge_contract_run_id": KNOWLEDGE_RUN,
            "tests_passed": 606,
            "coverage_percent": 86.49,
            "coverage_minimum_percent": 85.0,
            "contract_registry": "PASS",
            "contract_count": 10,
            "contract_registry_sha256": CONTRACT_HASH,
            "research_advisory_authority_boundary": "PASS",
            "r5_stream_shadow_forward_authority_boundary": "PASS",
            "debt_register_contract": "PASS",
            "knowledge_contract": "PASS",
        },
        "live_stream_evidence": {
            "artifact": "knowledge/60_EVIDENCE/R5_LIVE_CLOSED_KLINE_STREAM_EVIDENCE.json",
            "run_id": LIVE_RUN,
            "source_head": LIVE_SOURCE_HEAD,
            "venue": "BINANCE_SPOT",
            "symbol": "BTCUSDT",
            "interval": "1s",
            "market_data_only": True,
            "application_send_surface": "NONE",
            "status": "PASS",
        },
        "closed_r5_debt_ids": R5_IDS,
        "open_r5_blocking_debt_ids": [],
        "open_nonblocking_ops_debt_ids": ["TD-OPS-001"],
        "capabilities": {
            "closed_kline_read_only_stream": {
                "status": "PASS",
                "evidence": EVIDENCE["TD-R5-001"],
            },
            "duplicate_gap_order_integrity": {
                "status": "PASS",
                "evidence": EVIDENCE["TD-R5-002"],
            },
            "degraded_socket_lifecycle": {
                "status": "PASS",
                "evidence": EVIDENCE["TD-R5-003"],
            },
            "synchronized_portfolio_shadow": {
                "status": "PASS",
                "evidence": EVIDENCE["TD-R5-004"],
            },
            "post_activation_forward_evidence": {
                "status": "PASS",
                "evidence": EVIDENCE["TD-R5-005"],
            },
            "r5_execution_authority_boundary": {
                "status": "PASS",
                "evidence": EVIDENCE["TD-R5-006"],
            },
        },
        "cross_boundary_invariants": [
            "Streaming is disabled by default and exact market-data-only endpoint policy is validated before I/O.",
            "Only authoritative closed klines may advance the stream cursor; malformed/open/future/stale/conflicting/gapped evidence cannot advance state.",
            "Unexpected transport or continuity failure becomes sticky DEGRADED and reconnect cannot hide unresolved gaps.",
            "Portfolio shadow weights/config/timestamps are frozen, exact-Decimal and fully recomputable from canonical persisted components.",
            "Shadow and forward evidence use append-only hash chains with separately anchored durable heads so tail deletion is detectable.",
            "Forward evidence is post-activation only, sourced from verified shadow hashes and structurally separated from FINAL_HOLDOUT/selection authority.",
            "Permanent CI rejects OMS/broker/Safety/operational Portfolio State imports, order/send calls, Alpaca/LIVE endpoints and unauthorized networking in R5 modules.",
            "No R5 path grants external PAPER or LIVE execution authority or may increase capital risk from stale/missing/gapped evidence.",
        ],
        "explicit_non_claims": [
            "R5 certification is not profitability evidence.",
            "R5 certification is not external PAPER qualification.",
            "R5 certification is not LIVE trading approval.",
            "The bounded live WebSocket observation validates market-data transport behavior only, not strategy edge or expected returns.",
        ],
        "capital_authority": "NONE",
        "external_paper_authority": "NONE_ADDED_BY_R5",
        "live_trading": "BLOCKED",
        "next_track": "R6_AFTER_R5_MERGE_AND_POST_MERGE_MAIN_RECERTIFICATION",
    }
    CERT.write_text(json.dumps(payload, indent=2) + "\n")


def write_debt_md() -> None:
    DEBT_MD.write_text(f"""# DEBT REGISTER — v0.28R

Fecha: 2026-08-11
Estado: **R0–R5 CERTIFIED; R5 PR #13 pending integration; R6 gated**

## Authority
La autoridad machine-readable es `knowledge/00_CANON/debt_register.json`. Si esta vista humana discrepa, manda el JSON + CI.

## Regla
Ningún track puede certificarse con deuda conocida P0/P1/P2 asignada a ese track. Deuda nueva se registra antes de implementar y no se rebaja para satisfacer una release.

## Certified tracks
- **R0 — PASS**
- **R1 — PASS**
- **R2 — PASS**
- **R3 — PASS**
- **R4 — PASS e integrado/post-merge recertificado en `main` `{BASE_MAIN}`**
- **R5 — PASS en branch; PR #13 pendiente de integración y recertificación post-merge**

Certificación R5: `knowledge/60_EVIDENCE/R5_CERTIFICATION.json`, basis `{BASIS_HEAD}`: **606 tests PASS / 86.49% coverage**, Contract Registry 10 PASS, Research Authority PASS, R5 Authority Boundary PASS, Debt Register PASS y Knowledge Contract PASS.

## R5 debt closure
Todos los P0/P1/P2 conocidos de R5 están CLOSED: `TD-R5-001..006`.
Incluye stream WSS market-data-only acotado, continuidad fail-closed, DEGRADED sticky, shadow sincronizado/hash-bound, forward evidence post-activation y CI authority boundary permanente.

## Deuda abierta
| ID | Sev | Track | Área | Condición de cierre |
|---|---|---|---|---|
| `TD-OPS-001` | P3 | OPS | Graphify | generar graph semántico/deep real en runtime soportado y vincularlo a `SOURCE_SHA`; nunca fabricar artefactos |

No existe P0/P1/P2 OPEN de R5. `TD-OPS-001` no bloquea R5.

## Próximo orden
1. mantener PR #13 verde y sin nuevas features;
2. merge sólo contra el expected head certificado;
3. recertificar el SHA exacto de `main` post-merge;
4. crear R6 únicamente desde ese `main` verde;
5. registrar deuda R6 antes de implementar external PAPER.

## Capital
**LIVE TRADING: BLOQUEADO.**
R5 no concede external PAPER/LIVE authority ni demuestra rentabilidad.
""")


def write_state() -> None:
    STATE.write_text(f"""# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado canónico: **v0.28R reconstruction — R0–R5 CERTIFIED; R5 PR #13 integration pending**

## Base integrada
R4 está integrado y post-merge recertificado en `main` `{BASE_MAIN}`.

## R5 certificado en branch
Branch: `reconstruction/r5-stream-shadow-forward`.
Certification basis: `{BASIS_HEAD}`.
- Core Safety `{CORE_RUN}`: PASS — **606 tests / 86.49% coverage**.
- Knowledge Contract `{KNOWLEDGE_RUN}`: PASS.
- Contract Registry: 10 PASS — `{CONTRACT_HASH}`.
- Research/Advisory Authority Boundary: PASS.
- R5 Stream/Shadow/Forward Authority Boundary: PASS.
- R5 P0/P1/P2 OPEN: **0**.

Capacidades:
- closed-kline market-data-only WSS stream, disabled by default and bounded;
- identical duplicate idempotency + conflicting duplicate/out-of-order/gap fail-closed;
- timeout/EOF/socket/integrity failure => sticky DEGRADED, no reconnect hiding gaps;
- synchronized research-only portfolio shadow with exact frozen weights/timestamps, recomputation and anchored hash chain;
- append-only post-activation forward evidence sourced from verified shadow and separated from FINAL_HOLDOUT;
- permanent CI execution-authority boundary.

Live transport evidence: `R5_LIVE_CLOSED_KLINE_STREAM_EVIDENCE.json`, run `{LIVE_RUN}`, BTCUSDT 1s from market-data-only endpoint.

## Integración pendiente
PR #13 must remain feature-frozen. After merge, recertify the exact resulting `main` SHA before creating R6.

## Deuda
`TD-OPS-001` Graphify P3/OPS remains OPEN and non-blocking.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER authority added by R5: NONE.
R5 certification is infrastructure/evidence integrity, not profitability proof.
""")


def write_task() -> None:
    TASK.write_text(f"""# TAREA ACTIVA — AUTO-TRADE

## Objetivo inmediato
**Integrar R5 certificado en `main`, recertificar el SHA exacto resultante y sólo entonces abrir R6.**

R5 ya tiene branch certification sobre `{BASIS_HEAD}`. No añadir nuevas features R5 ni iniciar external PAPER desde la rama pre-merge.

## Secuencia obligatoria
1. exigir Core Safety + Knowledge Contract verdes en el head final de PR #13;
2. sacar PR #13 de DRAFT sólo con head exacto certificado;
3. merge por squash usando `expected_head_sha`;
4. verificar Core Safety + Knowledge Contract sobre el SHA exacto resultante en `main`;
5. sólo si `main` queda verde, crear rama R6 desde ese SHA;
6. registrar explícitamente deuda R6 antes de programar cualquier gateway PAPER.

## R6 — alcance siguiente, todavía no iniciado
- external Alpaca PAPER gateway, disabled by default;
- exact PAPER host allowlist; LIVE host forbidden;
- bounded external PAPER canary con prerequisites y notional cap más estricto;
- qualification evidence de terminality/fills/slippage/reconciliation;
- broker-side equity bracket protection con parent + exactamente 2 legs validadas;
- PAPER `trade_updates` protection evidence cuando la policy lo requiera;
- unsupported products fail closed; crypto bracket no soportado salvo certificación separada.

## Negative tests obligatorios para R6
- gateway disabled by default => cero red y cero order submission;
- LIVE host, arbitrary host, credentials/proxy no autorizado o path no permitido => reject antes de I/O;
- falta de preregistration, Instrument Master, Health/Safety approval, reconciliation o PAPER qualification => canary bloqueado;
- canary notional exactamente en frontera permitido y un quantum por encima rechazado; nunca auto-upsize a venue minimum;
- stale/missing/conflicting market/portfolio/broker state => fail closed, no nueva exposición;
- ambiguous submit/timeout => UNKNOWN + reconciliation; nunca retry ciego que duplique orden;
- partial fill/cancel/replace/restart preservan idempotencia y reservas;
- bracket equity debe tener parent + exactamente stop-loss y take-profit coherentes; leg faltante/extra/crossed/invalid => reject;
- asset/producto sin bracket certificado => fail closed; crypto bracket permanece unsupported;
- `trade_updates` faltante/stale/conflictivo cuando policy lo exige => protección no certificada;
- ninguna ruta R6 acepta host LIVE ni puede promover LIVE authority;
- AI/research output jamás es autorización de orden; Safety + OMS siguen siendo gates deterministas.

## Restricciones
- Coverage gate >=85% intacto.
- No relajar negative tests para cerrar R6.
- `TD-OPS-001` permanece visible; no fabricar Graphify.
- No declarar rentabilidad por PAPER qualification.

## Capital
**LIVE TRADING: BLOQUEADO.**
R6, si se certifica, será PAPER-only; cualquier futuro LIVE requerirá promoción separada y explícita fuera de v0.28R.
""")


def write_context() -> None:
    CONTEXT.write_text(f"""# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **v0.28R R0–R5 certified; R5 PR #13 pending integration**.

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md`
5. `knowledge/00_CANON/debt_register.json`
6. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`
7. `knowledge/60_EVIDENCE/R5_CERTIFICATION.json`
8. `knowledge/60_EVIDENCE/R5_LIVE_CLOSED_KLINE_STREAM_EVIDENCE.json`

## R5 certification
Basis `{BASIS_HEAD}` from post-R4-green `main` `{BASE_MAIN}`: **606 tests PASS / 86.49% coverage**; 10 contracts; Research Authority, R5 Authority, Debt Register and Knowledge Contract PASS. R5 blocking debt open: 0.

## Regla operativa inmediata
No empezar R6 desde la rama R5. Primero PR #13 -> merge -> CI verde sobre SHA exacto de `main`; después crear R6 y registrar su deuda.

## Próximo track
R6 = external Alpaca PAPER gateway + bounded canary + PAPER evidence qualification + broker-side protection. PAPER only; LIVE remains forbidden.

## Authority
AI/research/Portfolio Manager/stream/shadow/forward no tienen autoridad de ejecución. Safety + OMS continúan siendo fronteras deterministas obligatorias.

**LIVE TRADING: BLOQUEADO.**
""")


def write_handoff() -> None:
    HANDOFF.write_text(f"""# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado: **R5 branch certified; PR #13 integration pending**

## Base integrada conocida
R4 está integrado y post-merge certificado en `main` `{BASE_MAIN}`.

## R5
Branch: `reconstruction/r5-stream-shadow-forward`.
PR: #13.
Certification basis: `{BASIS_HEAD}`.
Evidence: `knowledge/60_EVIDENCE/R5_CERTIFICATION.json`.
Result: **606 tests PASS / 86.49% coverage**, 10 contracts, Research Authority PASS, R5 Authority Boundary PASS, Debt Register PASS, Knowledge Contract PASS.

Todos los P0/P1/P2 conocidos de R5 (`TD-R5-001..006`) están CLOSED. Todas las filas requeridas R5 de la capability matrix están PASS.

## Invariantes de cierre
- market-data stream disabled by default; exact host/path validated before I/O;
- adapter receive-only, bounded, proxy/compression disabled; no application `.send()` surface;
- only closed klines advance cursor; duplicate conflict/out-of-order/gap fail closed;
- timeout/EOF/socket/integrity failure => sticky DEGRADED; reconnect cannot hide gaps;
- shadow uses exact frozen weights/timestamps and fully recomputable canonical components;
- shadow + forward chains have anchored heads detecting tail deletion;
- forward evidence is post-activation only and structurally separated from FINAL_HOLDOUT;
- permanent R5 CI rejects execution-authority creep;
- no external PAPER/LIVE authority added by R5.

## Próxima acción exacta
1. final CI green on clean canonical R5 head;
2. mark PR #13 ready and update exact evidence;
3. squash merge using expected head SHA;
4. recertify exact resulting `main` SHA;
5. create R6 only from that green SHA and register R6 debt before coding.

## Deuda no bloqueante
`TD-OPS-001` Graphify P3/OPS remains OPEN; no fake semantic/deep artifact generation.

## Capital
**LIVE TRADING: BLOQUEADO.**
R5 added no external PAPER/LIVE authority.
""")


def main() -> None:
    close_machine_debt()
    update_matrix()
    write_certification()
    write_debt_md()
    write_state()
    write_task()
    write_context()
    write_handoff()


if __name__ == "__main__":
    main()
