# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-24
Estado: **R0–R5 formalmente certified; R6 first real PAPER canary broker-truth closed; W78/W79/W80/W81/W82/W83 technically certified; TD-R7D-001/002 CLOSED; W84 Shadow/Forward Promotion Binding ACTIVE.**

## Fuente de verdad al retomar
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`;
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`;
3. `knowledge/00_CANON/TAREA_ACTIVA.md`;
4. `knowledge/00_CANON/debt_register_r7d_auto_paper.json`;
5. `knowledge/30_DECISIONES/ADR-0015-w83-execution-strategy-version-binding.md`;
6. este handoff.

R5 sigue siendo el último track formal certificado del machine debt register principal. R6 y W78–W83 son hitos técnicos independientes.

## Stack activo
- PR #49 — R7 real PAPER close / lifecycle operacional independiente;
- PR #50 — W78 execution qualification;
- PR #51 — W79 promotion governance + Strategy Lab;
- PR #52 — W80 durable promotion assessment;
- PR #53 — W81 execution-cost continuity;
- PR #54 — W82 fee-complete deterministic execution accounting;
- PR #55 — W83 execution strategy-version binding, DRAFT apilado sobre W82.

No fusionar el stack fuera de orden.

## W83 — resultado
W83 cierra `EXECUTION_STRATEGY_VERSION_UNBOUND` **sólo para el exact candidate/artifact/runtime/intent identity certificado**, mediante dos capas:

1. `ExecutionStrategyBindingEvidence`: W79 selected trial/policy + StrategySpec hash + parameters + dataset + W82 product/venue/intent + deterministic MARKET signal projection;
2. `PromotionStrategyVersionResolution`: revalida toda la identidad y exige que `TrialSpec.code_version` coincida con el runtime source-set cargado.

### Runtime identity W83 V2
La attestation cubre y expone hashes individuales de:
- `research/dsl.py`;
- `research/strategy.py`;
- `research/market.py`;
- implementación y versión Python major.minor.patch exacta.

Esto evita reutilizar la qualification si cambia código transitivo relevante manteniendo el mismo `strategy_version` string.

### Behavioral exact-head
`177517a29d677a34dc4a711b56b955bb5cf2cd51`

Dedicated W83 run `32688103622`:
- **25/25 PASS**;
- W83 boundary PASS;
- W82/W81/W80/W79/W78/Research boundaries PASS.

Core Safety run `32688103642`:
- **2991/2991 PASS**;
- exact coverage `85.04640770024064%`;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- inherited boundaries through W83 PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

Knowledge Contract `32688103696`: PASS.

## Exact-head discipline PR #55
El commit canónico W83 es documentation-only y debe recertificar Dedicated W83 + Core Safety + Knowledge Contract sobre su **mismo exact final head**.

Después de esa recertificación:
- actualizar PR #55 con final head + run IDs;
- mantener PR #55 DRAFT por ser parte del stack;
- no volver a modificar canon sólo para copiar ese nuevo SHA, evitando el loop auto-invalidante.

## Debt separado
- `TD-R7D-001`: CLOSED;
- `TD-R7D-002`: CLOSED;
- `TD-R7D-003`: **OPEN P2** — remaining-quantity reservation after partial fills.

W83 no cierra `TD-R7D-003`.

## Blocker científico restante
`SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

## W84 — retomar aquí
No construir un shadow engine nuevo. R5 ya ofrece las primitives persistidas y hash-protected:
- `FrozenShadowConfig`;
- `StrategyShadowObservation`;
- `ShadowPeriodRecord`;
- `SQLitePortfolioShadowRegistry`;
- `FrozenForwardPolicy`;
- `ForwardPeriodEvidence`;
- `SQLiteForwardEvidenceRegistry`.

La tarea W84 es crear un sidecar candidate-bound que pruebe:

`exact W83 candidate/runtime identity == frozen Shadow identity == frozen Forward policy identity == verified post-activation Forward evidence chain`.

### Discovery prioritario W84
Inspeccionar primero:
- `src/autotrade/research/shadow.py`;
- `src/autotrade/research/forward.py`;
- tests R5 shadow/forward;
- `StrategyPromotionPolicy` y W79 selected-candidate semantics;
- W83 `ExecutionStrategyBindingEvidence` y `PromotionStrategyVersionResolution`;
- dónde se definen hoy `source_config_hash`, `frozen_parameters_hash`, `source_code_hash` y activation timestamps;
- si existe un forward gate/threshold preregistration separado o debe añadirse como sidecar sin mutar R5 registries.

### W84 hard requirements
- consume exact W83 resolution, no sólo strings;
- Shadow config debe incluir inequívocamente al selected candidate;
- `source_code_hash`/`frozen_parameters_hash` deben quedar ligados a identidad W83 o a derivación canónica demostrable;
- activation/freeze debe preceder a forward evidence aceptada;
- no recalibration/tuning/selection usando forward outcomes;
- verified contiguous shadow/forward chains;
- immutable candidate-bound resolution hash;
- sólo `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` removible.

### Negative tests W84
Cubrir al menos:
- W83 hash/candidate/runtime mismatch;
- wrong Shadow config/candidate weight;
- forward policy de otra config/campaign;
- code/parameter hash drift;
- policy/config frozen ex post;
- forward evidence pre-activation;
- chain gap/reorder/tail deletion/tamper;
- threshold selected after outcomes;
- receipt reuse;
- empty/insufficient evidence cuando corresponda;
- side-effect removal de otros guards;
- PAPER candidate true;
- broker/network/writer/Safety/OMS authority;
- LIVE != BLOCKED.

### Gate de cierre W84
Mismo exact head:
- Dedicated W84 PASS;
- W84 permanent boundary PASS;
- W83→W78 inherited boundaries PASS;
- Research Authority PASS;
- Core Safety PASS;
- coverage >=85%;
- Debt Register PASS;
- Canonical Knowledge PASS.

## Regla de producto
La cadena futura permanece:

`Research -> Promotion Evidence -> Durable Assessment -> Economic Qualification -> Strategy Version Binding -> Shadow/Forward Binding -> PAPER Candidate Decision -> Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

`EVIDENCE_QUALIFIED != PAPER_CANDIDATE` continúa siendo obligatorio. W84 no puede saltarse la decisión posterior de PAPER candidate.

## Estado de autoridad
- PAPER candidate: FALSE;
- W78–W84 capital authority: NONE;
- broker write desde capas científicas: NO;
- credentials en Strategy Lab: NO;
- broker-authoritative fee proof por W82: NO;
- realized profitability claim: NO;
- LIVE: BLOCKED.

**LIVE TRADING: BLOQUEADO.**
