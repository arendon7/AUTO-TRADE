# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-23
Estado canónico: **R0–R5 CERTIFIED; R6 FIRST PAPER CANARY BROKER-TRUTH CLOSED; R7 PAPER OPERATIONS ACTIVE; W78 EXECUTION QUALIFICATION CERTIFIED; W79 STRATEGY PROMOTION GOVERNANCE CERTIFIED; W80 DURABLE PROMOTION ASSESSMENT TECHNICALLY CERTIFIED; W81 EXECUTION-COST CONTINUITY NEXT.**

## R6 — hito real alcanzado
El primer canary crypto PAPER compatible con el mínimo real de Alpaca terminó reconciliado por verdad del broker.

Evidencia de operador:
- `attempt_id=first-canary-57d01d35e8b25f4babc57695ac87d962`;
- broker status `filled`;
- broker fill `0.00014432 BTC`;
- net broker position observada `0.000143959 BTC`;
- reconciliation `ORDER_PLUS_POSITION`;
- `entry_attempt_count=1`;
- `phase=RECOVERED_GET_ONLY`;
- `retry_post=false`;
- `credentials_persisted=false`;
- `LIVE=BLOCKED`.

Exact-head R6 first-canary: `0cbb782015eeed200b9851b53764ac6389c3d9ff`.

Ese hito demostró plumbing de entrada/recovery, no edge ni rentabilidad.

## R7 PAPER Operations

### R7A — Portfolio Truth
R7 incorpora broker-truth read models para account, posiciones y órdenes abiertas. Esta capa es GET/read-only y no concede broker write authority.

### R7B — risk-reducing close
PR #49 (`work/r7-paper-close-mac-staging`) mantiene separada la operación real de reducción de exposición BTC/USD.

Invariantes:
- PAPER sólo;
- posición real broker-bound;
- SELL FULL BTC/USD LIMIT IOC risk-reducing;
- Capital Safety + OMS reconstruidos frescos antes de ejecución;
- human review sin capital authority;
- durable UNKNOWN antes del único POST;
- exactamente un POST por attempt;
- ambigüedad/burned attempt => GET-only reconciliation;
- residual exposure => stop, nunca segundo SELL automático;
- credenciales memory-only;
- LIVE bloqueado.

PR #49 sigue DRAFT hasta que su gate runtime real alcance la verdad terminal exigida. Un CI verde por sí solo no cierra esa obligación.

## W78 — Strategy Lab execution qualification

Branch: `work/w78-realistic-paper-execution`.
PR #50.
Exact-head certificado: `2924456e33c2cc9e6579301b176267513a90861f`.

W78 reutiliza el control plane existente:

`OrderIntent -> Capital Safety -> OMS -> deterministic no-network broker -> Fill/EventLedger -> Portfolio/Reconciliation`

Certificado:
- deterministic adverse slippage;
- bounded partial fills;
- LIMIT fill/no-fill después de slippage;
- stale/future/crossed/spread deterministic rejection;
- local broker idempotency;
- cancel preserving fills;
- simulated inspectable broker truth;
- canonical durable portfolio + reconciliation;
- hash-bound execution scenarios/matrices;
- Research cost-model qualification contract;
- scientific `measurement_hash` separado de runtime `evidence_hash`;
- Execution Sensitivity Lab multi-scenario;
- permanent no-network/no-writer boundary.

W78 no prueba rentabilidad, no predice fills Alpaca y no concede Auto-Paper.

## W79 — Strategy Promotion Governance — CERTIFIED

Branch: `work/w79-strategy-promotion-evidence`.
PR #51, apilado sobre W78 certificado.
Behavioral implementation head certificado: `c5c264e64e931ef380801b1e0d1508ea2cac0dfa`.

W79 certificó:
1. `StrategyPromotionThresholdPolicy` antes de DEVELOPMENT;
2. Tournament DEVELOPMENT selecciona el candidato;
3. `StrategyPromotionPolicy` congela strategy id/version, trial fingerprint y tournament fingerprint antes del HOLDOUT final;
4. DEVELOPMENT y HOLDOUT separados;
5. una sola autoridad SQLite para policies + Trial Ledger;
6. policies hash-bound y append-only/idempotentes por identidad.

