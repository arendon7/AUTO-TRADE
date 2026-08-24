# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-24
Estado canónico: **R0–R5 CERTIFIED; R6 FIRST PAPER CANARY BROKER-TRUTH CLOSED; R7 PAPER OPERATIONS ACTIVE; W78/W79/W80/W81/W82/W83 TECHNICALLY CERTIFIED; TD-R7D-001/002 CLOSED; W84 SHADOW/FORWARD PROMOTION BINDING ACTIVE.**

## Tracks formales
El machine debt register principal mantiene R0–R5 como tracks certificados contiguos; **R5 sigue siendo el último track formalmente certificado** bajo ese registro.

R6 y W78–W83 tienen certificaciones técnicas específicas y no se reinterpretan como promoción automática del track registry.

## R6 / R7B
El first canary real PAPER alcanzó broker truth y recovery GET-only. PR #49 mantiene separada cualquier obligación operacional del lifecycle real PAPER close, con one-shot writer, Safety/OMS y reconciliation propios.

Nada en W78–W84 modifica ese writer ni convierte Research/Strategy Lab en broker authority.

## W78–W82 — cadena económica/promoción previa
- W78: deterministic no-network PAPER execution qualification.
- W79: Strategy Promotion Governance + Strategy Lab GET-only; thresholds preregistrados y candidate frozen antes de FINAL_HOLDOUT.
- W80: durable append-only/hash-chained promotion assessments.
- W81: non-fee Research -> W78 execution-cost continuity; `TD-R7D-001` CLOSED.
- W82: fee-complete deterministic qualification accounting, product-aware economics y conservative documented Alpaca crypto fee floor; `TD-R7D-002` CLOSED.

W82 conserva broker-authoritative fee proof FALSE y realized profitability unauthorized.

## W83 — Execution Strategy-Version Binding — CERTIFIED
PR #55 / `work/w83-execution-strategy-version-binding`, apilado exactamente sobre W82.

Behavioral exact head certificado:

`177517a29d677a34dc4a711b56b955bb5cf2cd51`

ADR canónico: `knowledge/30_DECISIONES/ADR-0015-w83-execution-strategy-version-binding.md`.

### Qué demuestra W83
W83 demuestra, para la identidad exacta certificada:

`selected W79 candidate == frozen TrialSpec/StrategySpec artifact identity == loaded deterministic safe-DSL runtime identity == semantic origin of the existing W82-qualified MARKET OrderIntent fingerprint`.

No se basa sólo en `strategy_id` / `strategy_version`.

### Artifact/config binding
`src/autotrade/strategy_execution_binding.py` liga:
- exact W79 promotion policy;
- selected DEVELOPMENT trial id/fingerprint;
- exact strategy id/version;
- exact parameters;
- `spec_hash == StrategySpec.canonical_hash`;
- exact dataset hash/context;
- exact W82 product/venue/quote-currency provenance;
- deterministic DSL signal;
- MARKET symbol/side/quantity projection;
- exact existing full `OrderIntent` fingerprint.

El módulo no construye `OrderIntent` y no posee execution authority.

### Runtime implementation binding
`src/autotrade/promotion_strategy_version_binding.py` V2 calcula una identidad runtime sobre:
- `research/dsl.py`;
- `research/strategy.py`;
- `research/market.py`;
- implementación Python;
- Python major.minor.patch exacto.

Los SHA-256 individuales quedan en el receipt y el hash agregado debe ser exactamente igual al `TrialSpec.code_version` preregistrado.

Esta regla evita reutilizar una calificación si cambia código transitivo que afecta la semántica del signal/context/bar aunque el version string permanezca igual.

### Candidate resolution
`PromotionStrategyVersionResolution` sólo puede retirar:

`EXECUTION_STRATEGY_VERSION_UNBOUND`.

Mantiene obligatoriamente:
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- PAPER candidate FALSE;
- runtime execution FALSE;
- external execution FALSE;
- capital authority NONE;
- LIVE BLOCKED.

### Scope
W83 certifica deterministic **MARKET-intent semantic projection**. No inventa LIMIT-price derivation.

### Behavioral exact-head evidence
Dedicated W83 run `32688103622`:
- **25/25 W83 PASS**;
- W83 boundary PASS;
- W82/W81/W80/W79/W78/Research boundaries PASS.

Core Safety run `32688103642`:
- **2991/2991 PASS**;
- exact coverage `85.04640770024064%` >=85%;
- `strategy_execution_binding.py` 71%;
- `promotion_strategy_version_binding.py` 94%;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- inherited boundaries through W83 PASS;
- Debt Register PASS: 60 items / 9 open / 8 blocking on uncertified tracks;
- Canonical Knowledge PASS.

Knowledge Contract run `32688103696`: PASS.

## Debt R7D
- `TD-R7D-001` **CLOSED** — non-fee execution-cost continuity;
- `TD-R7D-002` **CLOSED** — fee-complete deterministic qualification accounting;
- `TD-R7D-003` **OPEN P2** — safe remaining-quantity reservation after partial fills.

W83 no modifica ni cierra `TD-R7D-003`.

## Blockers científicos
Resuelto para el exact W83 candidate:
- `EXECUTION_STRATEGY_VERSION_UNBOUND`.

Aún abierto:
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

## W84 — Shadow/Forward Promotion Binding — ACTIVE
R5 ya contiene `FrozenShadowConfig`, `StrategyShadowObservation`, `ShadowPeriodRecord`, `FrozenForwardPolicy`, `ForwardPeriodEvidence` y registries append-only/hash-protected.

W84 debe reutilizar esas cadenas y demostrar:

`exact W83 candidate/runtime identity == frozen Shadow identity == frozen Forward policy identity == verified post-activation Forward evidence chain`.

La configuración/identidad/thresholds relevantes deben congelarse antes de los outcomes forward usados como evidencia. No se permite selección ex post ni recalibración usando esos outcomes.

Un eventual W84 resolution sólo podrá retirar `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` para la identidad exacta certificada. Incluso después, `EVIDENCE_QUALIFIED != PAPER_CANDIDATE` y una decisión posterior separada deberá gobernar cualquier PAPER candidate status.

## Authority
Research/W78–W84 científico no tiene camino directo al writer.

Cualquier futura automatización continúa obligada a:

`Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

IA/model output no puede saltarse esa cadena.

## No-claims
- deterministic qualification != future broker fill;
- W82 fee completeness != broker-observed realized fee;
- W83 strategy binding != forward robustness;
- evidence qualification != PAPER candidate;
- PAPER qualification != LIVE qualification;
- scientific evidence != capital authority.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER CANDIDATE: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
