from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT_JSON = ROOT / "knowledge/00_CANON/debt_register.json"
DEBT_MD = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
STATE = ROOT / "knowledge/00_CANON/ESTADO_ACTUAL.md"
TASK = ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md"
HANDOFF = ROOT / "knowledge/40_HANDOFF/HANDOFF_ACTUAL.md"
CERT = ROOT / "knowledge/60_EVIDENCE/R4_CERTIFICATION.json"

BASIS = "556a91ddeb5866313d5117898e97eb4e0308bab2"
CORE_RUN = 31460873058
KNOWLEDGE_RUN = 31460873011


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    if old not in text:
        raise SystemExit(f"{label} not found")
    path.write_text(text.replace(old, new, 1))


def certify_debt_register() -> None:
    data = json.loads(DEBT_JSON.read_text())
    blocking = [
        item["id"]
        for item in data["items"]
        if item.get("track") == "R4"
        and item.get("severity") in {"P0", "P1", "P2"}
        and item.get("status") != "CLOSED"
    ]
    if blocking:
        raise SystemExit(f"cannot certify R4 with blocking debt: {blocking}")
    tracks = data["certified_tracks"]
    if "R4" not in tracks:
        tracks.append("R4")
    expected = ["R0", "R1", "R2", "R3", "R4"]
    if tracks != expected:
        raise SystemExit(f"unexpected certified track ordering: {tracks}")
    DEBT_JSON.write_text(json.dumps(data, indent=2) + "\n")


def write_human_debt() -> None:
    DEBT_MD.write_text(
        """# DEBT REGISTER — v0.28R

Fecha: 2026-08-11
Estado: **R4 BRANCH CERTIFIED FOR MERGE — R5 bloqueado hasta recertificación post-merge**

## Authority
La autoridad machine-readable es `knowledge/00_CANON/debt_register.json`. Si esta vista humana discrepa, manda el JSON + CI y este archivo debe repararse.

## Regla
Ningún track puede certificarse con deuda conocida P0/P1/P2 asignada a ese track. Una deuda nueva se registra antes del cierre; su severidad no se reduce para hacer pasar una release.

## Certified tracks
- **R0 — PASS**
- **R1 — PASS**
- **R2 — PASS**
- **R3 — PASS e integrado en `main` `c585a84b5197076b210723bb70980b828e4e3026` con recertificación post-merge verde**
- **R4 — BRANCH PASS / CERTIFIED FOR MERGE**

## R4 — cierre certificado
Deuda R4 P0/P1/P2 conocida: **0 abierta**.

Slices cerrados con evidencia:
- `TD-R4-001` — Instrument Master autoritativo y versionado.
- `TD-R4-002` — dependencia/correlación + budgets de diversificación.
- `TD-R4-003` — allocation perturbation + leave-one-out robustness.
- `TD-R4-004` — régimen TRAIN/DEVELOPMENT-only; HOLDOUT solo evaluación congelada.
- `TD-R4-005` — Strategy/Portfolio Health & Drift durable.
- `TD-R4-006` — Defensive Health Bridge durable, reduce/block-only, integrado con Safety/OMS.
- `TD-R4-007` — Portfolio Manager determinista, acotado y advisory-only; sin autoridad OMS/broker.
- `TD-R4-008` — auditoría de invariantes de Portfolio State.
- `TD-R4-009` — integridad durable de fills en lectura/replay.
- `TD-R4-010` — compromiso hash + validación semántica durable de Portfolio State.
- `TD-R4-011` — normalización Decimal exacta de robustez; `sum(weights)==1` preservado para ratios periódicos.
- `TD-R4-012` — acknowledgements de recovery retry-safe mediante `recovery_id` durable.
- `TD-R4-013` — overlay de Health autoritativo en cada lectura Safety/OMS; worsening no sincronizado endurece inmediatamente.
- `TD-R4-014` — recovery ACK hash-chain anclado al fingerprint durable de Health state.

Base de certificación R4: `556a91ddeb5866313d5117898e97eb4e0308bab2`.
- **479 tests PASS / 86.45% branch coverage**;
- Contract Registry PASS;
- Research Authority Boundary PASS;
- Debt Register Contract PASS;
- Knowledge Contract PASS;
- artefacto: `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`.

## Deuda abierta
| ID | Sev | Track | Área | Condición de cierre |
|---|---|---|---|---|
| `TD-OPS-001` | P3 | OPS | Graphify | generar graph semántico/deep real en runtime soportado y vincularlo a `SOURCE_SHA`; nunca fabricar artefactos |

`TD-OPS-001` es P3/OPS y no bloquea la certificación R4. Permanece explícitamente abierta.

## Próximo orden
1. mantener PR #11 sin cambios funcionales y con CI verde;
2. merge de PR #11 únicamente cuando se decida integrar R4;
3. recertificar el SHA exacto de `main` después del merge;
4. abrir R5 sólo desde ese `main` recertificado.

## Capital
**LIVE TRADING: BLOQUEADO.**
R4 no concede autoridad PAPER/LIVE ni demuestra rentabilidad.
"""
    )


