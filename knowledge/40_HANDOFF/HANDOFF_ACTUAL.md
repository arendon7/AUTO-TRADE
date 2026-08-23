# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-23
Estado: **R0–R5 certified; R6 first real PAPER canary broker-truth closed; R7 PAPER Operations active; W78 execution qualification certified; W79 Strategy Promotion Governance + Strategy Lab read-only DRAFT.**

## Fuente de verdad al retomar

Leer en este orden:
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`;
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`;
3. `knowledge/00_CANON/TAREA_ACTIVA.md`;
4. `knowledge/00_CANON/CONTEXTO_RAPIDO.md`;
5. `knowledge/30_DECISIONES/ADR-0010-w78-deterministic-paper-execution-model.md`;
6. `knowledge/30_DECISIONES/ADR-0011-w79-strategy-promotion-governance.md`;
7. este handoff.

## Stack de PRs activo

### PR #49 — R7 close real PAPER
Branch: `work/r7-paper-close-mac-staging`.

Es la única obligación operacional real de reducción de la exposición BTC/USD residual del first canary. Permanece DRAFT hasta alcanzar verdad terminal de broker mediante su propio flujo.

No mezclar este gate con Strategy Lab.

### PR #50 — W78 execution qualification
Branch: `work/w78-realistic-paper-execution`.
Exact-head técnico certificado:

`2924456e33c2cc9e6579301b176267513a90861f`

W78 añade ejecución determinista/no-network para qualification reutilizando Capital Safety + OMS + fills + portfolio + reconciliation existentes. No tiene writer externo, credenciales ni LIVE.

### PR #51 — W79 Strategy Promotion Governance
Branch: `work/w79-strategy-promotion-evidence`.
Base: W78 certificado.

Es la rama activa de desarrollo en este handoff.

## Qué ya existe en W79

### Governance
- `StrategyPromotionThresholdPolicy` preregistrada antes de DEVELOPMENT;
- DEVELOPMENT y FINAL_HOLDOUT separados;
- candidato congelado después de Tournament DEVELOPMENT y antes del HOLDOUT final;
- strategy id/version vinculados;
- selected trial fingerprint;
- tournament fingerprint;
- una sola autoridad SQLite;
- escritura append-only/idempotente;
- hashes de policy verificables.

Gates canónicos:
- `DEVELOPMENT_SELECTION`;
- `EXECUTION_SENSITIVITY`;
- `FINAL_HOLDOUT`;
- `MULTIPLE_TESTING`.

W79 fuerza:
- `paper_candidate_authorized=false`;
- `external_execution_authorized=false`;
- `live_trading=BLOCKED`.

### Strategy Lab de producto
El Mac Control Center ya expone una tercera superficie junto a Equities y Crypto:

`/strategy-lab`

Su API es:

`GET /api/strategy-lab`

El read model:
- abre `core.sqlite3` con `mode=ro`;
- activa `PRAGMA query_only=ON`;
- no muta SQL;
- no usa broker;
- no usa credenciales;
- no construye `OrderIntent`;
- no entra en `SAFE_ACTIONS`;
- no tiene ruta POST;
- no concede Safety/OMS/capital authority.

La pantalla muestra governance state, threshold policies, candidate policies, blockers y provenance.

Los gates todavía no tienen assessment durable autoritativo. Debe seguir mostrando:

`NOT_PERSISTED_BY_W79`

hasta el próximo bloque arquitectónico. No inventar resultados.

## Boundaries añadidos

W79 incluye:
- `scripts/check_w79_strategy_promotion_boundary.py`;
- `scripts/check_w79_strategy_lab_read_model_boundary.py`;
- extensión del `check_mac_dashboard_boundary.py`;
- wiring en workflow dedicado W79;
- wiring permanente en Core Safety.

Los boundaries niegan broker/network/credentials/writer/TradingPipeline/Safety/OMS authority y también cualquier mutación o POST desde Strategy Lab.

## Blockers que siguen abiertos

- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `FEE_ACCOUNTING_INCOMPLETE`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN`;
- `TD-R7D-001` total execution-cost continuity;
- `TD-R7D-002` fee-complete execution accounting;
- `TD-R7D-003` partial-fill remaining-quantity reservation.

Por esto un assessment W79 favorable todavía no puede equivaler a Auto-Paper.

## Cómo retomar correctamente

1. Resolver el head actual de PR #51.
2. No confiar en CI de heads anteriores.
3. Exigir sobre ese mismo head:
   - W79 dedicated PASS;
   - W79 promotion boundary PASS;
   - Strategy Lab read-only boundary PASS;
   - Mac Control Center boundary PASS;
   - suite W79 PASS;
   - W78 boundary PASS;
   - Research authority PASS;
   - Core Safety PASS;
   - coverage >=85%;
   - Debt Register PASS;
   - Knowledge Contract PASS.
4. Si cualquier gate falla, corregir dentro de W79 y repetir exact-head.
5. Sólo cuando todo esté verde, actualizar PR #51 con la evidencia exacta y declarar W79 técnicamente cerrado.
6. Mantener PR #51 DRAFT si la política de stacking/base aún exige no fusionarlo.

## Próximo bloque, pero no abrirlo antes del cierre anterior

El siguiente hito lógico es persistir un **Promotion Assessment durable**:
- exact policy binding;
- gate id/status;
- reason codes;
- evidence hashes;
- timestamps/provenance;
- immutable/hash-bound receipt;
- UI leyendo assessment real.

Ese bloque debe mantener `paper_candidate_authorized=false` y cero broker authority. La eventual decisión `PAPER_CANDIDATE` será un hito separado.

## R7 close sigue independiente

La exposición real PAPER no se resuelve por avanzar Strategy Lab. PR #49 conserva:
- PAPER-only;
- FULL BTC/USD SELL LIMIT IOC;
- strict risk reduction;
- fresh Portfolio + Safety + OMS antes de writer;
- durable UNKNOWN antes del único POST;
- GET-only reconciliation;
- no retry POST;
- residual exposure => stop;
- credenciales memory-only;
- LIVE bloqueado.

## Regla de producto

Research, promotion governance y ejecución son superficies diferentes:

`Research -> Promotion Evidence -> future PAPER Candidate Decision -> Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

Ninguna salida de IA/modelo puede saltarse esa cadena.

## Estado de autoridad

- W79 PAPER candidate: **FALSE**;
- W79 capital authority: **NONE**;
- W79 broker write: **NO**;
- Strategy Lab credentials: **NO**;
- Strategy Lab broker network: **NO**;
- LIVE: **BLOCKED**.

**LIVE TRADING: BLOQUEADO.**
