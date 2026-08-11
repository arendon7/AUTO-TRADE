# DEBT REGISTER — v0.28R

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
- **R4 — PASS e integrado; post-merge recertificado en `main` `c294aa69f35b64559e3aea58a1c0661e66599db8`**

Post-merge R4: Core Safety `31463746764` PASS, Knowledge Contract `31463746745` PASS, **483 tests / 86.45% coverage**.

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
