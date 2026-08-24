# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-24
Estado canónico: **R0–R5 CERTIFIED; R6 FIRST PAPER CANARY BROKER-TRUTH CLOSED; R7 PAPER OPERATIONS ACTIVE; W78/W79/W80/W81/W82/W83/W84 TECHNICALLY CERTIFIED; TD-R7D-001/002 CLOSED; W85 PAPER CANDIDATE ADMISSION / PROBATION GATE ACTIVE.**

## Tracks formales
El machine debt register principal mantiene R0–R5 como tracks certificados contiguos; **R5 sigue siendo el último track formalmente certificado** bajo ese registro.

R6 y W78–W84 tienen certificaciones técnicas específicas y no se reinterpretan como promoción automática del track registry.

## R6 / R7B
El first canary real PAPER alcanzó broker truth y recovery GET-only. PR #49 mantiene separada cualquier obligación operacional del lifecycle real PAPER close, con one-shot writer, Safety/OMS y reconciliation propios.

Nada en W78–W85 modifica ese writer ni convierte Research/Strategy Lab/admission evidence en broker authority.

## W78–W83 — cadena económica/promoción previa
- W78: deterministic no-network PAPER execution qualification.
- W79: Strategy Promotion Governance + Strategy Lab GET-only; thresholds preregistrados y candidate frozen antes de FINAL_HOLDOUT.
- W80: durable append-only/hash-chained promotion assessments.
- W81: non-fee Research -> W78 execution-cost continuity; `TD-R7D-001` CLOSED.
- W82: fee-complete deterministic qualification accounting, product-aware economics y conservative documented Alpaca crypto fee floor; `TD-R7D-002` CLOSED.
- W83: exact TrialSpec/StrategySpec/dataset/W82-intent semantics + exact safe-DSL runtime identity; `EXECUTION_STRATEGY_VERSION_UNBOUND` resuelto para la exact identidad ligada.

W82 conserva broker-authoritative realized fee proof FALSE y realized profitability unauthorized.

## W84 — Shadow/Forward Promotion Binding — CERTIFIED
PR #56 / `work/w84-shadow-forward-promotion-binding`, apilado exactamente sobre W83 head:

`0d177a1cfb16cffbb1266ee07865db5f77f1fe50`

Behavioral exact head certificado:

`abf25f4b699f145a629955efe73a798966f29845`

ADR canónico: `knowledge/30_DECISIONES/ADR-0016-w84-shadow-forward-promotion-binding.md`.

### Problema que cerró W84
Una R5 Shadow/Forward hash-chain podía ser internamente íntegra pero no demostrar que cada retorno perteneciera a la exact versión/artifact/runtime W83. `StrategyShadowObservation.source_fingerprint` podía ser un hash opaco suministrado por el caller.

W84 reemplaza esa ambigüedad por la continuidad:

`exact W83 candidate/artifact/runtime -> frozen W84 measurement plan/policy -> deterministic prefix-only measurement receipts -> exact R5 Shadow observations -> exact R5 Forward tail -> W84 promotion resolution`.

### Measurement runtime identity
`src/autotrade/forward_shadow_measurement.py` agrega a la identidad W83:
- `research/backtest.py`;
- `research/costs.py`;
- `domain.py`;
- exact Python implementation/patch.

El aggregate `measurement_runtime_hash` debe permanecer estable. Un cambio en safe DSL, BacktestEngine, costo, domain semantics o Python patch invalida el binding.

### Pre-outcome plan
`ForwardMeasurementPlan` congela antes de forward outcomes:
- exact W83 resolution/binding/candidate/spec/runtime;
- exact BacktestConfig + initial cash;
- exact history dataset hash;
- source/symbol/venue/quote/timeframe;
- history bars;
- `planned_at` y `forward_activated_at`;
- no-authority flags.

El history dataset debe terminar exactamente en `planned_at`; activation es posterior y timeframe-aligned.

### Prefix-only receipts / no look-ahead
Cada qualification period ejecuta el `BacktestEngine` existente sobre un prefijo que termina en ese período. El receipt k no puede depender de períodos k+1…N.

Cada receipt conserva candidate/runtime/config/prefix hashes, equity before/after, return, period bounds y `previous_measurement_hash`.

### Exact Shadow observation binding
Cada R5 `StrategyShadowObservation.source_fingerprint` debe ser exactamente el `measurement_hash` del receipt correspondiente, además de coincidir strategy/timestamps/return.

Un retorno fabricado, opaque source hash, misma strategy string con otro runtime o receipt de otra policy falla cerrado.

### Candidate-only Shadow / Forward recommitment
La promotion Shadow config exige exactamente:

