# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-23
Estado: **R0–R5 certified; R6 first real PAPER canary broker-truth closed; R7 PAPER Operations active; W78 execution qualification certified; W79 Strategy Promotion Governance + Strategy Lab read-only CERTIFIED; W80 Promotion Assessment es el siguiente hito.**

## Fuente de verdad al retomar
Leer en este orden:
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`;
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`;
3. `knowledge/00_CANON/TAREA_ACTIVA.md`;
4. `knowledge/00_CANON/CONTEXTO_RAPIDO.md`;
5. `knowledge/30_DECISIONES/ADR-0010-w78-deterministic-paper-execution-model.md`;
6. `knowledge/30_DECISIONES/ADR-0011-w79-strategy-promotion-governance.md`;
7. este handoff.

R5 sigue siendo el último track formalmente certificado en el machine debt register R0–R5; R6/W78/W79 tienen además los hitos técnicos y broker-truth descritos en este handoff.

## Stack de PRs

### PR #49 — R7 close real PAPER
Branch: `work/r7-paper-close-mac-staging`.

Es la obligación operacional real de reducción de la exposición BTC/USD residual del first canary. Permanece DRAFT hasta alcanzar su verdad terminal de broker. No mezclar este gate con Strategy Lab.

### PR #50 — W78 execution qualification
Branch: `work/w78-realistic-paper-execution`.
Exact-head técnico certificado:

`2924456e33c2cc9e6579301b176267513a90861f`

W78 añade ejecución determinista/no-network para qualification reutilizando Capital Safety + OMS + fills + portfolio + reconciliation existentes. No tiene writer externo, credenciales ni LIVE.

### PR #51 — W79 Strategy Promotion Governance
Branch: `work/w79-strategy-promotion-evidence`.
Base: W78 certificado.
Behavioral implementation head certificado:

`c5c264e64e931ef380801b1e0d1508ea2cac0dfa`

## W79 cerrado técnicamente

### Governance certificado
- `StrategyPromotionThresholdPolicy` preregistrada antes de DEVELOPMENT;
- DEVELOPMENT y FINAL_HOLDOUT separados;
- candidato congelado después de Tournament DEVELOPMENT y antes del HOLDOUT final;
- strategy id/version vinculados;
- selected trial fingerprint;
- tournament fingerprint;
- una sola autoridad SQLite;
- escritura append-only/idempotente;
- hashes de policy verificables.

Gates:
- `DEVELOPMENT_SELECTION`;
- `EXECUTION_SENSITIVITY`;
- `FINAL_HOLDOUT`;
- `MULTIPLE_TESTING`.

W79 fuerza:
- `paper_candidate_authorized=false`;
- `external_execution_authorized=false`;
- `capital_authority=NONE`;
- `live_trading=BLOCKED`.

### Strategy Lab certificado
El Mac Control Center expone:
- `/strategy-lab`;
- `GET /api/strategy-lab`.

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

W79 no persiste todavía un assessment autoritativo de gates. Strategy Lab debe mantener `NOT_PERSISTED_BY_W79` hasta que W80 tenga evidence receipts válidos.

### Evidencia de certificación W79
Sobre `c5c264e64e931ef380801b1e0d1508ea2cac0dfa`:
- W79 dedicated workflow: PASS;
- 73 pruebas W79: PASS;
- Mac Control Center: 19 pruebas PASS;
- Core Safety: 2844/2844 PASS;
- branch coverage: `85.15094919501644%`;
- W79 promotion boundary: PASS;
- Strategy Lab read-only boundary: PASS;
- Mac Control Center boundary: PASS;
- W78 boundary: PASS;
- Research authority: PASS;
- Debt Register: PASS;
- Canonical Knowledge: PASS.

## Blockers que siguen abiertos
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `FEE_ACCOUNTING_INCOMPLETE`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN`;
- `TD-R7D-001` total execution-cost continuity;
- `TD-R7D-002` fee-complete execution accounting;
- `TD-R7D-003` partial-fill remaining-quantity reservation.

Por esto un assessment favorable todavía no puede equivaler a Auto-Paper.

## Próximo hito — W80 Promotion Assessment durable

Retomar desde `knowledge/00_CANON/TAREA_ACTIVA.md`.

W80 debe persistir receipts autoritativos que vinculen:
- exact threshold/candidate policy ids + hashes;
- strategy id/version;
- selected trial + tournament fingerprints;
- DEVELOPMENT/HOLDOUT identities;
- gate statuses + reason codes;
- evidence hashes + provenance;
- assessment state;
- immutable receipt hash.

Debe usar la misma autoridad SQLite y semántica append-only/idempotente. Strategy Lab podrá leer esos receipts, pero seguirá `mode=ro/query_only` y GET-only.

W80 mantiene obligatoriamente:
- PAPER candidate: FALSE;
- capital authority: NONE;
- broker network: NONE;
- broker write: NO;
- credentials: NO;
- OrderIntent authority: NO;
- Safety/OMS execution authority: NO;
- LIVE: BLOCKED.

La eventual decisión `PAPER_CANDIDATE` será un hito posterior y separado.

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
Research, promotion governance y ejecución siguen separados:

`Research -> Promotion Evidence -> future PAPER Candidate Decision -> Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

Ninguna salida de IA/modelo puede saltarse esa cadena.

## Estado de autoridad
- W79 PAPER candidate: **FALSE**;
- W80 PAPER candidate: **FALSE**;
- Strategy Lab capital authority: **NONE**;
- Strategy Lab broker write: **NO**;
- Strategy Lab credentials: **NO**;
- LIVE: **BLOCKED**.

**LIVE TRADING: BLOQUEADO.**
