# ADR-0015 — W83 Execution Strategy-Version Binding

Fecha: 2026-08-24
Estado: **ACCEPTED / BEHAVIORAL IMPLEMENTATION CERTIFIED / `EXECUTION_STRATEGY_VERSION_UNBOUND` RESOLVED FOR THE EXACT BOUND CANDIDATE**

## Contexto
W82 cerró `FEE_ACCOUNTING_INCOMPLETE` para deterministic qualification accounting, pero dejó correctamente abierto `EXECUTION_STRATEGY_VERSION_UNBOUND`.

`strategy_id` y `strategy_version` son etiquetas necesarias, pero no bastan para demostrar que la estrategia seleccionada por Promotion Governance es exactamente el artefacto determinista que produce la semántica de un execution intent. Un mismo string podría sobrevivir a cambios en código, parámetros, defaults, dataset o runtime.

W83 debía cerrar únicamente esa discontinuidad de identidad sin modificar `OrderIntent`, sin crear broker write, sin usar credenciales y sin convertir evidencia científica en PAPER/LIVE authority.

## Decisión
W83 adopta dos receipts/verificadores separados y complementarios.

### 1. ExecutionStrategyBindingEvidence
`src/autotrade/strategy_execution_binding.py` prueba la continuidad:

`W79 selected candidate -> exact DEVELOPMENT TrialSpec -> exact StrategySpec artifact -> exact dataset/context -> deterministic ResearchSignal -> semantic MARKET projection -> existing W82-qualified OrderIntent fingerprint`.

El binding exige, entre otros:
- exact `StrategyPromotionPolicy` W79;
- selected trial id + fingerprint;
- selected strategy id + version;
- DEVELOPMENT phase;
- exact `TrialSpec.parameters`;
- exact `spec_hash == StrategySpec.canonical_hash` congelado en el trial;
- exact `MarketDataset.dataset_hash`;
- exact W82 resolution + `FeeProductEconomicsEvidence`;
- product / venue / quote-currency identity;
- deterministic signal para el contexto congelado;
- para el scope W83, MARKET projection exacta: symbol, sign->side, abs(delta)->quantity;
- full existing `OrderIntent` fingerprint ya certificado por W82.

El verificador consume un `OrderIntent` existente. No construye `OrderIntent`, no asigna idempotency keys y no posee autoridad de ejecución.

### 2. PromotionStrategyVersionResolution
`src/autotrade/promotion_strategy_version_binding.py` prueba que el runtime realmente cargado coincide con el `TrialSpec.code_version` preregistrado y sólo entonces puede retirar el blocker de versión.

La identidad runtime W83 no se reduce a un archivo. `W83_SAFE_DSL_RUNTIME_CODE_IDENTITY_V2` liga un source-set semántico mínimo:
- `autotrade.research.dsl`;
- `autotrade.research.strategy`;
- `autotrade.research.market`;
- implementación Python;
- versión exacta Python major.minor.patch.

El receipt conserva individualmente:
- `runtime_dsl_source_hash`;
- `runtime_strategy_source_hash`;
- `runtime_market_source_hash`;
- `runtime_python`;
- aggregate `loaded_runtime_code_hash`.

El aggregate debe ser exactamente igual a `selected_trial.code_version`. Así, un cambio transitivo en evaluación DSL, `StrategyContext.current_bar`, `Bar.ended_at` u otra semántica cubierta por esos módulos invalida la identidad aunque el string `strategy_version` no cambie.

## Freeze dual
W83 trata como identidades distintas pero necesarias:

1. **artifact/config identity**: `StrategySpec.canonical_hash` + parameters + dataset + trial fingerprint;
2. **runtime implementation identity**: source-set hash + exact Python runtime.

Ambas deben coincidir con el exact candidate chain. Ninguna sustituye a la otra.