Gates:
- `DEVELOPMENT_SELECTION`;
- `EXECUTION_SENSITIVITY`;
- `FINAL_HOLDOUT`;
- `MULTIPLE_TESTING`.

Estados:
- PASS;
- FAIL;
- MISSING;
- BLOCKED.

Contrato permanente:

`EVIDENCE_QUALIFIED != PAPER_CANDIDATE`.

W79 fuerza:
- `paper_candidate_authorized=false`;
- `external_execution_authorized=false`;
- `capital_authority=NONE`;
- `live_trading=BLOCKED`.

Evidencia W79 behavioral head:
- 2844/2844 Core PASS;
- coverage `85.15094919501644%`;
- W79 promotion boundary PASS;
- Strategy Lab read-only boundary PASS;
- Mac Control Center PASS;
- W78 / Research / Debt / Knowledge PASS.

## W80 — Durable Promotion Assessment — TECHNICALLY CERTIFIED

Branch: `work/w80-durable-promotion-assessment`.
PR #52, DRAFT apilado sobre W79.
Behavioral implementation head certificado:

`492ca4a621b263324b2cb5322490d74beda66a9c`

### Problema resuelto
W79 podía evaluar gates pero deliberadamente no persistía la evaluación. Strategy Lab debía mostrar `gate_evidence_state=NOT_PERSISTED_BY_W79` para no convertir datos parciales en un PASS visual.

W80 persiste la conclusión científica como evidencia durable, pero **no** como permiso de trading.

### Journal W80
`strategy_promotion_assessment.py` implementa:
- `StrategyPromotionAssessmentReceipt` hash-bound;
- journal SQLite append-only;
- `BEGIN IMMEDIATE`;
- ordinal monotónico por policy;
- predecessor assessment hash;
- timestamp monotónico;
- W79 evaluation ejecutada internamente;
- no arbitrary public `StrategyPromotionEvidenceView` ingestion;
- side-column / receipt JSON cross-check;
- duplicate/conflicting identity fail-closed;
- unchanged source view no puede reinsertarse con otro id;
- evidence hashes observados no pueden desaparecer;
- un gate no-MISSING no puede volver a MISSING;
- authority siempre false/NONE/BLOCKED.

### Reader W80 independiente
`strategy_promotion_assessment_read_model.py`:
- NO importa el writer W80;
- SQLite `mode=ro`;
- `PRAGMA query_only=ON`;
- rechaza core DB symlinked/missing;
- recalcula receipt hashes;
- valida receipt/side-columns;
- valida toda la predecessor chain;
- valida monotonicidad de evidence hashes y estados;
- reconstruye la frozen W79 policy;
- exige exact policy hash, threshold hash, selected strategy id/version;
- exige que la frozen threshold identity siga presente;
- journal autocoherente pero desligado de W79 => BLOCKED.

### Strategy Lab W80
La ruta continúa siendo la misma:

`GET /api/strategy-lab`

No se añadió POST ni SAFE_ACTION.

La proyección mantiene dos dominios de provenance separados:
1. W79 governance — thresholds + candidate freeze y `gate_evidence_state=NOT_PERSISTED_BY_W79`;
2. W80 durable assessment — history/last assessment con provenance propio o `NO_DURABLE_W80_ASSESSMENT`.

La UI muestra:
- assessment id / ordinal;
- policy y strategy version;
- assessment state;
- gates;
- reason codes;
- evidence hashes;
- predecessor / assessment / source hashes;
- W79 provenance y W80 provenance;
- blockers.

Sigue mostrando explícitamente:
- `PAPER CANDIDATE · FALSE`;
- `CAPITAL · NONE`;
- `LIVE · BLOCKED`;
- `Broker POST: NO`.

### Certificación técnica W80
Implementation head `492ca4a621b263324b2cb5322490d74beda66a9c`:

