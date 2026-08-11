# DEBT REGISTER — v0.28R

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
