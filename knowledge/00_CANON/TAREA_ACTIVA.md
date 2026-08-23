# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-23

## Objetivo inmediato
**Cerrar W79 como capa de Strategy Promotion Governance + Strategy Lab read-only sobre W78 certificado, con exact-head CI completo y sin conceder PAPER candidate, capital authority ni LIVE.**

Stack actual:
- R6 first-canary broker-truth: certificado;
- R7 close real: `work/r7-paper-close-mac-staging` / PR #49, gate operativo independiente;
- W78 execution qualification: `work/w78-realistic-paper-execution` / PR #50, exact-head certificado `2924456e33c2cc9e6579301b176267513a90861f`;
- W79: `work/w79-strategy-promotion-evidence` / PR #51, DRAFT apilado sobre W78.

R0–R5 permanecen los tracks certificados del machine debt register. R6 ya tiene broker truth real alcanzado, pero la promoción canónica de tracks sigue gobernada por el registro machine-readable existente.

## Qué debe quedar cerrado en W79

### 1. Gobernanza de promoción
W79 ya implementa:
- threshold policy congelada antes de DEVELOPMENT;
- DEVELOPMENT y FINAL_HOLDOUT como campañas separadas;
- candidato congelado después del Tournament DEVELOPMENT y antes del HOLDOUT final;
- strategy id + strategy version;
- selected trial fingerprint;
- tournament fingerprint;
- una sola autoridad SQLite para threshold policy, candidate policy y trial ledger;
- transacciones append-only/idempotentes por identidad;
- políticas hash-bound.

Gates canónicos:
- `DEVELOPMENT_SELECTION`;
- `EXECUTION_SENSITIVITY`;
- `FINAL_HOLDOUT`;
- `MULTIPLE_TESTING`.

Estados:
- PASS;
- FAIL;
- MISSING;
- BLOCKED.

Contrato obligatorio:

`EVIDENCE_QUALIFIED != PAPER_CANDIDATE`.

W79 siempre mantiene:
- `paper_candidate_authorized=false`;
- `external_execution_authorized=false`;
- `capital_authority=NONE`;
- `live_trading=BLOCKED`.

### 2. Strategy Lab read-only
El Control Center ya incorpora `/strategy-lab` y `/api/strategy-lab` GET-only.

Debe conservarse:
- `core.sqlite3` abierto con `mode=ro`;
- `PRAGMA query_only=ON`;
- cero INSERT/UPDATE/DELETE/DDL desde el read model;
- sin broker network;
- sin credenciales;
- sin `SAFE_ACTIONS`;
- sin ruta POST;
- sin construcción de `OrderIntent`;
- sin Safety/OMS execution authority;
- sin `localStorage`/`sessionStorage` como autoridad;
- provenance hash verificable;
- blockers visibles;
- PAPER candidate FALSE;
- LIVE BLOCKED.

W79 todavía no persiste un assessment autoritativo de gates. Hasta que exista un diseño durable posterior, la UI debe mostrar exactamente:

`gate_evidence_state=NOT_PERSISTED_BY_W79`.

No sintetizar PASS a partir de datos parciales.

## Blockers que W79 no puede cerrar por interpretación

- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `FEE_ACCOUNTING_INCOMPLETE`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN`.

Además siguen vigentes las deudas R7D:
- `TD-R7D-001` P1 — total execution-cost continuity Research -> PAPER;
- `TD-R7D-002` P1 — fee-complete execution accounting;
- `TD-R7D-003` P2 — safe remaining-quantity reservation after partial fills.

## Gate de cierre exact-head W79

No declarar W79 cerrado hasta que el **mismo head final** demuestre:
1. dedicated W79 workflow PASS usando la definición actual que compila y prueba promotion + read model + dashboard;
2. W79 no-execution promotion boundary PASS;
3. W79 Strategy Lab read-only boundary PASS;
4. Mac Control Center boundary PASS;
5. suite W79 completa PASS;
6. W78 execution boundary re-probado;
7. Research authority boundary re-probado;
8. Core Safety completo PASS;
9. coverage >=85%;
10. Debt Register PASS;
11. Canonical Knowledge PASS;
12. todos los gates heredados sin regresión.

No reutilizar un run verde de un head anterior para certificar código o workflow añadido después.

## R7B — obligación operacional separada

PR #49 mantiene el cierre real de la exposición BTC/USD residual del first canary.

W79 no debe tocar:
- writer real;
- lifecycle de close;
- credential handling;
- broker POST;
- reconciliation real;
- residual-exposure policy.

La política R7B sigue siendo:
- PAPER únicamente;
- strict risk reduction;
- un POST máximo por attempt;
- durable UNKNOWN antes de I/O;
- ambigüedad => GET-only reconciliation;
- residual exposure => stop, no segundo SELL automático;
- LIVE bloqueado.

## Siguiente bloque permitido sólo después de cerrar W79

Diseñar y persistir un **Promotion Assessment durable, hash-bound y reproducible** que:
- vincule cada gate a evidencia inmutable;
- se ate a la exact `StrategyPromotionThresholdPolicy` y `StrategyPromotionPolicy`;
- capture evidence hashes, resultado, reason codes y provenance;
- permita al Strategy Lab leer resultados reales en lugar de `NOT_PERSISTED_BY_W79`;
- siga sin conceder PAPER candidate authority.

Ese bloque no es todavía Auto-Paper.

## Negative tests permanentes

Conservar y ampliar:
- threshold policy registrada después de iniciar DEVELOPMENT;
- candidato congelado después de observar HOLDOUT;
- DEVELOPMENT/HOLDOUT con misma campaign id;
- strategy version drift;
- tournament/trial fingerprint mismatch;
- policy hash tampering;
- SQLite runtime distinto entre policies y trial ledger;
- duplicate/identity conflict;
- gate set incompleto o inventado;
- gate evidence sintetizada por la UI;
- read model con mutación SQL;
- Strategy Lab entrando a POST/SAFE_ACTIONS;
- credenciales o broker network desde Strategy Lab;
- `OrderIntent` creado por promotion governance;
- W79 importando writer/Safety/OMS/TradingPipeline;
- PAPER candidate pasando a true dentro de W79;
- LIVE host/path;
- IA/model output intentando saltar Strategy Runtime/Safety/OMS.

## No-claims

- R6 broker truth != estrategia rentable;
- W78 simulated execution != fill Alpaca futuro;
- W79 evidence qualification != PAPER candidate;
- Strategy Lab read-only != capital authority;
- HOLDOUT aislado != promoción;
- fee-incomplete evidence != realized profitability;
- PAPER qualification != LIVE qualification.

**LIVE TRADING: BLOQUEADO.**