## Scope deliberado
W83 certifica semantic projection determinista para **MARKET intents**. No inventa una regla de LIMIT-price derivation a partir de `ResearchSignal`. Un LIMIT path futuro requiere contrato explícito separado.

## Blocker semantics
Un W83 PASS puede retirar únicamente:

`EXECUTION_STRATEGY_VERSION_UNBOUND`.

Debe conservar:
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TD-R7D-003` OPEN;
- broker-authoritative fee proof FALSE;
- realized profitability unauthorized;
- PAPER candidate FALSE;
- runtime execution authority FALSE;
- external execution authority FALSE;
- capital authority NONE;
- LIVE BLOCKED.

W83 no modifica `knowledge/00_CANON/debt_register_r7d_auto_paper.json` porque `TD-R7D-003` es una deuda operacional distinta de partial-fill remaining-quantity reservation.

## Fail-closed / Negative tests
La suite W83 cubre, entre otros:
- same strategy id/version con artifact distinto;
- changed `spec_hash` / parameters / selected-trial fingerprint;
- dataset drift;
- runtime source-set drift;
- runtime Python drift;
- source semántico no localizable;
- non-immutable / mismatched `code_version`;
- product/venue/currency drift;
- W82 candidate/resolution mismatch;
- signal ausente o no determinista;
- symbol/side/quantity mismatch;
- unsupported LIMIT projection;
- intent fingerprint drift;
- temporal regression;
- evidence/resolution hash tampering;
- attempts to preclaim W83 authority from W82;
- attempts to remove Shadow/Forward como side effect;
- PAPER/runtime/external/capital/LIVE authority escalation.

## Permanent boundary
`scripts/check_w83_strategy_version_binding_boundary.py` está conectado tanto al workflow dedicado W83 como a Core Safety.

El boundary prohíbe en la superficie W83:
- broker modules/writers;
- network libraries/calls;
- Safety/OMS authority;
- SQLite mutation;
- credentials;
- `OrderIntent(` construction;
- PAPER/LIVE/capital escalation.

Además exige los markers estructurales del exact W79/W82 provenance, source-set runtime identity, blocker semantics y authority flags.

## Behavioral exact-head certification
Behavioral exact head:

`177517a29d677a34dc4a711b56b955bb5cf2cd51`

Dedicated W83 run `32688103622`:
- **25/25 W83 PASS**;
- compile PASS;
- W83 permanent boundary PASS;
- W82/W81/W80/W79/W78 boundaries PASS;
- Research Authority PASS.

Core Safety run `32688103642`:
- **2991/2991 PASS**;
- exact measured branch coverage `85.04640770024064%` >= 85%;
- `strategy_execution_binding.py`: 71%;
- `promotion_strategy_version_binding.py`: 94%;
- Contract Registry: 10 PASS;
- registry SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- inherited R5/R6/R7/W78-W83 boundaries PASS;
- Debt Register PASS: 60 items / 9 open / 8 blocking on uncertified tracks;
- Canonical Knowledge PASS.

Knowledge Contract run `32688103696`: **SUCCESS**.

El cierre canónico es un descendiente documentation-only de ese behavioral head. Su certificación exact-head final se registra en PR #55 después de ejecutar Dedicated W83 + Core Safety + Knowledge Contract, evitando un loop de documentación auto-invalidante.

## Consecuencia
`EXECUTION_STRATEGY_VERSION_UNBOUND` queda resuelto **sólo para el exact candidate/artifact/runtime/intent identity certificado por W83**. El resultado no es transferible a otra campaign, trial, dataset, spec, runtime source-set o intent fingerprint.

El siguiente blocker científico es `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` y corresponde a W84. W84 debe reutilizar esta misma identidad W83 y ligar las cadenas R5 `FrozenShadowConfig` / `StrategyShadowObservation` / `FrozenForwardPolicy` / `ForwardPeriodEvidence` al exact candidate sin selección ex post ni recalibración usando outcomes forward.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER CANDIDATE: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
