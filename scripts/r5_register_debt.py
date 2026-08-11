from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT_JSON = ROOT / "knowledge/00_CANON/debt_register.json"
DEBT_MD = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
STATE = ROOT / "knowledge/00_CANON/ESTADO_ACTUAL.md"
TASK = ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md"
CONTEXT = ROOT / "knowledge/00_CANON/CONTEXTO_RAPIDO.md"
HANDOFF = ROOT / "knowledge/40_HANDOFF/HANDOFF_ACTUAL.md"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
POST_MERGE = ROOT / "knowledge/60_EVIDENCE/R4_POST_MERGE_INTEGRATION_AUDIT.json"

GREEN_MAIN = "c294aa69f35b64559e3aea58a1c0661e66599db8"
CORE_RUN = 31463746764
KNOWLEDGE_RUN = 31463746745

R5_DEBTS = [
    {
        "area": "Closed-kline read-only streaming boundary",
        "evidence": [],
        "id": "TD-R5-001",
        "next_action": "Implement a disabled-by-default read-only closed-kline stream with exact allowlisted HTTPS/WSS host/path/protocol, bounded I/O and no broker/order authority; certify malformed/open/future/stale input rejection before closing.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R5",
    },
    {
        "area": "Streaming duplicate, ordering and gap integrity",
        "evidence": [],
        "id": "TD-R5-002",
        "next_action": "Implement deterministic sequence/timestamp continuity with identical-duplicate idempotent no-op, conflicting duplicate/out-of-order/gap fail-closed semantics and no silent imputation; certify replay behavior.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R5",
    },
    {
        "area": "Streaming DEGRADED lifecycle and reconnect safety",
        "evidence": [],
        "id": "TD-R5-003",
        "next_action": "Implement durable/read-only stream health where EOF, timeout, protocol ambiguity or unexpected termination enters DEGRADED and reconnect cannot clear or hide an unresolved continuity gap.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R5",
    },
    {
        "area": "Synchronized portfolio shadow integrity",
        "evidence": [],
        "id": "TD-R5-004",
        "next_action": "Implement reproducible hash-bound portfolio shadow state using frozen strategy weights/configuration and exact market timestamps; reject stale, mismatched or non-reproducible shadow evidence and prevent double counting.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R5",
    },
    {
        "area": "Forward evidence separation from FINAL_HOLDOUT",
        "evidence": [],
        "id": "TD-R5-005",
        "next_action": "Implement append-only post-activation forward evidence that is structurally separated from FINAL_HOLDOUT and cannot recalibrate frozen thresholds, weights or selection decisions; certify idempotency and provenance.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R5",
    },
    {
        "area": "R5 streaming/shadow execution-authority boundary",
        "evidence": [],
        "id": "TD-R5-006",
        "next_action": "Extend permanent CI authority checks so R5 stream/shadow/forward modules cannot import or invoke OMS submission, broker order methods, LIVE endpoints or capital-promotion paths; stale/missing/gapped evidence must never increase risk.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R5",
    },
]


def register_debt() -> None:
    data = json.loads(DEBT_JSON.read_text())
    if data.get("certified_tracks") != ["R0", "R1", "R2", "R3", "R4"]:
        raise SystemExit(f"unexpected certified tracks: {data.get('certified_tracks')}")
    existing = {item["id"] for item in data["items"]}
    collisions = sorted(existing & {item["id"] for item in R5_DEBTS})
    if collisions:
        raise SystemExit(f"R5 debt IDs already exist: {collisions}")
    data["items"].extend(R5_DEBTS)
    DEBT_JSON.write_text(json.dumps(data, indent=2) + "\n")


def update_matrix() -> None:
    text = MATRIX.read_text()
    marker = "## Active target — R5\n"
    if marker not in text:
        raise SystemExit("R5 active-target marker missing")
    insert = (
        "## Active target — R5\n"
        f"R5 starts only from post-merge-green `main` `{GREEN_MAIN}`. "
        "Registered blocking debt before implementation: `TD-R5-001..006`.\n\n"
    )
    MATRIX.write_text(text.replace(marker, insert, 1))