def update_matrix() -> None:
    replace_once(
        MATRIX,
        "Estado: ACTIVE — **R0–R3 certified; R4 next**",
        "Estado: ACTIVE — **R0–R4 certified; R5 next after R4 merge + post-merge recertification**",
        "matrix state",
    )
    replace_once(
        MATRIX,
        "| R4 | versioned Portfolio State / reconciliation infrastructure | v0.18 lineage | PASS | durable/versioned base exists from R0/R2; R4 portfolio-governance invariants still need certification |",
        "| R4 | versioned Portfolio State / reconciliation infrastructure | v0.18 lineage | PASS | shared semantic snapshot validation + durable portfolio hash commitment + atomic fill/projection integrity certified (`TD-R4-008`,`TD-R4-009`,`TD-R4-010`) |",
        "portfolio-state matrix evidence",
    )
    old = """## Active target — R4
R4 closes portfolio/regime/health governance before R5 shadow/forward monitoring:
1. authoritative instrument master;
2. audit/reuse existing versioned portfolio state and certify R4 invariants;
3. correlation-aware diversification/concentration research;
4. allocation perturbation + leave-one-out;
5. TRAIN-only regime calibration;
6. Strategy/Portfolio Health & Drift;
7. reduce/block-only Defensive Health Bridge;
8. deterministic sizing + cross-strategy budgets;
9. keep PAPER/LIVE fail-closed.
"""
    new = """### R4
Branch certification basis: `556a91ddeb5866313d5117898e97eb4e0308bab2`.
Latest certified closure evidence: **479 tests PASS / 86.45% coverage**, Contract Registry PASS, Research Authority PASS, Debt Register PASS and Knowledge Contract PASS.
All known R4 P0/P1/P2 debt (`TD-R4-001..014`) is CLOSED.
Certification artifact: `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`.

R4 remains advisory/defensive with respect to capital: no external PAPER/LIVE execution authority is introduced.

## Next target — R5
R5 reconstructs closed-kline read-only streaming and synchronized portfolio shadow/forward evidence. It must start only after PR #11 is merged and the exact R4 merge SHA on `main` is recertified green.
"""
    replace_once(MATRIX, old, new, "R4 active-target transition")


def write_state() -> None:
    STATE.write_text(
        """# ESTADO ACTUAL

Fecha: 2026-08-11
Fase: v0.28R Reconstruction — **R4 BRANCH CERTIFIED FOR MERGE; R5 gated by post-merge recertification**

## Estado de certificación
- **R0 — PASS:** foundation durable del control plane.
- **R1 — PASS:** market-data/backtesting/Strategy DSL/research-integrity foundation.
- **R2 — PASS:** Capital Safety + OMS lifecycle/control-plane maturity.
- **R3 — PASS e integrado en `main`** `c585a84b5197076b210723bb70980b828e4e3026`.
- **R4 — BRANCH PASS / CERTIFIED FOR MERGE** sobre base CI `556a91ddeb5866313d5117898e97eb4e0308bab2`.
- **R5–R6 — NOT CERTIFIED.**

## R4 — capacidades certificadas
R4 cierra Portfolio / Regime / Health Governance sin crear autoridad externa sobre capital:
- Instrument Master autoritativo, versionado, hash-bound y fail-closed ante metadata desconocida/stale/conflictiva;
- Portfolio State/reconciliation auditado, con validación semántica compartida, fill-read integrity y compromiso SHA-256 durable;
- dependencia/correlación sobre panel común con clusters recomputables y budgets exactos strategy/cluster/portfolio;
- allocation robustness determinista con perturbaciones, leave-one-out y normalización Decimal exacta;
- regímenes calibrables sólo en TRAIN/DEVELOPMENT; HOLDOUT evalúa modelo congelado y stale/missing => UNKNOWN;
- Strategy/Portfolio Health durable con baseline/policy binding, empeoramiento monotónico automático y recovery explícito;
- recovery ACK idempotente y tamper-evident mediante hash-chain anclado en el fingerprint durable del Health state;
- Defensive Health Bridge reduce/block-only, stricter-state-wins y overlay de Health autoritativo en cada lectura Safety/OMS;
- Portfolio Manager determinista y advisory-only, con budgets/robustness/venue rules recomputados y quantity round-down.

## Evidencia R4
Certification basis: `556a91ddeb5866313d5117898e97eb4e0308bab2`.
- **479 tests PASS**;
- **86.45% total branch coverage** con gate 85% intacto;
- Contract Registry PASS — 10 contracts — SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- Research Authority Boundary PASS;
- Debt Register Contract PASS;
- Knowledge Contract PASS;
- Core Safety run `31460873058`;
- Knowledge Contract run `31460873011`;
- certification artifact: `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`.

## Deuda
Toda deuda conocida R4 P0/P1/P2 (`TD-R4-001..014`) está CLOSED.
Permanece `TD-OPS-001` P3/OPS para Graphify semántico/deep real; no bloquea R4 y no se fabrican artefactos Graphify.

## Estado de capital
**LIVE TRADING: BLOQUEADO.**
R4 no demuestra rentabilidad, no promociona capital y no concede autoridad external PAPER/LIVE. Portfolio Manager sigue siendo advisory/control-plane; Safety Kernel + OMS conservan autoridad determinista.

## Próximo hito
Mantener PR #11 verde, integrar R4 cuando corresponda, recertificar el SHA exacto de `main` post-merge y sólo entonces abrir R5 para closed-kline read-only streaming + synchronized shadow/forward evidence.
"""
    )


