# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-23
Estado: **R0–R5 certified; R6 first real PAPER canary broker-truth closed; R7 PAPER Operations active; W78 execution qualification certified; W79 promotion governance certified; W80 durable promotion assessment technically certified; W81 execution-cost continuity next.**

## Fuente de verdad al retomar
Leer en este orden:
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`;
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`;
3. `knowledge/00_CANON/TAREA_ACTIVA.md`;
4. `knowledge/00_CANON/CONTEXTO_RAPIDO.md`;
5. `knowledge/30_DECISIONES/ADR-0010-w78-deterministic-paper-execution-model.md`;
6. `knowledge/30_DECISIONES/ADR-0011-w79-strategy-promotion-governance.md`;
7. `knowledge/30_DECISIONES/ADR-0012-w80-durable-promotion-assessment.md`;
8. este handoff.

## Stack de PRs activo
- PR #49 — R7 real PAPER risk-reducing close; obligación operacional independiente;
- PR #50 — W78 execution qualification;
- PR #51 — W79 Strategy Promotion Governance;
- PR #52 — W80 Durable Promotion Assessment, DRAFT apilado sobre W79.

No fusionar la pila fuera de orden.

## W80 — qué quedó construido

### 1. Durable assessment writer
`src/autotrade/strategy_promotion_assessment.py`

Persiste assessment científico W79 como receipt:
- append-only;
- hash-bound;
- ordinal por policy;
- predecessor assessment hash;
- timezone-aware timestamp;
- exact gate set/reason codes/evidence hashes;
- source W79 view hash;
- exact policy/threshold/strategy identity;
- `BEGIN IMMEDIATE`;
- no arbitrary view ingestion;
- evidencia previa no puede desaparecer;
- no-MISSING no puede volver a MISSING;
- unchanged view no puede insertarse con otro id.

### 2. Independent reader
`src/autotrade/strategy_promotion_assessment_read_model.py`

No importa el writer W80.

Abre `core.sqlite3`:
- `mode=ro`;
- `query_only=ON`;
- sin schema initialization;
- sin mutation;
- sin broker/credentials/OMS/Safety.

Revalida independientemente:
- receipt hash;
- SQLite side columns;
- gate set/status/reasons/evidence;
- predecessor/ordinal/timestamp chain;
- evidence-history monotonicity;
- frozen W79 candidate policy;
- policy hash;
- threshold-policy hash;
- selected strategy id/version.

Un journal autocoherente pero desligado de su frozen W79 policy falla cerrado.

### 3. Strategy Lab
La ruta permanece:

`GET /api/strategy-lab`

No se añadió POST ni SAFE_ACTION.

La UI separa:
- W79 governance provenance;
- W80 assessment provenance.

W79 conserva exactamente:

`gate_evidence_state=NOT_PERSISTED_BY_W79`

W80 expone:
- `DURABLE_W80_ASSESSMENT`; o
- `NO_DURABLE_W80_ASSESSMENT`.

Puede mostrar assessment states/gates/reasons/evidence hashes, pero siempre:
- PAPER candidate FALSE;
- external execution FALSE;
- CAPITAL NONE;
- LIVE BLOCKED;
- Broker POST NO.

## W80 exact implementation certification
Behavioral implementation head:

`492ca4a621b263324b2cb5322490d74beda66a9c`

Dedicated W80 run `32671751555`:
- **46/46 PASS**;
- writer boundary PASS;
- independent reader boundary PASS;
- Strategy Lab durable projection boundary PASS;
- W79 promotion/read-only boundaries PASS;
- Mac Control Center PASS;
- W78 PASS;
- Research Authority PASS.

Core Safety `32671751544`:
- **2890/2890 PASS**;
- coverage exacta **85.1061367161277%**;
- writer 82%;
- independent assessment reader 84%;
- Strategy Lab read-model 84%;
- all inherited R5/R6/R7/W78/W79 boundaries PASS;
- all W80 boundaries PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

Los commits canónicos posteriores a ese implementation head sólo documentan cierre/handoff. Exigir CI exact-head también sobre el head documental final antes de considerar PR #52 completamente cerrada.

## W80 no significa
- no demuestra estrategia rentable;
- no concede Auto-Paper;
- no concede capital;
- no concede broker write;
- no altera la exposición PAPER real;
- no cierra fees;
- no cierra Shadow/Forward;
- no cierra strategy runtime/version binding;
- no convierte el SHA-256 local en un transparency log firmado.

## Deuda abierta relevante
- `TD-R7D-001` P1 — total execution-cost continuity;
- `TD-R7D-002` P1 — fee-complete accounting;
- `TD-R7D-003` P2 — safe remaining-quantity reservation after partial fills;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `EXECUTION_STRATEGY_VERSION_UNBOUND`.

## W81 — próximo bloque
Objetivo: cerrar `TD-R7D-001` sin mezclar fees.

Problema exacto:
- Research preregistra `half_spread_bps + slippage_bps`;
- W78 usa spread observado desde bid/ask + configured adverse slippage;
- la comprobación actual garantiza slippage configurado >= Research slippage, pero no garantiza que el spread observado sea >= Research half-spread;
- una observación con spread estrecho puede ser más favorable que Research y no debe pasar como conservative qualification.

Diseño esperado:
- evidence contract hash-bound;
- exact Research cost-model binding;
- exact W78 scenario/matrix binding;
- quote bid/ask + side;
- observed effective half-spread;
- configured adverse slippage;
- modeled total non-fee price impact;
- preregistered Research non-fee impact;
- explicit classification (`CONSERVATIVE`, `FAVORABLE_OBSERVATION`, `BLOCKED` o equivalente);
- provenance/evidence hashes;
- compatible read-only external PAPER execution evidence cuando exista;
- no ex-post widening/selection;
- PAPER candidate false;
- broker write none;
- LIVE blocked.

`FEE_ACCOUNTING_INCOMPLETE` permanece explícitamente abierto después de W81. No inventar fees dentro de core `Fill` ni hacer profitability claims fee-complete.

## R7 real sigue independiente
PR #49 conserva:
- broker Portfolio GET truth;
- PAPER-only;
- strict risk reduction;
- fresh Safety + OMS antes del writer;
- durable UNKNOWN before one POST;
- no retry POST;
- GET-only ambiguity reconciliation;
- residual exposure => stop;
- credentials memory-only;
- LIVE blocked.

## Regla de producto / autoridad
Research, assessment y ejecución siguen siendo capas diferentes:

`Research -> Promotion Governance -> Durable Assessment -> future PAPER Candidate Decision -> Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

Ninguna salida de IA/modelo puede saltarse esa cadena.

## Estado de autoridad
- W80 PAPER candidate: **FALSE**;
- W80 broker write: **NO**;
- W80 capital authority: **NONE**;
- Strategy Lab credentials: **NO**;
- Strategy Lab broker network: **NO**;
- LIVE: **BLOCKED**.

**LIVE TRADING: BLOQUEADO.**