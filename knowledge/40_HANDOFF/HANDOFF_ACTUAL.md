# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-24
Estado: **R0–R5 formalmente certified; R6 first real PAPER canary broker-truth closed; W78/W79/W80/W81/W82 technically certified; TD-R7D-001/002 CLOSED; W83 strategy-version binding ACTIVE.**

## Fuente de verdad al retomar
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`;
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`;
3. `knowledge/00_CANON/TAREA_ACTIVA.md`;
4. `knowledge/00_CANON/debt_register_r7d_auto_paper.json`;
5. `knowledge/30_DECISIONES/ADR-0014-w82-fee-complete-execution-accounting.md`;
6. este handoff.

R5 sigue siendo el último track formal certificado del machine debt register principal. R6 y W78–W82 son hitos técnicos independientes.

## Stack activo
- PR #49 — R7 real PAPER close / lifecycle operacional independiente;
- PR #50 — W78 execution qualification;
- PR #51 — W79 promotion governance + Strategy Lab;
- PR #52 — W80 durable promotion assessment;
- PR #53 — W81 execution-cost continuity;
- PR #54 — W82 fee-complete deterministic execution accounting, DRAFT apilado sobre W81;
- W83 debe abrirse como nuevo DRAFT PR apilado sobre el exact final W82 closure head sólo después de recertificar PR #54.

No fusionar el stack fuera de orden.

## W82 — resultado final de comportamiento
W82 cierra `TD-R7D-002 / FEE_ACCOUNTING_INCOMPLETE` para deterministic qualification mediante evidencias separadas y un resolver candidate-bound:

1. base fee accounting ligado al exact Research cost model + W78/W81 evidence;
2. product-aware fee mechanics;
3. fresh versioned documented Alpaca crypto fee-schedule attestation;
4. final candidate resolution que revalida identidad y semántica antes de retirar el blocker.

`PromotionFeeAccountingResolution` V3 es el único punto que puede quitar `FEE_ACCOUNTING_INCOMPLETE`.

### Alpaca crypto conservatism
La attestation documental W82 fija:
- canonical qualification venue: `alpaca-paper-model`;
- Tier 1 maker 15 bps;
- Tier 1 taker 25 bps;
- conservative qualification floor 25 bps cuando no existe evidencia certificada de volume tier / liquidity role más favorable;
- source snapshot fijo/versionado con expiry de 30 días.

La resolución final exige además:
- `FeeChargeConvention.RECEIVED_ASSET_PERCENT`;
- `FeeLiquidityRole.WORST_CASE`.

Una local caller policy no puede abaratar el floor. Una attestation Alpaca no puede mintarse para otro venue. Una receipt re-hasheada con charge convention o liquidity role distinto falla closed.

### Fee truth real NO se fabrica
Broker fee activity sigue separada:
- `broker_authoritative_fee_proven=false`;
- missing/unpublished activity => `PENDING_PUBLICATION`;
- `fee_amount=None`;
- `zero_fee_inferred=false`;
- `realized_profitability_authorized=false`.

Gross-vs-net position, rounding o residual quantity no son fuente válida de fee truth.

## W82 behavioral exact-head evidence
Behavioral implementation head:

`66dbc63941cb2d6552ff1dfadc292dc020e1ecb2`

Dedicated W82 run `32684230790`:
- **49/49 W82 PASS**;
- W82 fee-accounting boundary PASS;
- W82 promotion fee-resolution boundary PASS;
- canonical Alpaca venue/charge/liquidity semantic hardening PASS;
- W81/W80/W79/W78/Research boundaries PASS.

Core Safety run `32684230698`:
- **2966/2966 PASS**;
- exact coverage `85.12870855148343%`;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- inherited R5/R6/R7/W78–W82 boundaries PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

## Canonical closure
- `TD-R7D-001`: CLOSED;
- `TD-R7D-002`: CLOSED;
- `TD-R7D-003`: OPEN P2.

El cierre de `TD-R7D-002` significa **fee-complete deterministic qualification accounting**. No significa broker-observed realized fee ni realized profitability.