def write_task() -> None:
    TASK.write_text(
        """# TAREA ACTIVA

## Estado
**R4 — BRANCH CERTIFIED FOR MERGE.**
No continuar con mutaciones R5 sobre esta rama. La transición exige integración y recertificación exacta de `main`.

## Objetivo inmediato
1. preservar PR #11 sin cambios funcionales no auditados;
2. exigir Core Safety + Knowledge Contract verdes en el head final;
3. merge sólo cuando se decida integrar R4;
4. ejecutar recertificación post-merge sobre el SHA exacto de `main`;
5. crear la rama R5 únicamente desde ese SHA verde.

## R4 cerrado
Capacidades R4 certificadas:
- authoritative Instrument Master;
- versioned Portfolio State / reconciliation integrity;
- correlation-aware portfolio research + concentration budgets;
- allocation perturbation + leave-one-out robustness con Decimal exacto;
- TRAIN/DEVELOPMENT-only regime calibration;
- Strategy/Portfolio Health & Drift;
- retry-safe + tamper-evident recovery acknowledgements;
- reduce/block-only Defensive Health Bridge con authoritative Health overlay;
- deterministic bounded Portfolio Manager / sizing advisory-only.

Toda deuda conocida R4 P0/P1/P2 `TD-R4-001..014` está CLOSED.
Base CI de certificación: `556a91ddeb5866313d5117898e97eb4e0308bab2` — 479 tests PASS / 86.45% coverage.

## Próximo track — R5
R5 reconstruirá **closed-kline read-only streaming + synchronized portfolio shadow/forward evidence**.

Capacidades objetivo según `RECONSTRUCTION_V028R_MATRIX.md`:
1. stream read-only de velas cerradas, deshabilitado por defecto y con host fijo;
2. idempotencia de duplicados + gap detection fail-closed, sin imputación silenciosa;
3. terminación/ambigüedad del socket => `DEGRADED`, sin reconnect que oculte gaps;
4. portfolio shadow sincronizado con pesos congelados y timestamps exactos;
5. forward evidence post-activation separado de HOLDOUT.

## Guardrails R5
- no broker order submission;
- no external PAPER/LIVE authority;
- no uso de HOLDOUT para recalibrar decisiones forward;
- stale/missing/gapped stream nunca puede aumentar riesgo ni producir evidencia optimista;
- cualquier shadow state debe ser reproducible, hash-bound e idempotente;
- promoción a R6 queda bloqueada hasta certificar R5 y recertificar su merge SHA.

## Negative tests obligatorios R5
- stream deshabilitado por defecto no abre conexiones ni hace I/O;
- host/path/protocolo no permitido => reject antes de I/O;
- vela abierta, fuera de orden, duplicada conflictiva o timestamp futuro => fail closed;
- duplicado idéntico => idempotent no-op; duplicado conflictivo => fail closed;
- gap temporal o sequence gap => `DEGRADED`, sin imputación ni avance optimista;
- socket EOF/error/timeout/ambigüedad => `DEGRADED`; reconnect no puede ocultar un gap no resuelto;
- shadow con pesos/config/dataset/hash no coincidente => reject;
- repeated identical shadow/forward evidence => idempotent, sin doble conteo;
- forward evidence no puede leer FINAL_HOLDOUT ni recalibrar thresholds/pesos congelados;
- stale/missing stream o shadow no puede incrementar allocation/risk;
- ninguna ruta R5 puede importar o invocar broker order submission, OMS authority o LIVE execution.

## Deuda no bloqueante
`TD-OPS-001` — P3/OPS Graphify real. Mantener abierta hasta disponer de runtime soportado; nunca fabricar `graphify-out/`.

## Capital
**LIVE TRADING: BLOQUEADO.**
"""
    )


