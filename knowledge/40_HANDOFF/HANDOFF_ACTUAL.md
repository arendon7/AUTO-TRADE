# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-24
Estado: **R0–R5 formalmente certified; R6 first real PAPER canary broker-truth closed; W78/W79/W80/W81/W82/W83/W84 technically certified; TD-R7D-001/002 CLOSED; W85 PAPER Candidate Admission / Probation Gate ACTIVE.**

## Fuente de verdad al retomar
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`;
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`;
3. `knowledge/00_CANON/TAREA_ACTIVA.md`;
4. `knowledge/00_CANON/debt_register_r7d_auto_paper.json`;
5. `knowledge/30_DECISIONES/ADR-0016-w84-shadow-forward-promotion-binding.md`;
6. este handoff.

R5 sigue siendo el último track formal certificado del machine debt register principal. R6 y W78–W84 son hitos técnicos independientes.

## Stack activo
- PR #49 — R7 real PAPER close / lifecycle operacional independiente;
- PR #50 — W78 execution qualification;
- PR #51 — W79 promotion governance + Strategy Lab;
- PR #52 — W80 durable promotion assessment;
- PR #53 — W81 execution-cost continuity;
- PR #54 — W82 fee-complete deterministic execution accounting;
- PR #55 — W83 execution strategy-version binding;
- PR #56 — W84 Shadow/Forward promotion binding, DRAFT apilado exactamente sobre W83.

No fusionar el stack fuera de orden. No mezclar el cierre operacional PR #49 con W84/W85 científico.

## W84 — resultado
W84 resuelve `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` **sólo para la exact W83 candidate/artifact/runtime + W84 measurement/policy + R5 Shadow/Forward identity certificada**.

La arquitectura final V2 es:

`exact W83 candidate/artifact/runtime -> frozen W84 measurement plan/policy -> deterministic prefix-only measurement receipts -> exact R5 Shadow observations -> exact R5 Forward tail -> W84 promotion resolution`.

### Brecha que W84 cerró
No asumir que una R5 hash-chain íntegra prueba candidate provenance. Antes de W84 V2, `StrategyShadowObservation.source_fingerprint` podía ser un SHA-256 opaco suministrado por el caller. El mismo `strategy_id` podía teóricamente ocultar otra versión/runtime.

**No regresar a ese diseño.**

Cada Shadow observation usada para promotion debe corresponder a un `ForwardShadowMeasurementReceipt` determinista cuyo `measurement_hash` sea exactamente el `source_fingerprint`.

### Measurement provenance
`src/autotrade/forward_shadow_measurement.py`:
- exact W83 resolution/binding/candidate/spec;
- W83 safe-DSL runtime identity;
- `backtest.py + costs.py + domain.py + exact Python patch` en el measurement runtime identity;
- frozen BacktestConfig + initial cash;
- frozen pre-outcome history dataset;
- plan freeze antes de forward activation;
- prefix-only BacktestEngine recomputation por período;
- hash-linked measurement receipts;
- exact per-observation source fingerprint verification;
- no broker/network/OMS/Safety authority.

### Shadow/Forward binding
`src/autotrade/promotion_shadow_forward_binding.py` exige:
- candidate-only Shadow `{selected_strategy_id: Decimal("1")}`;
- Shadow source config hash = W84 policy hash;
- Forward frozen parameters hash = W84 policy hash;
- Forward source code hash = W84 measurement runtime hash;
- complete observed eligible Shadow tail == Forward tail;
- fixed exact horizon N;
- `<N=PENDING`, `N=evaluate`, `>N=FAIL/OVERRUN`;
- capture lag + assessment delay < market period;
- data cutoff <= capture <= assessment;
- only Shadow/Forward blocker removable.

### Behavioral exact-head
`abf25f4b699f145a629955efe73a798966f29845`

Dedicated W84 run `32740076750`:
- **44/44 PASS**;
- W84 boundary PASS;
- W83/W82/W81/W80/W79/W78/Research boundaries PASS.

Core Safety run `32740076693`:
- **3035/3035 PASS**;
- exact coverage `85.20576561520785%`;
- `forward_shadow_measurement.py` 93%;
- `promotion_shadow_forward_binding.py` 89%;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- inherited boundaries through W84 PASS;
- Debt Register PASS: 60 items / 9 open / 8 blocking on uncertified tracks;
- Canonical Knowledge PASS.