Dedicated W80 workflow `32671751555`:
- **46/46 W80 tests PASS**;
- W80 writer boundary PASS;
- W80 independent reader boundary PASS;
- W80 Strategy Lab durable projection boundary PASS;
- W79 promotion boundary PASS;
- W79 Strategy Lab read-only boundary PASS;
- Mac Control Center boundary PASS;
- W78 boundary PASS;
- Research Authority PASS.

Core Safety workflow `32671751544`:
- **2890/2890 PASS**;
- required coverage 85%;
- exact coverage `85.1061367161277%` PASS;
- `strategy_promotion_assessment.py`: 82%;
- `strategy_promotion_assessment_read_model.py`: 84%;
- `strategy_lab_read_model.py`: 84%;
- all inherited R5/R6/R7/W78/W79 boundaries PASS;
- all three W80 boundaries PASS;
- Debt Register PASS: 60 items / 3 registries; 11 open, 10 blocking on uncertified tracks;
- Canonical Knowledge PASS.

### Criptografía / alcance de integridad
La cadena SHA-256 es evidencia determinista contra cambios parciales/inconsistentes dentro del contrato local. No se presenta como transparencia log firmado ni como defensa contra un administrador privilegiado capaz de reescribir coherentemente todo SQLite y sus policies. Una futura garantía de no repudio requeriría checkpoint firmado o anclaje externo separado.

## Blockers que permanecen después de W80
- `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN` / `TD-R7D-001` P1;
- `FEE_ACCOUNTING_INCOMPLETE` / `TD-R7D-002` P1;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `TD-R7D-003` P2 safe remaining-quantity reservation after partial fills.

W80 no demuestra rentabilidad, no concede PAPER candidate y no altera R7B real.

## W81 — siguiente hito
W81 debe cerrar `TD-R7D-001` demostrando continuidad de **price impact** entre:

`Research ExecutionCostModel -> W78 scenario/quote -> effective spread + adverse slippage -> compatible PAPER execution evidence`

El propósito es impedir que una qualification use fricción posterior más favorable que la preregistrada sin etiquetarla explícitamente como favorable/no-conservadora.

W81 debe clasificar evidencia de forma fail-closed y hash-bound. Un spread observado más estrecho no puede convertirse en PASS conservador por interpretación.

`TD-R7D-002` fees sigue siendo P1 separado: W81 no puede declarar P&L fee-complete ni quitar `FEE_ACCOUNTING_INCOMPLETE`.

## Deuda R7D / Auto-Paper
Machine-readable: `knowledge/00_CANON/debt_register_r7d_auto_paper.json`.

- `TD-R7D-001` P1 — total execution-cost continuity Research -> PAPER;
- `TD-R7D-002` P1 — fee-complete execution accounting antes de profitability/Auto-Paper claims;
- `TD-R7D-003` P2 — remaining-quantity reservation segura después de partial fills.

## Modelo de automatización
La IA puede generar hipótesis, variantes, experimentos y análisis. No puede tener camino directo a POST.

Una estrategia automática futura deberá cruzar:

`Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

Ningún W78/W79/W80/W81 puede saltar esa cadena.

## Negative tests permanentes
Mantener y ampliar:
- acciones fuera de orden;
- stale/tampered evidence;
- wrong account / credential drift;
- unsupported asset;
- over-notional / loss / drawdown / Health / kill switch;
- broker ambiguity / UNKNOWN restart;
- reconciliation mismatch;
- duplicate POST;
- direct writer access;
- qualification/research intentando importar writer/red;
- W79/W80 intentando crear ejecución externa;
- W80 receipt/policy/hash-chain tampering;
- Strategy Lab intentando POST, mutar SQLite, persistir credenciales o sintetizar W79 gates;
- strategy version drift;
- favorable execution assumptions presentados como conservative;
- IA/model output intentando saltar Strategy Runtime/Safety/OMS;
- cualquier intento LIVE.

## Capital
Existe exposición PAPER derivada del first canary y PR #49 mantiene su tratamiento como obligación operacional separada. W78/W79/W80 no tienen autoridad para alterar esa exposición.

**LIVE TRADING: BLOQUEADO.**