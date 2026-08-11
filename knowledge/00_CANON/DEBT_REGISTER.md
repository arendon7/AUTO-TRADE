# DEBT REGISTER — v0.28R

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
- **R4 — PASS e integrado/post-merge recertificado en `main` `c294aa69f35b64559e3aea58a1c0661e66599db8`**
- **R5 — PASS en branch; PR #13 pendiente de integración y recertificación post-merge**

Certificación R5: `knowledge/60_EVIDENCE/R5_CERTIFICATION.json`, basis `0d4f75d083a055b83646bb861f08731aecace560`: **606 tests PASS / 86.49% coverage**, Contract Registry 10 PASS, Research Authority PASS, R5 Authority Boundary PASS, Debt Register PASS y Knowledge Contract PASS.

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