Knowledge Contract `32740076824`: PASS.

## Permanent W84 authority
Boundary:
`scripts/check_w84_shadow_forward_promotion_boundary.py`

Workflow:
`.github/workflows/w84-shadow-forward-promotion.yml`

También está cableado permanentemente en Core Safety.

No debilitar los siguientes invariantes:
- no broker/network/OMS/Safety/connectivity/paper-close/direct-SQLite authority;
- no credentials/endpoints Alpaca;
- no `OrderIntent(` construction;
- PAPER candidate false en W84;
- capital NONE;
- LIVE blocked;
- per-observation measurement provenance;
- prefix-only/no-lookahead;
- complete tail;
- fixed horizon;
- sub-period freshness budgets;
- exact blocker semantics.

## Debt separado
- `TD-R7D-001`: CLOSED;
- `TD-R7D-002`: CLOSED;
- `TD-R7D-003`: **OPEN P2** — remaining-quantity reservation after partial fills.

W84 no cierra `TD-R7D-003`.

## W85 — retomar aquí
W85 no debe ejecutar una estrategia. Debe construir la decisión explícita que separa qualification de admission.

Objetivo:

`exact W79→W84 certified evidence chain + frozen admission policy -> durable PAPER_CANDIDATE admission receipt`.

### Principio central

`EVIDENCE_QUALIFIED != PAPER_CANDIDATE`

y además:

`PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED`.

Un candidate admitido sólo sería elegible para un proceso posterior de PAPER runtime readiness. No recibe POST authority, Capital Safety decision ni OMS handoff por el hecho de ser candidate.

### Discovery prioritario W85
Inspeccionar primero:
- W79 `StrategyPromotionPolicy` y threshold policy;
- W80 durable assessment journal/read model;
- W81 `PromotionCostContinuityResolution`;
- W82 `PromotionFeeAccountingResolution`;
- W83 `PromotionStrategyVersionResolution`;
- W84 `PromotionShadowForwardResolution` y exact evidence/policy/measurement hashes;
- todos los lugares donde hoy aparece `paper_candidate_authorized`;
- si existe ya algún concepto de probation/admission/readiness que pueda reutilizarse sin crear authority duplicada.

### W85 hard requirements
- consumir receipts/resolutions exactos, no strings sueltos;
- definir `PaperCandidateAdmissionPolicy` o equivalente, frozen/hash-bound;
- definir durable `PaperCandidateAdmissionReceipt` o equivalente;
- candidate/campaign/policy-specific identity;
- causalidad temporal explícita;
- deterministic replay/idempotency/conflict semantics;
- PASS/FAIL/BLOCKED/INCOMPLETE explícitos;
- distinguir candidate admission de runtime readiness;
- no broker/network/writer/Safety/OMS/OrderIntent authority;
- LIVE blocked.

### Negative tests W85
Cubrir al menos:
- missing/unknown/tampered W84 resolution;
- W84 evidence non-PASS;
- mismatched candidate/spec/runtime/measurement chain;
- stale/wrong admission policy;
- admission anterior a evidencia necesaria;
- replay para otra campaign/candidate;
- duplicate conflicting admission;
- hash tamper;
- PAPER execution authority minted from candidate receipt;
- broker/network/writer/Safety/OMS import/call;
- `OrderIntent` construction;
- LIVE != BLOCKED.

### Gate de cierre W85
Mismo exact head:
- Dedicated W85 PASS;
- permanent W85 admission boundary PASS;
- W84→W78 inherited boundaries PASS;
- Research Authority PASS;
- Core Safety PASS;
- coverage >=85%;
- Debt Register PASS;
- Canonical Knowledge PASS.

## Regla de producto
La cadena futura permanece:

`Research -> Promotion Evidence -> Durable Assessment -> Economic Qualification -> Strategy Version Binding -> Shadow/Forward Binding -> PAPER Candidate Admission -> PAPER Runtime Readiness -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

IA/model output no puede saltarse esa cadena.

## Estado de autoridad
- PAPER candidate actual: FALSE;
- W78–W85 capital authority: NONE;
- broker write desde capas científicas/admission: NO;
- credentials en Strategy Lab/admission: NO;
- broker-authoritative realized fee proof por W82: NO;
- realized profitability claim: NO;
- LIVE: BLOCKED.

**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**
**LIVE TRADING: BLOQUEADO.**
