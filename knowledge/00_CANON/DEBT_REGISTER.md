# DEBT REGISTER — v0.28R

Fecha: 2026-08-11
Estado: ACTIVE — **R0–R4 CERTIFIED; R5 NEXT**

## Authority
La autoridad machine-readable es `knowledge/00_CANON/debt_register.json`. Si esta vista humana discrepa, manda el JSON + CI.

## Regla
Ningún track puede certificarse con deuda conocida P0/P1/P2 asignada a ese track. Deuda nueva se registra antes del cierre y no se rebaja para satisfacer una release.

## Certified tracks
- **R0 — PASS**
- **R1 — PASS**
- **R2 — PASS**
- **R3 — PASS e integrado en `main` `c585a84b5197076b210723bb70980b828e4e3026`**
- **R4 — PASS en branch; PR #11 pendiente de integración y recertificación post-merge**

Certificación R4: `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`, basis `350efd43ac133c95a1997b4a821a2e0bab4afaf2`: **479 tests PASS / 86.45% coverage**, 10 contratos, Research/Advisory Authority PASS, Debt Register PASS y Knowledge Contract PASS.

## R4 debt closure
Todos los P0/P1/P2 conocidos de R4 están CLOSED: `TD-R4-001..014`.
Esto incluye los hardenings tardíos de exact Decimal normalization, retry-safe recovery, authoritative Health overlay y recovery ACK hash-chain.

## Deuda abierta
| ID | Sev | Track | Área | Condición de cierre |
|---|---|---|---|---|
| `TD-OPS-001` | P3 | OPS | Graphify | generar graph semántico/deep real en runtime soportado y vincularlo a `SOURCE_SHA`; nunca fabricar artefactos |

No existe P0/P1/P2 OPEN de R4. Graphify P3/OPS no bloquea la certificación R4 y permanece explícitamente visible.

## Próximo orden — R5
1. integrar PR #11 sólo contra su head validado;
2. recertificar el SHA exacto de `main` post-merge;
3. registrar deuda/capacidades R5 antes de implementar;
4. closed-kline read-only stream;
5. duplicate idempotency + gap fail-closed;
6. socket termination -> DEGRADED;
7. synchronized portfolio shadow;
8. forward evidence sin HOLDOUT;
9. certificación adversarial y debt closure;
10. external PAPER/LIVE continúa bloqueado.

## Capital
**LIVE TRADING: BLOQUEADO.**
R4 no concede external PAPER/LIVE authority. R5 tampoco podrá hacerlo; esa frontera pertenece a R6 y requerirá certificación separada.
