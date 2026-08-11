from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "knowledge/00_CANON/debt_register.json"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
HUMAN = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
EVIDENCE = "knowledge/60_EVIDENCE/R4_RESEARCH_GOVERNANCE_CERTIFICATION.json"


UPDATES = {
    "TD-R4-002": {
        "resolution": (
            "Canonical correlation/dependence evidence uses one common aligned panel, "
            "deterministic recomputed clusters and exact strategy/cluster/portfolio budget "
            "validation; insufficient or forged evidence fails closed and the module has no "
            "execution authority."
        ),
        "evidence": [
            "src/autotrade/research/portfolio_dependence.py",
            "tests/test_r4_portfolio_dependence.py",
            "tests/test_r4_dependence_integrity.py",
            "tests/test_r4_dependence_universe_integrity.py",
            EVIDENCE,
        ],
    },
    "TD-R4-003": {
        "resolution": (
            "Deterministic complete leave-one-out and bounded allocation perturbation evidence "
            "is recomputed from source inputs; non-positive/degenerate baselines, budget "
            "violations and fragile allocations fail closed."
        ),
        "evidence": [
            "src/autotrade/research/allocation_robustness.py",
            "tests/test_r4_allocation_robustness.py",
            EVIDENCE,
        ],
    },
    "TD-R4-004": {
        "resolution": (
            "Regime calibration structurally permits TRAIN/DEVELOPMENT only; FINAL_HOLDOUT "
            "evaluates frozen models only. Missing/stale/future features are UNKNOWN and the "
            "append-only registry verifies durable lineage, row identity and parser integrity "
            "before advancing versions."
        ),
        "evidence": [
            "src/autotrade/research/regimes.py",
            "tests/test_r4_regime_governance.py",
            EVIDENCE,
        ],
    },
    "TD-R4-005": {
        "resolution": (
            "Durable Strategy/Portfolio Health & Drift uses immutable baseline/policy binding, "
            "separate entity namespaces, hash-verified state, monotone automatic worsening, "
            "non-consecutive replay idempotency and explicit fresh-evidence acknowledged recovery."
        ),
        "evidence": [
            "src/autotrade/research/health.py",
            "tests/test_r4_health_drift.py",
            "tests/test_r4_health_binding_integrity.py",
            EVIDENCE,
        ],
    },
}


MATRIX_REPLACEMENTS = {
    "| R4 | correlation-aware portfolio research | v0.18 | TODO | diversification/concentration constraints using authorized research evidence |":
        "| R4 | correlation-aware portfolio research | v0.18 | PASS | common-panel dependence + anti-forgery clusters + exact strategy/cluster/portfolio budgets (`TD-R4-002`) |",
    "| R4 | allocation perturbation + leave-one-out | v0.18 | TODO | deterministic robustness reports tied to immutable inputs |":
        "| R4 | allocation perturbation + leave-one-out | v0.18 | PASS | complete deterministic scenario set + recomputable robustness gate (`TD-R4-003`) |",
    "| R4 | TRAIN-calibrated regimes | v0.18 | TODO | no HOLDOUT-derived thresholds; unknown regime conservative |":
        "| R4 | TRAIN-calibrated regimes | v0.18 | PASS | TRAIN/DEVELOPMENT-only calibration + frozen HOLDOUT evaluation + UNKNOWN on stale/missing evidence (`TD-R4-004`) |",
    "| R4 | Strategy/Portfolio Health & Drift | v0.19 | TODO | immutable baselines/reports + explicit state transitions |":
        "| R4 | Strategy/Portfolio Health & Drift | v0.19 | PASS | immutable baseline/policy-bound durable state + monotone automatic worsening + acknowledged fresh-evidence recovery (`TD-R4-005`) |",
}