`{selected_strategy_id: Decimal("1")}`.

Además:
- `FrozenShadowConfig.source_config_hash = W84 policy_hash`;
- `FrozenForwardPolicy.frozen_parameters_hash = W84 policy_hash`;
- `FrozenForwardPolicy.source_code_hash = W84 measurement_runtime_hash`;
- exact candidate Shadow fingerprint.

### Fixed horizon / complete tail / freshness
Qualification usa un horizonte exacto N:
- `<N`: PENDING;
- `N`: threshold evaluation;
- `>N`: FAIL / `FORWARD_WINDOW_OVERRUN`.

Todo Shadow tail eligible debe aparecer en Forward en orden exacto. No se puede omitir un período adverso.

La policy congela capture/assessment lag budgets cuya suma debe ser menor que un market period y exige:

`measurement_data_cutoff_at <= measurement_captured_at <= assessed_at`.

Esto reduce optional stopping y decisiones tardías con outcomes posteriores ya conocidos.

### Candidate resolution
`PromotionShadowForwardResolution` sólo puede retirar:

`SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

Mantiene:
- `strategy_version_execution_bound = True`;
- `shadow_forward_promotion_bound = True`;
- PAPER candidate FALSE;
- runtime execution FALSE;
- external execution FALSE;
- capital authority NONE;
- LIVE BLOCKED.

### Permanent authority boundary
`scripts/check_w84_shadow_forward_promotion_boundary.py` queda cableado en:
- dedicated W84 workflow;
- Core Safety.

Prohíbe broker/network/OMS/Safety/connectivity/paper-close/direct-SQLite authority, credentials/endpoints, `OrderIntent(` construction y authority escalation. También exige estructuralmente prefix-only measurement, exact observation fingerprint, complete tail, fixed horizon, freshness y blocker semantics.

### Behavioral exact-head evidence
Dedicated W84 run `32740076750`:
- **44/44 W84 PASS**;
- compile PASS;
- W84 permanent boundary PASS;
- W83/W82/W81/W80/W79/W78 boundaries PASS;
- Research Authority PASS.

Core Safety run `32740076693`:
- **3035/3035 PASS**;
- exact coverage `85.20576561520785%` >=85%;
- `forward_shadow_measurement.py`: 93%;
- `promotion_shadow_forward_binding.py`: 89%;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- inherited R5/R6/R7/W78-W84 boundaries PASS;
- Debt Register PASS: 60 items / 9 open / 8 blocking on uncertified tracks;
- Canonical Knowledge PASS.

Knowledge Contract run `32740076824`: PASS.

## Debt R7D
- `TD-R7D-001` **CLOSED** — non-fee execution-cost continuity;
- `TD-R7D-002` **CLOSED** — fee-complete deterministic qualification accounting;
- `TD-R7D-003` **OPEN P2** — safe remaining-quantity reservation after partial fills.

W84 no modifica ni cierra `TD-R7D-003`.

## Blockers científicos
Resueltos para las identidades exactas certificadas:
- `EXECUTION_STRATEGY_VERSION_UNBOUND` — W83;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` — W84.

Esto **no significa** PAPER candidate admission.

## W85 — PAPER Candidate Admission / Probation Gate — ACTIVE
La cadena W79→W84 ya puede demostrar governance, durable assessment, economics, exact runtime identity y exact forward evidence provenance. El siguiente problema es separar explícitamente **qualification** de **admission**.

W85 debe decidir:

`exact certified evidence chain + frozen admission policy -> PAPER_CANDIDATE admission receipt`

sin crear `PAPER_EXECUTION_AUTHORIZED`.

La admission policy/receipt debe ser durable, hash-bound, candidate-specific, replay-safe y temporalmente causal. Debe distinguir PASS/FAIL/BLOCKED/INCOMPLETE y no llamar broker, OMS, Safety ni writers.

Cualquier runtime PAPER readiness, freshness, Portfolio, Capital Safety, OMS y writer permanece en capas posteriores.

## Authority
Research/W78–W85 científico/admission no tiene camino directo al writer.

Cualquier futura automatización continúa obligada a:

`PAPER Candidate Admission -> PAPER Runtime Readiness -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`.

IA/model output no puede saltarse esa cadena.

## No-claims
- deterministic qualification != future broker fill;
- W82 deterministic fee completeness != broker-observed realized fee;
- W83 strategy binding != forward robustness;
- W84 forward binding != PAPER candidate admission;
- PAPER candidate != PAPER execution authorization;
- PAPER qualification != LIVE qualification;
- scientific/admission evidence != capital authority.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**
**PAPER CANDIDATE ACTUAL: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
