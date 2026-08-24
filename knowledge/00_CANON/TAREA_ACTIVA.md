# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-24
Estado: **W84 BEHAVIORALLY CERTIFIED; CANONICAL DOCUMENTATION RECERTIFICATION IN PROGRESS; W85 PAPER CANDIDATE ADMISSION / PROBATION GATE NEXT.**

## Base certificada
R0–R5 continúan como tracks formalmente certificados del machine registry. R6 first real PAPER canary y W78–W84 tienen certificaciones técnicas independientes.

Behavioral exact W84 head:

`f1ed0f675224c515f74a099ddb0beeefd9c96629`

Evidencia:
- Dedicated W84 `32745537577`: **76/76 PASS**;
- Core `32745537856`: **3067/3067 PASS**;
- cobertura exacta `85.27030933795895%`;
- Knowledge `32745537825`: PASS;
- Research/R5/R6/R7/W78–W84 boundaries PASS;
- Debt Register PASS.

## W84 final trust boundary
La cadena que W85 puede consumir termina exclusivamente en:

`PromotionShadowForwardFinalVerification`.

Las etapas:
- `PromotionShadowForwardResolution` V2;
- `PromotionShadowForwardSourceVerification`;

son **intermedias**.

El finalizador W84:
1. vuelve a verificar la exact W83/W84 identity;
2. relee R5 Shadow/Forward durable truth;
3. verifica exact measurement receipts;
4. recalcula horizon, heads, duration, cumulative return y drawdown;
5. rechaza evidence rehash-valid que contradiga las fuentes;
6. usa reloj UTC interno del proceso, sin `verified_at` suministrado por caller;
7. deriva el capture time desde source truth;
8. exige el frozen `max_assessment_delay_seconds`;
9. conserva PAPER candidate FALSE, capital NONE y LIVE BLOCKED.

R5 sigue siendo la única autoridad de persistencia Shadow/Forward. W84 production no puede ejecutar `register_config`, `append_period`, `register_policy` ni `append_shadow_record`.

## Tarea siguiente — W85 PAPER Candidate Admission / Probation Gate
Objetivo:

`exact certified W79→W84 chain + frozen admission policy -> durable PAPER_CANDIDATE admission decision`

La entrada W84 obligatoria es un `PromotionShadowForwardFinalVerification` íntegro y exacto. W85 no puede reconstruir esa verdad a partir de hashes, strings, V2 receipts o source-verification intermedia.

### Diseño mínimo esperado
- `PaperCandidateAdmissionPolicy` frozen/hash-bound;
- `PaperCandidateAdmissionReceipt` durable, candidate/campaign/policy-specific;
- status explícitos `PASS/FAIL/BLOCKED/INCOMPLETE` o semántica equivalente fail-closed;
- temporal causality;
- replay/idempotency/conflict semantics;
- exact W79/W80/W81/W82/W83/W84 final provenance;
- candidate admission separado de runtime readiness;
- no broker/network/credentials/OMS/Safety/writer/OrderIntent authority;
- LIVE blocked.

### Negative tests obligatorios
- missing/tampered/unknown `PromotionShadowForwardFinalVerification`;
- intentar consumir `PromotionShadowForwardResolution` V2 directamente;
- intentar consumir `PromotionShadowForwardSourceVerification` directamente;
- W84 `source_truth_verified != True`;
- W84 `process_clock_freshness_verified != True`;
- mismatched candidate/trial/spec/runtime/measurement identity;
- stale o wrong admission policy;
- admission anterior a evidencia requerida;
- replay para otro candidate/campaign;
- duplicate conflicting admission;
- receipt/hash tamper;
- PAPER execution authority acuñada desde candidate receipt;
- imports/calls broker/network/OMS/Safety/writer;
- `OrderIntent` construction;
- LIVE distinto de BLOCKED.

## Gate de cierre W85
Mismo exact head:
- Dedicated W85 PASS;
- permanent W85 boundary PASS;
- inherited W84→W78 boundaries PASS;
- Research Authority PASS;
- Core Safety PASS;
- coverage >=85%;
- Debt Register PASS;
- Canonical Knowledge PASS.

## Deuda independiente
- `TD-R7D-001`: CLOSED;
- `TD-R7D-002`: CLOSED;
- `TD-R7D-003`: **OPEN P2** — remaining-quantity reservation after partial fills.

W85 no debe cerrar `TD-R7D-003` salvo trabajo explícito separado.

## R6/R7 recordatorio operacional
R6/R7 real PAPER authority sigue separada. Ninguna capa W78–W85 puede escribir al broker ni saltarse:

`PAPER Candidate Admission -> PAPER Runtime Readiness -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**
**PAPER CANDIDATE ACTUAL: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