def write_post_merge_audit() -> None:
    payload = {
        "schema_version": 1,
        "track": "R4",
        "audit": "POST_MERGE_INTEGRATION",
        "initial_merge_sha": "aa6d80dc1682967edef367f726a620e41c0af118",
        "status": "REPAIRED_AND_RECERTIFIED",
        "initial_runs": {
            "knowledge_contract": {"run_id": 31461659067, "conclusion": "success"},
            "core_safety": {
                "run_id": 31461659063,
                "conclusion": "failure",
                "observed_tests": {"passed": 481, "failed": 2},
                "coverage_percent": 86.45,
            },
        },
        "integration_defects": [
            {
                "id": "R4-PM-001",
                "type": "CERTIFICATION_CONTRACT_MISMATCH",
                "file": "tests/test_r4_certification_contract.py",
                "remediation": "permanent contract corrected from 480 to verified 479 tests and exact 86.45% coverage",
            },
            {
                "id": "R4-PM-002",
                "type": "TEMPORARY_WORKFLOW_LEAK",
                "file": ".github/workflows/r4-final-readiness-one-shot.yml",
                "remediation": "temporary workflow removed from repository tree",
            },
        ],
        "repair": {
            "pull_request": 12,
            "hotfix_head": "2a8977110b32039d5fdc9d1cbd37278f36758fee",
            "final_main_sha": GREEN_MAIN,
            "core_safety_run_id": CORE_RUN,
            "knowledge_contract_run_id": KNOWLEDGE_RUN,
            "tests_passed": 483,
            "coverage_percent": 86.45,
            "contract_registry": "PASS",
            "contract_count": 10,
            "research_advisory_authority_boundary": "PASS",
            "debt_register_contract": "PASS",
            "knowledge_contract": "PASS",
        },
        "functional_regression_detected": False,
        "r4_blocking_debt_reopened": False,
        "r5_gate": "OPEN_FROM_CERTIFIED_MAIN_SHA",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    POST_MERGE.write_text(json.dumps(payload, indent=2) + "\n")


def write_debt_md() -> None:
    DEBT_MD.write_text(f"""# DEBT REGISTER — v0.28R

Fecha: 2026-08-11
Estado: ACTIVE — **R0–R4 CERTIFIED; R5 ACTIVE**

## Authority
La autoridad machine-readable es `knowledge/00_CANON/debt_register.json`. Si esta vista humana discrepa, manda el JSON + CI.

## Regla
Ningún track puede certificarse con deuda conocida P0/P1/P2 asignada a ese track. Deuda nueva se registra antes de implementar y no se rebaja para satisfacer una release.

## Certified tracks
- **R0 — PASS**
- **R1 — PASS**
- **R2 — PASS**
- **R3 — PASS**
- **R4 — PASS e integrado; post-merge recertificado en `main` `{GREEN_MAIN}`**

Post-merge R4: Core Safety `{CORE_RUN}` PASS, Knowledge Contract `{KNOWLEDGE_RUN}` PASS, **483 tests / 86.45% coverage**.

## R5 — deuda registrada antes de implementación
| ID | Sev | Área | Condición de cierre |
|---|---|---|---|
| `TD-R5-001` | P1 | Closed-kline read-only streaming boundary | disabled-by-default, allowlist exacta, closed-only, bounded I/O, sin broker/order authority |
| `TD-R5-002` | P1 | Duplicate/order/gap integrity | duplicate idéntico idempotente; conflicto/out-of-order/gap fail closed; sin imputación |
| `TD-R5-003` | P1 | DEGRADED lifecycle | EOF/timeout/ambigüedad => DEGRADED; reconnect no oculta gaps |
| `TD-R5-004` | P1 | Synchronized portfolio shadow | pesos/config/timestamps congelados, hash-bound, reproducible e idempotente |
| `TD-R5-005` | P1 | Forward evidence vs HOLDOUT | evidencia post-activation separada; FINAL_HOLDOUT no recalibra decisiones |
| `TD-R5-006` | P1 | Execution-authority boundary | stream/shadow/forward sin OMS submit, broker orders, LIVE endpoints ni risk increase por evidencia degradada |

R5 P0/P1/P2 OPEN: **6**. Por definición R5 NO puede certificarse hasta cerrarlas con evidencia.

## Deuda no bloqueante fuera de R5
| ID | Sev | Track | Área |
|---|---|---|---|
| `TD-OPS-001` | P3 | OPS | Graphify semántico/deep real pendiente de runtime soportado |

## Capital
**LIVE TRADING: BLOQUEADO.**
R5 no puede conceder external PAPER/LIVE authority.
""")


def write_state() -> None:
    STATE.write_text(f"""# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado canónico: **v0.28R reconstruction — R0–R4 CERTIFIED; R5 ACTIVE**

## Base certificada
R4 quedó integrado y post-merge recertificado en el SHA exacto de `main` `{GREEN_MAIN}`.
- Core Safety `{CORE_RUN}`: PASS — **483 tests / 86.45% coverage**.
- Knowledge Contract `{KNOWLEDGE_RUN}`: PASS.
- Contract Registry: 10 PASS.
- Research/Advisory Authority Boundary: PASS.
- Debt Register Contract: PASS.

El primer merge R4 `aa6d80dc...` falló recertificación por dos defectos de integración y fue reparado por PR #12. Evidencia: `knowledge/60_EVIDENCE/R4_POST_MERGE_INTEGRATION_AUDIT.json`.

## R5 activo
Branch: `reconstruction/r5-stream-shadow-forward`.
Base exacta: `{GREEN_MAIN}`.
Antes de implementar se registraron `TD-R5-001..006` como P1 OPEN.

Alcance:
- closed-kline read-only streaming;
- duplicate/idempotency + gap/out-of-order fail-closed;
- DEGRADED socket lifecycle sin reconnect que esconda gaps;
- synchronized portfolio shadow con frozen weights/config/timestamps;
- forward evidence post-activation separado de FINAL_HOLDOUT;
- CI permanente de no execution-authority creep.

## Deuda
- R5 P1 OPEN: **6** (`TD-R5-001..006`).
- `TD-OPS-001` Graphify P3/OPS: OPEN, no bloqueante.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER/LIVE authority en R5: NONE.
""")


def write_task() -> None:
    TASK.write_text(f"""# TAREA ACTIVA — AUTO-TRADE

## Objetivo inmediato
**R5 — closed-kline read-only streaming + synchronized shadow + forward evidence.**

Base obligatoria: post-merge-green `main` `{GREEN_MAIN}`.
Branch activa: `reconstruction/r5-stream-shadow-forward`.

## Deuda registrada antes de programar
- `TD-R5-001` — closed-kline read-only streaming boundary.
- `TD-R5-002` — duplicate/order/gap integrity.
- `TD-R5-003` — DEGRADED lifecycle + reconnect safety.
- `TD-R5-004` — synchronized portfolio shadow integrity.
- `TD-R5-005` — forward evidence separation from FINAL_HOLDOUT.
- `TD-R5-006` — permanent execution-authority boundary.

## Orden de implementación
1. modelo de estado + contrato del stream closed-kline read-only;
2. duplicate idempotency, order/sequence/gap validation;
3. socket DEGRADED lifecycle y reconnect continuity gate;
4. synchronized portfolio shadow hash-bound;
5. forward evidence append-only separado de HOLDOUT;
6. authority scan + adversarial certification + debt closure.

## Negative tests obligatorios para R5
- stream deshabilitado por defecto no abre conexiones ni hace I/O;
- host/path/protocolo no permitido => reject antes de I/O;
- vela abierta, malformed, stale, futura o fuera de orden => fail closed;
- duplicado idéntico => idempotent no-op; duplicado conflictivo => fail closed;
- gap temporal/sequence gap => DEGRADED, sin imputación ni avance optimista;
- socket EOF/error/timeout/ambigüedad => DEGRADED;
- reconnect no puede borrar ni ocultar un gap no resuelto;
- shadow con weights/config/timestamp/source hash mismatch => reject;
- repeated identical shadow/forward evidence => idempotent, sin doble conteo;
- forward evidence no puede leer FINAL_HOLDOUT ni recalibrar thresholds/pesos congelados;
- stale/missing/gapped evidence no puede incrementar allocation/risk;
- ningún path R5 puede importar/invocar broker order submission, OMS authority o LIVE execution.

## Restricciones
- No bajar coverage gate de 85%.
- No borrar/relajar negative tests para cerrar deuda.
- `TD-OPS-001` permanece visible; no fabricar `graphify-out`.
- No declarar rentabilidad por infraestructura o forward observability.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER pertenece a R6, no a R5.
""")


def write_context() -> None:
    CONTEXT.write_text(f"""# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **v0.28R R0–R4 certified; R5 active**.

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md`
5. `knowledge/00_CANON/debt_register.json`
6. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`
7. `knowledge/60_EVIDENCE/R4_POST_MERGE_INTEGRATION_AUDIT.json`

## Base R5
Exact post-merge-green `main`: `{GREEN_MAIN}`.
Core Safety `{CORE_RUN}` PASS; Knowledge Contract `{KNOWLEDGE_RUN}` PASS; **483 tests / 86.45%**.

## R5
Branch `reconstruction/r5-stream-shadow-forward`.
`TD-R5-001..006` fueron registrados P1 OPEN antes de implementación.

R5 = closed-kline read-only stream -> duplicate/gap fail-closed -> DEGRADED lifecycle -> synchronized shadow -> forward evidence without HOLDOUT.

## Authority
AI/research/Portfolio Manager/stream/shadow no tienen autoridad de ejecución. Safety + OMS continúan siendo fronteras obligatorias. External PAPER/LIVE no está habilitado.

**LIVE TRADING: BLOQUEADO.**
""")


def write_handoff() -> None:
    HANDOFF.write_text(f"""# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado: **R4 post-merge certified; R5 active**

## Base certificada
`main` `{GREEN_MAIN}` es la base exacta verde después de reparar la primera integración R4 mediante PR #12.
- Core Safety `{CORE_RUN}`: PASS — 483 tests / 86.45%.
- Knowledge Contract `{KNOWLEDGE_RUN}`: PASS.
- Contract Registry / Research Authority / Debt Register: PASS.

Incidente y reparación: `knowledge/60_EVIDENCE/R4_POST_MERGE_INTEGRATION_AUDIT.json`.

## R5
Branch: `reconstruction/r5-stream-shadow-forward`.
Deuda registrada antes de implementar: `TD-R5-001..006`, todas P1 OPEN.

Orden:
1. closed-kline read-only stream;
2. duplicate/gap/order integrity;
3. DEGRADED socket/reconnect semantics;
4. synchronized hash-bound portfolio shadow;
5. append-only forward evidence separado de FINAL_HOLDOUT;
6. permanent authority gate + adversarial certification.

## Invariantes heredados
- Safety + OMS son autoridad determinista obligatoria.
- Portfolio Manager sigue advisory-only.
- R5 nunca aumenta riesgo por evidencia stale/missing/gapped.
- No silent imputation.
- HOLDOUT no se reutiliza para recalibrar forward decisions.
- External PAPER/LIVE queda fuera de R5.

## Deuda no bloqueante
`TD-OPS-001` Graphify P3/OPS sigue OPEN; no fabricar artefactos semánticos/deep.

## Capital
**LIVE TRADING: BLOQUEADO.**
""")


def main() -> None:
    register_debt()
    update_matrix()
    write_post_merge_audit()
    write_debt_md()
    write_state()
    write_task()
    write_context()
    write_handoff()


if __name__ == "__main__":
    main()
