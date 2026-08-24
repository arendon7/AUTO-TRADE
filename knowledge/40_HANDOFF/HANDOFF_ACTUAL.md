# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-24
Estado: **R0–R5 formalmente certified; R6 broker-truth cerrado; W78–W84 técnicamente certificados; W84 final trust boundary endurecido; W85 PAPER Candidate Admission / Probation Gate NEXT.**

## Fuente de verdad al retomar
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`;
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`;
3. `knowledge/00_CANON/TAREA_ACTIVA.md`;
4. `knowledge/00_CANON/debt_register_r7d_auto_paper.json`;
5. `knowledge/30_DECISIONES/ADR-0016-w84-shadow-forward-promotion-binding.md`;
6. este handoff.

R5 sigue siendo el último track formal certificado del machine registry principal. R6 y W78–W84 son hitos técnicos independientes.

## Stack
- PR #49 — R7 real PAPER close / lifecycle operacional separado;
- PR #50 — W78 execution qualification;
- PR #51 — W79 promotion governance;
- PR #52 — W80 durable assessment;
- PR #53 — W81 execution-cost continuity;
- PR #54 — W82 fee-complete accounting;
- PR #55 — W83 execution strategy-version binding;
- PR #56 — W84 Shadow/Forward promotion binding, DRAFT apilado sobre W83.

No fusionar fuera de orden. No mezclar PR #49 con la cadena científica W78–W85.

## W84 — resultado definitivo
Behavioral exact head:

`f1ed0f675224c515f74a099ddb0beeefd9c96629`

W84 resuelve `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` únicamente para la exact W83 candidate/artifact/runtime + exact W84 plan/policy/measurement + exact R5 Shadow/Forward truth, y además exige una finalización dentro del frozen temporal budget usando el reloj del proceso.

### Cadena final
`exact W83 candidate/artifact/runtime`
`-> ForwardMeasurementPlan`
`-> prefix-only ForwardShadowMeasurementReceipt chain`
`-> exact R5 Shadow`
`-> exact R5 Forward`
`-> PromotionShadowForwardResolution [INTERMEDIATE]`
`-> PromotionShadowForwardSourceVerification [INTERMEDIATE]`
`-> PromotionShadowForwardFinalVerification [CANONICAL]`.

**W85 sólo puede consumir `PromotionShadowForwardFinalVerification`.**

### Qué cerró la source verification
`src/autotrade/promotion_shadow_forward_source_verification.py` no confía en un PASS receipt porque esté correctamente rehasheado. Relee los registries R5 y measurement receipts, valida exact identity/heads/horizon y recalcula:
- duration;
- cumulative return;
- peak-to-trough drawdown;
- capture lag.

Un evidence rehash-valid que mienta sobre métricas, heads, windows o proof flags falla cerrado. También hay relectura final de R5 control heads para TOCTOU.

### Qué cerró la final verification
R5 no contiene un timestamp independiente de append. Por eso `assessed_at` no puede funcionar como temporal authority final si el caller puede reconstruir/rehashear el objeto.

`src/autotrade/promotion_shadow_forward_final_verification.py`:
- no acepta caller `verified_at`;
- obtiene `_now_utc()` internamente;
- ejecuta nuevamente source verification;
- deriva `measurement_capture_at` desde source truth;
- calcula el actual `decision_delay_seconds`;
- exige `decision_delay <= policy.max_assessment_delay_seconds`;
- produce `source_truth_verified=True` y `process_clock_freshness_verified=True`.

### Permanent boundary
`scripts/check_w84_shadow_forward_promotion_boundary.py` prohíbe:
- broker/network/OMS/Safety/connectivity/paper-close/direct-SQLite authority;
- Alpaca credentials/endpoints;
- `OrderIntent(` construction;
- R5 mutation calls (`register_config`, `append_period`, `register_policy`, `append_shadow_record`);
- uso downstream del resolver V2 o source verifier como final authority;
- PAPER/execution/capital/LIVE escalation.

## Certificación W84
Dedicated W84 `32745537577`: **SUCCESS**.
- **76/76 W84 PASS**;
- permanent W84 boundary PASS;
- W83/W82/W81/W80/W79/W78/Research PASS.

Core Safety `32745537856`: **SUCCESS**.
- **3067/3067 PASS**;
- exact coverage `85.27030933795895%`;
- measurement 93%; binding V2 89%; source verifier 89%; final verifier 95%;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- R5/R6/R7/W78–W84 boundaries PASS;
- Debt Register PASS: 60 items / 9 open / 8 blocking on uncertified tracks;
- Canonical Knowledge PASS.

Knowledge `32745537825`: **SUCCESS**.

## W85 — retomar aquí
Objetivo:

`exact W79→W84 final verified chain + frozen admission policy -> durable PAPER_CANDIDATE admission decision`.

Principios:

`EVIDENCE_QUALIFIED != PAPER_CANDIDATE`

y

`PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED`.

### Discovery W85
Inspeccionar:
- W79 selected candidate/policy;
- W80 durable assessment;
- W81 continuity resolution;
- W82 fee resolution;
- W83 strategy-version resolution;
- **W84 `PromotionShadowForwardFinalVerification`**;
- lugares donde aparece `paper_candidate_authorized`;
- patrones existentes de durable policy/receipt/idempotency que puedan reutilizarse sin crear execution authority.

### W85 hard requirements
- entrada typed exacta, no hashes sueltos;
- frozen/hash-bound admission policy;
- durable admission receipt;
- candidate/campaign/policy-specific identity;
- explicit status semantics;
- temporal causality;
- replay/idempotency/conflict protection;
- candidate admission separado de PAPER runtime readiness;
- no broker/network/credentials/writer/Safety/OMS/OrderIntent authority;
- LIVE blocked.

### Negative tests W85
- missing/tampered final W84 receipt;
- consumo directo de V2/source intermediate receipts;
- false source/process-clock verification flags;
- candidate/spec/runtime/measurement mismatch;
- stale policy;
- admission temporalmente imposible;
- cross-campaign replay;
- conflicting duplicate admission;
- hash tamper;
- PAPER execution minted from admission;
- prohibited imports/calls;
- LIVE escalation.

## Debt
- `TD-R7D-001`: CLOSED;
- `TD-R7D-002`: CLOSED;
- `TD-R7D-003`: **OPEN P2**.

## Regla de producto
La cadena futura permanece:

`Research -> Promotion Evidence -> Durable Assessment -> Economic Qualification -> Strategy Version Binding -> Shadow/Forward Final Verification -> PAPER Candidate Admission -> PAPER Runtime Readiness -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`.

IA/model output no puede saltarse la cadena.

## Authority
- PAPER candidate actual: FALSE;
- W78–W84 capital authority: NONE;
- W85 candidate admission no implica capital ni execution authority;
- broker write desde Research/promotion/admission: NO;
- broker-authoritative realized fee proof: FALSE;
- realized-profitability claim: NO;
- LIVE: BLOCKED.

**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**
**LIVE TRADING: BLOQUEADO.**