def write_handoff() -> None:
    HANDOFF.write_text(
        """# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado: **R4 branch certified for merge; PR #11 integration pending**

## Base integrada conocida
R3 está integrado y post-merge certificado en `main` `c585a84b5197076b210723bb70980b828e4e3026`.

## R4
Branch: `reconstruction/r4-portfolio-health`
PR: #11
Certification basis: `556a91ddeb5866313d5117898e97eb4e0308bab2`
Evidence: `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`
Result: **479 tests PASS / 86.45% coverage**, 10 contracts, Research/Advisory Authority PASS, Debt Register PASS, Knowledge Contract PASS.

Todos los P0/P1/P2 conocidos de R4 (`TD-R4-001..014`) están CLOSED. Todas las filas requeridas R4 de la capability matrix están PASS.

## Invariantes de cierre que no se deben perder
- Instrument Master autoritativo separado de research metadata.
- exact Decimal normalization; no tolerance weakening.
- Health recovery explicit + retry-safe + ACK-chain tamper-evident.
- unsynced authoritative worsening tightens immediately.
- Defensive Health Bridge automatic actions reduce/block only.
- Portfolio Manager advisory-only; no OrderIntent/OMS/broker authority.
- Safety + OMS remain mandatory; true risk reductions remain available under restrictive health states only when Safety classifies them as reducing.

## Próxima acción exacta
1. mantener CI final verde sobre el branch canónico de cierre;
2. actualizar PR #11 como R4 certified for merge;
3. merge únicamente por decisión explícita, contra el expected head SHA;
4. recertificar exact merge SHA en `main`;
5. crear R5 únicamente desde ese `main` verde.

## R5 después del merge
Read-only closed-kline stream -> duplicate/gap fail-closed -> DEGRADED socket semantics -> synchronized shadow -> forward evidence without HOLDOUT.

## Deuda no bloqueante
`TD-OPS-001` Graphify P3/OPS sigue OPEN; no se fabrican artefactos semánticos/deep sin runtime soportado y `SOURCE_SHA` verificable.

## Capital
**LIVE TRADING: BLOQUEADO.**
R4 no añadió external PAPER/LIVE authority.
"""
    )


def write_certification() -> None:
    cert = {
        "schema_version": 1,
        "track": "R4",
        "reconstruction_target": "v0.28R",
        "certification_state": "BRANCH_CERTIFIED_FOR_MERGE",
        "branch": "reconstruction/r4-portfolio-health",
        "certification_basis_commit": BASIS,
        "ci": {
            "core_safety_run_id": CORE_RUN,
            "knowledge_contract_run_id": KNOWLEDGE_RUN,
            "compile": "PASS",
            "tests_passed": 479,
            "coverage_percent": 86.45,
            "coverage_gate_percent": 85.0,
            "contract_registry": "PASS",
            "contract_count": 10,
            "contract_registry_sha256": "ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785",
            "research_authority_boundary": "PASS",
            "debt_register_contract": "PASS",
            "knowledge_contract": "PASS"
        },
        "closed_r4_debt": [f"TD-R4-{n:03d}" for n in range(1, 15)],
        "certified_capabilities": [
            "authoritative versioned instrument master with provenance, staleness and fingerprint integrity",
            "versioned portfolio-state and durable fill/projection integrity with shared semantic validation",
            "correlation-aware portfolio dependence with recomputable clusters and exact concentration budgets",
            "deterministic allocation perturbation and leave-one-out robustness with exact Decimal normalization",
            "TRAIN/DEVELOPMENT-only regime calibration with frozen HOLDOUT evaluation and UNKNOWN conservative state",
            "durable Strategy/Portfolio Health & Drift with immutable baseline/policy binding",
            "retry-safe and tamper-evident recovery acknowledgements anchored by an append-only hash chain",
            "reduce/block-only Defensive Health Bridge with stricter-state-wins and authoritative Health overlay",
            "deterministic bounded Portfolio Manager / sizing that remains advisory-only and recomputes safety constraints"
        ],
        "open_debt_outside_r4": ["TD-OPS-001"],
        "explicit_non_claims": [
            "R4 certification is not profitability evidence.",
            "R4 certification does not authorize external PAPER order submission.",
            "R4 certification does not authorize LIVE trading or capital promotion.",
            "Graphify semantic/deep artifacts remain unavailable until a supported runtime generates them from a bound SOURCE_SHA."
        ],
        "capital_state": "LIVE_TRADING_BLOCKED",
        "next_track": "R5_AFTER_R4_MERGE_AND_POST_MERGE_MAIN_RECERTIFICATION"
    }
    CERT.write_text(json.dumps(cert, indent=2) + "\n")


def main() -> None:
    certify_debt_register()
    write_human_debt()
    update_matrix()
    write_state()
    write_task()
    write_handoff()
    write_certification()


if __name__ == "__main__":
    main()
