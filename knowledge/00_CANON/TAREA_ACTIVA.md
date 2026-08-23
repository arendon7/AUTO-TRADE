# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-23

## Objetivo inmediato
**W80 — persistir un Promotion Assessment durable, hash-bound y reproducible sobre W79 certificado, sin conceder PAPER candidate, capital authority, broker write ni LIVE.**

Stack actual:
- R0–R5: tracks certificados del machine debt register;
- R6 first-canary broker-truth: certificado;
- R7 close real: `work/r7-paper-close-mac-staging` / PR #49, gate operativo independiente;
- W78 execution qualification: `work/w78-realistic-paper-execution` / PR #50, exact-head `2924456e33c2cc9e6579301b176267513a90861f`;
- W79 Strategy Promotion Governance + Strategy Lab read-only: PR #51, behavioral implementation head certificado `c5c264e64e931ef380801b1e0d1508ea2cac0dfa`.

W79 cerró gobernanza y visualización read-only, pero deliberadamente dejó `gate_evidence_state=NOT_PERSISTED_BY_W79`. W80 debe cerrar exactamente esa deuda sin avanzar todavía a PAPER candidate.

## Scope W80 — Promotion Assessment durable

Diseñar e implementar un assessment autoritativo en la misma autoridad SQLite que W79, con semántica append-only e idempotente por identidad.

El receipt durable debe vincular, como mínimo:
- `assessment_id` estable;
- `promotion_policy_id` y `promotion_policy_hash`;
- `threshold_policy_id` y `threshold_policy_hash`;
- `strategy_id` y `strategy_version` congelados;
- selected trial id + trial fingerprint;
- tournament id + tournament fingerprint;
- DEVELOPMENT campaign id;
- FINAL_HOLDOUT campaign/trial id;
- set exacto de gates;
- status por gate: PASS / FAIL / MISSING / BLOCKED;
- reason codes por gate;
- evidence hashes y provenance por gate;
- assessment state agregado;
- `recorded_at`/provenance temporal;
- hash canónico del receipt completo.

## Reglas de persistencia

W80 debe:
1. reutilizar el mismo `core.sqlite3` autoritativo de W79;
2. persistir mediante transacción fail-closed;
3. ser append-only para identidad material;
4. permitir idempotencia sólo si payload/hash coinciden exactamente;
5. rechazar identity conflict o hash drift;
6. no aceptar resultados visuales como evidencia;
7. poder revalidar el assessment contra policies y evidence source hashes;
8. conservar reason codes explícitos en FAIL/MISSING/BLOCKED;
9. nunca transformar un assessment favorable en authority.

## Strategy Lab W80

El read model podrá sustituir `NOT_PERSISTED_BY_W79` por datos persistidos sólo cuando exista un assessment W80 válido y verificable.

Debe seguir:
- SQLite `mode=ro`;
- `PRAGMA query_only=ON`;
- GET-only;
- cero INSERT/UPDATE/DELETE/DDL desde la UI/read model;
- sin credenciales;
- sin broker network;
- sin `SAFE_ACTIONS`;
- sin POST;
- sin `OrderIntent`;
- sin Safety/OMS execution authority;
- `paper_candidate_authorized=false`;
- `capital_authority=NONE`;
- `LIVE=BLOCKED`.

Si receipt, hashes o policy binding no validan, el read model debe mostrar estado fail-closed y nunca sintetizar PASS.

## Blockers que W80 debe preservar

- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `FEE_ACCOUNTING_INCOMPLETE`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN`;
- `TD-R7D-001` total execution-cost continuity;
- `TD-R7D-002` fee-complete execution accounting;
- `TD-R7D-003` safe remaining-quantity reservation after partial fills.

Un receipt `EVIDENCE_QUALIFIED` seguirá significando únicamente evidencia científica suficiente bajo policy, no permiso de capital.

`EVIDENCE_QUALIFIED != PAPER_CANDIDATE`.

## Fuera de alcance W80

W80 no debe:
- crear PAPER candidate authority;
- modificar writer real;
- importar broker clients;
- usar red o credenciales;
- construir o emitir `OrderIntent`;
- invocar Capital Safety para autorizar ejecución;
- invocar OMS para staging externo;
- modificar R7B close lifecycle;
- habilitar Auto-Paper;
- habilitar LIVE.

## Criterios de cierre W80

No declarar W80 cerrado hasta que el mismo implementation head demuestre:
1. dedicated W80 workflow PASS;
2. Promotion Assessment persistence boundary PASS;
3. assessment hash/idempotency/tamper tests PASS;
4. Strategy Lab persisted-assessment read-only boundary PASS;
5. W79 promotion boundary re-probado;
6. W78 execution boundary re-probado;
7. Research authority boundary re-probado;
8. Mac Control Center boundary/tests PASS si se modifica UI/read model;
9. Core Safety completo PASS;
10. coverage >=85%;
11. Debt Register PASS;
12. Canonical Knowledge PASS.

## R7B — obligación operacional separada
PR #49 mantiene el cierre real de la exposición BTC/USD residual del first canary. W80 no debe tocar writer, lifecycle, credential handling, broker POST, reconciliation real ni residual-exposure policy.

La política R7B sigue siendo:
- PAPER únicamente;
- strict risk reduction;
- un POST máximo por attempt;
- durable UNKNOWN antes de I/O;
- ambigüedad => GET-only reconciliation;
- residual exposure => stop, no segundo SELL automático;
- LIVE bloqueado.

## Negative tests W80

Añadir como mínimo:
- assessment sin threshold policy exacta;
- assessment sin candidate policy exacta;
- policy hash tampering;
- strategy id/version drift;
- trial/tournament fingerprint mismatch;
- DEVELOPMENT/HOLDOUT campaign mismatch;
- evidence hash inexistente o alterado;
- gate set incompleto, duplicado o desconocido;
- PASS sin evidencia suficiente;
- FAIL/MISSING/BLOCKED sin reason code;
- duplicate identity con payload diferente;
- receipt hash alterado;
- timestamp/provenance inconsistente;
- read model aceptando receipt inválido;
- UI sintetizando PASS;
- read model intentando mutar SQLite;
- W80 entrando a POST/SAFE_ACTIONS;
- credenciales o broker network desde W80;
- `OrderIntent`, Safety, OMS, TradingPipeline o writer authority desde W80;
- PAPER candidate pasando a true;
- LIVE host/path;
- IA/model output intentando saltar Strategy Runtime/Safety/OMS.

## No-claims
- R6 broker truth != estrategia rentable;
- W78 simulated execution != fill Alpaca futuro;
- W79/W80 evidence qualification != PAPER candidate;
- Strategy Lab != capital authority;
- assessment persistido != Auto-Paper;
- fee-incomplete evidence != realized profitability;
- PAPER qualification != LIVE qualification.

**LIVE TRADING: BLOQUEADO.**