HUMAN_TEXT = """# DEBT REGISTER — v0.28R

Fecha: 2026-08-10
Estado: ACTIVE — R4 en reconstrucción

## Authority
La autoridad machine-readable es `knowledge/00_CANON/debt_register.json`. Si esta vista humana discrepa, manda el JSON + CI y este archivo debe repararse.

## Regla
Ningún track puede certificarse con deuda conocida P0/P1/P2 asignada a ese track. Una deuda nueva se registra antes del cierre; su severidad no se reduce para hacer pasar una release.

## Certified tracks
- **R0 — PASS**
- **R1 — PASS**
- **R2 — PASS**
- **R3 — PASS e integrado en `main` `c585a84b5197076b210723bb70980b828e4e3026` con recertificación post-merge verde**

## R4 — estado actual
Slices cerrados con evidencia:
- `TD-R4-001` — Instrument Master autoritativo y versionado.
- `TD-R4-002` — dependencia/correlación + budgets de diversificación.
- `TD-R4-003` — allocation perturbation + leave-one-out robustness.
- `TD-R4-004` — régimen TRAIN/DEVELOPMENT-only; HOLDOUT solo evaluación congelada.
- `TD-R4-005` — Strategy/Portfolio Health & Drift durable.
- `TD-R4-008` — auditoría de invariantes de Portfolio State.
- `TD-R4-009` — integridad durable de fills en lectura/replay.
- `TD-R4-010` — compromiso hash + validación semántica durable de Portfolio State.

Evidencia conjunta para `TD-R4-002..005`: **412 tests PASS / 86.79% coverage**, Contract Registry PASS, Research Authority PASS, Debt Register PASS y Knowledge Contract PASS sobre `76e1eec851f433f9e5c4c49f786ae79c7a846ee0` antes del commit documental de cierre.

## Deuda abierta
| ID | Sev | Track | Área | Condición de cierre |
|---|---|---|---|---|
| `TD-R4-006` | P1 | R4 | Defensive Health Bridge | automatización solo puede reducir/bloquear/quarantinar riesgo; stricter-state-wins; recuperación explícita con evidencia fresca; jamás aumenta exposición ni omite Safety/OMS |
| `TD-R4-007` | P1 | R4 | Portfolio Manager / sizing | sizing determinista y acotado bajo budgets de estrategia/cluster/portfolio; output sin autoridad de broker/OMS |
| `TD-OPS-001` | P3 | OPS | Graphify | generar graph semántico/deep real en runtime soportado y vincularlo a `SOURCE_SHA`; nunca fabricar artefactos |

Graphify P3/OPS no es una deuda P0/P1/P2 del track R4, pero permanece explícitamente abierta.

## Próximo orden
1. `TD-R4-006` Defensive Health Bridge.
2. `TD-R4-007` deterministic bounded sizing.
3. auditoría adversarial R4 completa.
4. cerrar cualquier deuda nueva encontrada.
5. sincronizar canon y certificar PR #11.
6. merge solo con CI verde y recertificar el SHA exacto de `main` antes de R5.

## Capital
**LIVE TRADING: BLOQUEADO.**
Ningún estado de deuda ni certificación de research concede autoridad PAPER/LIVE.
"""


def main() -> None:
    if not (ROOT / EVIDENCE).exists():
        raise SystemExit(f"missing certification evidence: {EVIDENCE}")

    data = json.loads(DEBT.read_text())
    found: set[str] = set()
    for item in data["items"]:
        debt_id = item["id"]
        if debt_id in UPDATES:
            found.add(debt_id)
            if item["track"] != "R4" or item["severity"] not in {"P0", "P1", "P2"}:
                raise SystemExit(f"unexpected debt ownership/severity for {debt_id}")
            item["status"] = "CLOSED"
            item["resolution"] = UPDATES[debt_id]["resolution"]
            item["evidence"] = UPDATES[debt_id]["evidence"]
            item["next_action"] = ""

    missing = set(UPDATES) - found
    if missing:
        raise SystemExit(f"missing debt ids: {sorted(missing)}")

    # R4 is not certified here; only the four independently evidenced slices close.
    if "R4" in data["certified_tracks"]:
        raise SystemExit("R4 must not be certified by a slice-closure script")

    DEBT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    matrix = MATRIX.read_text()
    for old, new in MATRIX_REPLACEMENTS.items():
        if old not in matrix:
            if new in matrix:
                continue
            raise SystemExit(f"matrix row not found: {old}")
        matrix = matrix.replace(old, new, 1)
    MATRIX.write_text(matrix)
    HUMAN.write_text(HUMAN_TEXT)


if __name__ == "__main__":
    main()
