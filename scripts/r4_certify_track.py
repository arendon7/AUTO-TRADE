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
CONTEXT = ROOT / "knowledge/00_CANON/CONTEXTO_RAPIDO.md"
AUDIT = ROOT / "knowledge/20_ARQUITECTURA/R4_FINAL_ADVERSARIAL_AUDIT.md"
CERT = ROOT / "knowledge/60_EVIDENCE/R4_CERTIFICATION.json"

CERT_SHA = "350efd43ac133c95a1997b4a821a2e0bab4afaf2"
R3_MAIN = "c585a84b5197076b210723bb70980b828e4e3026"


def main() -> None:
    if not CERT.exists():
        raise SystemExit("R4 certification artifact missing")
    cert = json.loads(CERT.read_text())
    if cert.get("certification_basis_head") != CERT_SHA:
        raise SystemExit("R4 certification basis SHA mismatch")
    if cert.get("status") != "CERTIFIED_BRANCH_PENDING_PR_INTEGRATION":
        raise SystemExit("R4 certificate status is not branch-certified")
    if cert.get("open_r4_blocking_debt_ids") != []:
        raise SystemExit("R4 certificate still reports blocking debt")

    debt = json.loads(DEBT_JSON.read_text())
    blockers = [
        item["id"]
        for item in debt["items"]
        if item.get("track") == "R4"
        and item.get("status") == "OPEN"
        and item.get("severity") in {"P0", "P1", "P2"}
    ]
    if blockers:
        raise SystemExit(f"cannot certify R4 with open blocking debt: {blockers}")
    certified = debt.get("certified_tracks", [])
    if certified not in (["R0", "R1", "R2", "R3"], ["R0", "R1", "R2", "R3", "R4"]):
        raise SystemExit(f"unexpected certified_tracks before R4 closure: {certified}")
    if "R4" not in certified:
        certified.append("R4")
    debt["certified_tracks"] = certified
    DEBT_JSON.write_text(json.dumps(debt, indent=2, ensure_ascii=False) + "\n")

    matrix = MATRIX.read_text()
    old_header = "Estado: ACTIVE — **R0–R3 certified; R4 next**"
    new_header = "Estado: ACTIVE — **R0–R4 certified; R5 next**"
    if old_header in matrix:
        matrix = matrix.replace(old_header, new_header, 1)
    elif new_header not in matrix:
        raise SystemExit("matrix status header not found")

    r4_rows = []
    for line in matrix.splitlines():
        if line.startswith("| R4 |"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) >= 4:
                r4_rows.append(cells[3].upper())
    if not r4_rows or any(status != "PASS" for status in r4_rows):
        raise SystemExit(f"R4 matrix contains non-PASS rows: {r4_rows}")

    ledger_anchor = "## Active target — R4"
    if ledger_anchor in matrix:
        before, after = matrix.split(ledger_anchor, 1)
        if "### R4\n" not in before:
            before += (
                "### R4\n"
                f"Branch certification basis: `{CERT_SHA}`.\n"
                "Final branch evidence before canonical closure: **480 tests PASS / 86.58% coverage**, "
                "Contract Registry 10 PASS, Research/Advisory Authority PASS, Debt Register PASS, "
                "Knowledge Contract PASS.\n"
                "Certification artifact: `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`.\n\n"
                "R4 adds no external PAPER/LIVE authority; Portfolio Manager remains advisory-only and "
                "the Defensive Health Bridge can only maintain/reduce/block risk.\n\n"
            )
        debt_policy_index = after.index("## Debt policy")
        tail = after[debt_policy_index:]
        active = (
            "## Active target — R5\n"
            "R5 reconstruye read-only streaming/shadow/forward evidence sin habilitar capital externo:\n"
            "1. closed-kline read-only stream, disabled by default y host fijo;\n"
            "2. duplicate idempotency + gap fail-closed, sin silent imputation;\n"
            "3. unexpected socket termination -> DEGRADED, sin reconnect que oculte gaps;\n"
            "4. synchronized portfolio shadow con pesos/timestamps congelados;\n"
            "5. forward evidence post-activation separado de HOLDOUT;\n"
            "6. registrar deuda R5 antes de implementar cualquier gap descubierto;\n"
            "7. mantener external PAPER/LIVE bloqueado.\n\n"
        )
        matrix = before + active + tail
    elif "## Active target — R5" not in matrix:
        raise SystemExit("matrix active-target section not found")
    MATRIX.write_text(matrix)

    DEBT_MD.write_text(f'''# DEBT REGISTER — v0.28R

Fecha: 2026-08-10
Estado: ACTIVE — **R0–R4 CERTIFIED; R5 NEXT**

## Authority
La autoridad machine-readable es `knowledge/00_CANON/debt_register.json`. Si esta vista humana discrepa, manda el JSON + CI.

## Regla
Ningún track puede certificarse con deuda conocida P0/P1/P2 asignada a ese track. Deuda nueva se registra antes del cierre y no se rebaja para satisfacer una release.

## Certified tracks
- **R0 — PASS**
- **R1 — PASS**
- **R2 — PASS**
- **R3 — PASS e integrado en `main` `{R3_MAIN}`**
- **R4 — PASS en branch; PR #11 pendiente de integración y recertificación post-merge**

Certificación R4: `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`, basis `{CERT_SHA}`: **480 tests PASS / 86.58% coverage**, 10 contratos, Research/Advisory Authority PASS, Debt Register PASS y Knowledge Contract PASS.

## R4 debt closure
Todos los P0/P1/P2 conocidos de R4 están CLOSED: `TD-R4-001..014`.
Esto incluye los hardenings tardíos de exact Decimal normalization, retry-safe recovery, authoritative Health overlay y recovery ACK hash-chain.

## Deuda abierta
| ID | Sev | Track | Área | Condición de cierre |
|---|---|---|---|---|
| `TD-OPS-001` | P3 | OPS | Graphify | generar graph semántico/deep real en runtime soportado y vincularlo a `SOURCE_SHA`; nunca fabricar artefactos |

No existe P0/P1/P2 OPEN de R4. Graphify P3/OPS no bloquea la certificación R4 y permanece explícitamente visible.

## Próximo orden — R5
1. registrar deuda/capacidades R5 antes de implementar;
2. closed-kline read-only stream;
3. duplicate idempotency + gap fail-closed;
4. socket termination -> DEGRADED;
5. synchronized portfolio shadow;
6. forward evidence sin HOLDOUT;
7. certificación adversarial y debt closure;
8. external PAPER/LIVE continúa bloqueado.

## Capital
**LIVE TRADING: BLOQUEADO.**
R4 no concede external PAPER/LIVE authority. R5 tampoco podrá hacerlo; esa frontera pertenece a R6 y requerirá certificación separada.
''')

    STATE.write_text(f'''# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-10
Estado canónico: **v0.28R reconstruction — R0–R4 CERTIFIED; PR #11 integration pending**

## Fuente de verdad
El proyecto histórico v0.28 alcanzó mayor madurez que la reconstrucción inicial, pero su source package no fue recuperado. La ruta activa es la reconstrucción equivalente v0.28R regida por `SOURCE_OF_TRUTH.md`, `RECONSTRUCTION_V028R_MATRIX.md` y `debt_register.json`.

## Certificaciones actuales
- R0: deterministic safety/durability baseline — PASS.
- R1: Market Data + Strategy DSL + Research Integrity — PASS.
- R2: Capital Safety + OMS maturity — PASS.
- R3: bounded real-data/research governance — PASS e integrado en `main` `{R3_MAIN}`.
- R4: portfolio/regimes/health governance — **PASS en branch**, certificado sobre `{CERT_SHA}` con 480 tests PASS / 86.58% coverage y todos los gates verdes.

## R4 certificado
Incluye:
- Instrument Master autoritativo/versionado;
- Portfolio State/fill durability audit;
- dependence/correlation + cross-strategy/cluster budgets;
- allocation perturbation + leave-one-out con exact Decimal normalization;
- TRAIN/DEVELOPMENT-only regime calibration;
- Strategy/Portfolio Health & Drift durable;
- retry-safe/tamper-evident recovery acknowledgements;
- authoritative unsynced Health overlay;
- reduce/block-only Defensive Health Bridge integrado con Safety/OMS;
- deterministic advisory-only Portfolio Manager/sizing con post-Health/post-venue revalidation;
- permanent Research/Advisory Authority CI boundary.

## Estado de integración
- PR #11 permanece pendiente de merge hasta que el head canónico de cierre vuelva a pasar Core Safety + Knowledge Contract.
- Después del merge, el SHA exacto de `main` debe recertificarse antes de crear R5.
- No iniciar R5 desde la rama pre-merge.

## Deuda
- R4 P0/P1/P2 OPEN: **0**.
- `TD-OPS-001` Graphify P3/OPS: OPEN, no bloqueante.

## Próximo track
**R5 — read-only streaming + synchronized shadow + forward evidence.**
No ejecutar implementación R5 hasta completar integración/post-merge de R4.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER authority añadida por R4: NONE.
''')

    TASK.write_text('''# TAREA ACTIVA — AUTO-TRADE

## Objetivo inmediato
**Integrar R4 certificado en `main`, recertificar el SHA exacto resultante y solo entonces abrir R5.**

R4 ya tiene certificado de rama. La tarea activa NO es seguir añadiendo features R4 ni empezar R5 antes del merge.

## Secuencia obligatoria
1. validar el head final de branch con Core Safety + Knowledge Contract;
2. actualizar PR #11 con evidencia exacta y sacarlo de DRAFT solo si ambos gates están verdes;
3. merge por squash usando `expected_head_sha`;
4. verificar Core Safety + Knowledge Contract sobre el SHA exacto resultante en `main`;
5. solo si `main` queda verde, crear rama R5 desde ese SHA;
6. antes de programar R5, registrar explícitamente sus deudas/capacidades.

## R5 — alcance siguiente, todavía no iniciado
- closed-kline read-only stream, disabled by default y fixed host;
- duplicate idempotency + gap fail-closed; no silent imputation;
- unexpected socket termination -> DEGRADED; no reconnect que esconda gaps;
- synchronized portfolio shadow con pesos/timestamps congelados;
- forward evidence post-activation sin HOLDOUT;
- ninguna autoridad external PAPER/LIVE.

## Negative tests obligatorios para R5
- duplicated closed kline no duplica estado/evidencia;
- gap o out-of-order stream falla cerrado y no imputa;
- stale/malformed/future kline rechazada;
- unexpected socket termination deja estado DEGRADED;
- reconnect no puede ocultar un gap existente;
- shadow con weight/timestamp mismatch falla cerrado;
- forward evidence no toca FINAL_HOLDOUT;
- cualquier path de stream/shadow sigue sin importar OMS/broker execution authority.

## Restricciones
- `TD-OPS-001` Graphify P3 permanece visible; no fabricar `graphify-out`.
- No reducir coverage gate ni borrar negative tests para cerrar R5.
- No declarar rentabilidad por resultados de infraestructura/reproducibilidad.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER queda fuera de R5 y pertenece al track R6.
''')

    HANDOFF.write_text(f'''# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-10
Estado: **R4 branch certified; PR #11 integration pending**

## Base integrada conocida
R3 está integrado y post-merge certificado en `main` `{R3_MAIN}`.

## R4
Branch: `reconstruction/r4-portfolio-health`
PR: #11
Certification basis: `{CERT_SHA}`
Evidence: `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`
Result: **480 tests PASS / 86.58% coverage**, 10 contracts, Research/Advisory Authority PASS, Debt Register PASS, Knowledge Contract PASS.

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
1. correr CI final sobre el branch canónico de cierre;
2. si verde, actualizar PR #11 y marcar ready;
3. merge squash con expected head SHA;
4. recertificar exact merge SHA en `main`;
5. crear R5 únicamente desde ese `main` verde.

## R5 después del merge
Read-only closed-kline stream -> duplicate/gap fail-closed -> DEGRADED socket semantics -> synchronized shadow -> forward evidence without HOLDOUT.

## Deuda no bloqueante
`TD-OPS-001` Graphify P3/OPS sigue OPEN; este runtime no puede fabricar un graph semántico/deep real.

## Capital
**LIVE TRADING: BLOQUEADO.**
R4 no añadió external PAPER/LIVE authority.
''')

    CONTEXT.write_text(f'''# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **v0.28R R0–R4 certified; R4 PR #11 pending integration**.

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md`
5. `knowledge/00_CANON/debt_register.json`
6. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`
7. `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`

## Certificación R4
Basis `{CERT_SHA}`: 480 tests PASS /86.58% coverage; 10 contracts; authority/debt/knowledge gates PASS. R4 blocking debt open: 0.

## Regla operativa inmediata
No empezar R5 desde la rama R4. Primero PR #11 -> merge -> CI verde sobre SHA exacto de `main`; después crear rama R5 desde ese SHA.

## Próximo track
R5 = read-only closed-kline streaming + gap/idempotency semantics + synchronized shadow + forward evidence without HOLDOUT.

## Authority
AI/research/Portfolio Manager no tienen autoridad de ejecución. Safety + OMS continúan siendo fronteras obligatorias. External PAPER/LIVE no está habilitado.

**LIVE TRADING: BLOQUEADO.**
''')

    audit = AUDIT.read_text()
    audit = audit.replace(
        "Estado: **FINAL SCAN CLEAN — TRACK CERTIFICATION PENDING / NO MERGE AÚN**",
        "Estado: **BRANCH CERTIFIED — PR #11 INTEGRATION PENDING / NO MERGE SIN FINAL CI**",
        1,
    )
    if "## Track certification" not in audit:
        audit += (
            "\n## Track certification\n"
            f"Certification basis: `{CERT_SHA}`.\n"
            "Artifact: `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`.\n"
            "All R4 P0/P1/P2 debt is CLOSED and all required R4 matrix rows are PASS.\n"
            "Canonical closure now transitions the next target to R5, but R5 implementation remains blocked until PR #11 is merged and the exact `main` SHA is green.\n"
        )
    AUDIT.write_text(audit)


if __name__ == "__main__":
    main()
