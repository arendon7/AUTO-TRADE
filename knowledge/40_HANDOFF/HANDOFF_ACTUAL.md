# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-24
Estado: **R0–R5 formalmente certified; R6 first real PAPER canary broker-truth closed; W78/W79/W80/W81/W82 technically certified; TD-R7D-002 CLOSED; W83 strategy-version binding next.**

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
- PR #54 — W82 fee-complete deterministic execution accounting, DRAFT apilado sobre W81.

No fusionar el stack fuera de orden.

## W82 — resultado
W82 cierra `TD-R7D-002 / FEE_ACCOUNTING_INCOMPLETE` para deterministic qualification mediante tres evidencias separadas y un resolver candidate-bound:

1. base fee accounting ligado al exact Research cost model + W78/W81 evidence;
2. product-aware fee mechanics;
3. fresh versioned documented Alpaca crypto fee-schedule attestation.

`PromotionFeeAccountingResolution` V3 es el único punto que puede quitar `FEE_ACCOUNTING_INCOMPLETE` y vuelve a validar identidad económica antes de hacerlo.

### Alpaca crypto conservatism
La attestation documental W82 fija:
- Tier 1 maker 15 bps;
- Tier 1 taker 25 bps;
- conservative qualification floor 25 bps cuando no existe evidencia certificada de volume tier / liquidity role más favorable;
- snapshot versionado y expirable a 30 días.

Una local caller policy no puede abaratar ese floor.

### Fee truth real NO se fabrica
Broker fee activity sigue separada:
- `broker_authoritative_fee_proven=false`;
- missing/unpublished activity => `PENDING_PUBLICATION`;
- `fee_amount=None`;
- `zero_fee_inferred=false`;
- `realized_profitability_authorized=false`.

Gross-vs-net position, rounding o residual quantity no son fuente válida de fee truth.

## W82 behavioral evidence
Behavioral implementation head:

`78f3a1a7d454b0c096b0c6f1085942bb1c131452`

Dedicated W82 run `32682423352`:
- **47/47 W82 PASS**;
- W82 fee-accounting boundary PASS;
- W82 promotion fee-resolution boundary PASS;
- W81/W80/W79/W78/Research boundaries PASS.

Core Safety run `32682423322`:
- **2964/2964 PASS**;
- coverage `85.13062266745237%`;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- all inherited R5/R6/R7/W78–W81 boundaries PASS;
- both W82 boundaries PASS;
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

## Exact-head discipline
Después de los cambios documentales, PR #54 debe volver a demostrar Dedicated W82 + Core Safety en el **mismo exact final head**. No reutilizar la evidencia de `78f3a1a7…` como certificación del SHA documental posterior.

Sólo después de esa recertificación se debe actualizar el cuerpo de PR #54 con el head/run final y declarar W82 integralmente cerrado.

## W83 — retomar aquí
Objetivo: cerrar `EXECUTION_STRATEGY_VERSION_UNBOUND` sin habilitar Auto-Paper.

El problema no es sólo guardar un string `strategy_version`. W83 debe probar que:

`selected candidate strategy == frozen deterministic strategy artifact == runtime strategy identity used to derive future intent identity`.

Debe ligar como mínimo:
- exact W79 campaign/candidate;
- exact W80 assessment;
- exact W81 resolution;
- exact W82 resolution;
- strategy id/version;
- canonical deterministic artifact hash;
- DSL/config/parameter/default hashes o canonical equivalents existentes;
- product/symbol/universe identity;
- interpreter/runtime version cuando afecte semantics;
- deterministic intent derivation proof/fingerprint;
- chronology y immutable receipt hash.

### Fail-closed W83
Debe BLOCK/reject:
- same id/version con artifact distinto;
- artifact/params/default/runtime drift;
- candidate/assessment mismatch;
- intent derivado de otra estrategia;
- version string spoof;
- artifact congelado ex post cuando la chronology lo prohíba;
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
- realized profitability claim: NO;
- LIVE: BLOCKED.

**LIVE TRADING: BLOQUEADO.**