Los blockers científicos aún abiertos son:
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

## Exact-head discipline de PR #54
El cierre canónico/documental posterior al behavioral head debe volver a demostrar Dedicated W82 + Core Safety sobre el **mismo exact final head**.

Una vez verde:
- actualizar PR #54 con ese exact head y los run IDs finales;
- mantener PR #54 DRAFT por ser parte del stack;
- no volver a editar canon sólo para copiar el nuevo SHA, evitando un loop de self-invalidating documentation commits.

## W83 — retomar aquí
Objetivo: cerrar `EXECUTION_STRATEGY_VERSION_UNBOUND` sin habilitar Auto-Paper.

La identidad disponible hoy es más fuerte que un version string:
- `TrialSpec`: strategy id/version, parameters, dataset hash, split, phase, code version + canonical fingerprint;
- W79 `StrategyPromotionPolicy`: selected trial id/fingerprint, selected strategy id/version, tournament fingerprint;
- W80/W81/W82: candidate/assessment/economic-resolution chain;
- `OrderIntent`: strategy id + intent fingerprint, sin strategy version.

Por tanto, **no añadir `strategy_version` a `OrderIntent` por reflejo**. Primero inspeccionar el runtime/registry/DSL exactos y construir, si es viable, un sidecar `ExecutionStrategyVersionBinding` que pruebe:

`selected candidate strategy == frozen deterministic strategy artifact == runtime strategy identity used to derive future intent identity`.

Debe ligar como mínimo:
- exact W79 candidate policy + selected trial fingerprint;
- exact W80 assessment;
- exact W81 resolution;
- exact W82 resolution;
- strategy id/version;
- canonical artifact/DSL/config/parameters/defaults;
- product/symbol/universe identity;
- runtime/compiler/interpreter version cuando afecte semantics;
- deterministic intent derivation proof;
- chronology/freeze provenance;
- immutable receipt hash.

### Inspección prioritaria W83
Antes de implementar, revisar:
- `src/autotrade/research/strategy.py`;
- `src/autotrade/research/registry.py`;
- `src/autotrade/research/trials.py`;
- `src/autotrade/research/tournament.py`;
- W79 policy creation/validation en `strategy_lab_promotion.py`;
- cualquier Strategy Runtime real que produzca `OrderIntent`;
- persistencia/serialization del intent sólo para entender el boundary, sin modificarlo todavía.

### Fail-closed W83
Debe BLOCK/reject:
- same id/version con artifact distinto;
- artifact/params/default/runtime drift;
- candidate/assessment/W81/W82 mismatch;
- selected trial fingerprint mismatch;
- intent derivado de otra estrategia;
- version string spoof;
- artifact congelado ex post cuando chronology lo prohíba;
- receipt tampering/reuse;
- missing canonical material;
- nondeterministic reconstruction.

W83 sólo podrá remover `EXECUTION_STRATEGY_VERSION_UNBOUND`.

Debe conservar:
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TD-R7D-003` OPEN;
- PAPER candidate FALSE;
- external execution FALSE;
- capital authority NONE;
- broker-authoritative fee proof FALSE;
- realized profitability unauthorized;
- LIVE BLOCKED.

## R7B sigue independiente
Nada en W78–W83 resuelve por inferencia un lifecycle broker real. PR #49 mantiene su camino one-shot/GET-only recovery y sus propias condiciones operacionales.

## Regla de producto
La cadena futura permanece:

`Research -> Promotion Evidence -> Durable Assessment -> Economic Qualification -> Strategy Version Binding -> Shadow/Forward Binding -> PAPER Candidate Decision -> Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

IA/model output nunca puede saltarse esa cadena ni llegar directamente a broker POST.

## Estado de autoridad
- PAPER candidate: FALSE;
- W78–W83 capital authority: NONE;
- broker write desde capas científicas: NO;
- credentials en Strategy Lab: NO;
- broker-authoritative fee proof por W82: NO;
- realized profitability claim: NO;
- LIVE: BLOCKED.

**LIVE TRADING: BLOQUEADO.**
