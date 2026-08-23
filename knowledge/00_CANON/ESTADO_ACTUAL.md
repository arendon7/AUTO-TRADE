# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-23
Estado canónico: **R0–R5 CERTIFIED; R6 FIRST PAPER CANARY BROKER-TRUTH CLOSED; R7 PAPER OPERATIONS ACTIVE; W78 EXECUTION QUALIFICATION CERTIFIED; W79 STRATEGY PROMOTION GOVERNANCE + STRATEGY LAB READ-ONLY CERTIFIED; W80 PROMOTION ASSESSMENT NEXT.**

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

### Problema resuelto
W79 impide que la selección de una estrategia o sus thresholds se reinterpretan retrospectivamente después de observar HOLDOUT o execution sensitivity.

### Gobernanza certificada
1. `StrategyPromotionThresholdPolicy` se congela antes de DEVELOPMENT.
2. Tournament DEVELOPMENT selecciona el candidato.
3. `StrategyPromotionPolicy` congela strategy id/version, trial fingerprint y tournament fingerprint antes del HOLDOUT final.
4. DEVELOPMENT y HOLDOUT usan campañas separadas.
5. Threshold policy, candidate policy y trial ledger comparten una sola autoridad SQLite.
6. Las políticas son hash-bound y append-only/idempotentes por identidad.

Gates W79:
- `DEVELOPMENT_SELECTION`;
- `EXECUTION_SENSITIVITY`;
- `FINAL_HOLDOUT`;
- `MULTIPLE_TESTING`.

Estados:
- PASS;
- FAIL;
- MISSING;
- BLOCKED.

Contrato:

`EVIDENCE_QUALIFIED != PAPER_CANDIDATE`.

W79 fuerza:
- `paper_candidate_authorized=false`;
- `external_execution_authorized=false`;
- `capital_authority=NONE`;
- `live_trading=BLOCKED`.

### Certificación técnica W79
Sobre `c5c264e64e931ef380801b1e0d1508ea2cac0dfa`:
- dedicated W79 workflow: PASS;
- 73 pruebas W79: PASS;
- 19 pruebas Mac Control Center: PASS;
- Core Safety: `2844 passed`;
- coverage total: `85.15094919501644%` con floor 85% PASS;
- W79 no-execution boundary: PASS;
- W79 Strategy Lab read-only boundary: PASS;
- Mac Control Center boundary: PASS;
- W78 execution boundary: PASS;
- Research authority boundary: PASS;
- Debt Register: PASS;
- Canonical Knowledge: PASS.

La actualización canónica posterior a ese head sólo registra el cierre y el siguiente hito; cualquier head documental final debe revalidar los contratos CI que dispare el PR.

### Blockers preservados
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `FEE_ACCOUNTING_INCOMPLETE`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN`.

## Strategy Lab — superficie W79 certificada

El Control Center nativo incorpora `/strategy-lab` como proyección local de gobernanza.

La ruta:
- abre `core.sqlite3` con `mode=ro`;
- activa `PRAGMA query_only=ON`;
- es GET-only;
- no usa broker ni credenciales;
- no entra en `SAFE_ACTIONS`;
- no expone POST;
- no crea `OrderIntent`;
- no usa Safety/OMS como authority;
- muestra thresholds preregistrados, candidato congelado, blockers y provenance.

W79 todavía **no persiste assessment autoritativo de gates**. La UI debe mostrar `gate_evidence_state=NOT_PERSISTED_BY_W79`; no puede sintetizar un PASS visual.

## W80 — siguiente hito permitido
Persistir un **Promotion Assessment durable, hash-bound y reproducible** que ate cada gate a evidencia inmutable y a la policy exacta.

W80 no puede conceder:
- PAPER candidate authority;
- broker network;
- writer;
- Safety/OMS execution authority;
- `OrderIntent` authority;
- LIVE.

La eventual decisión PAPER-candidate será un hito posterior y separado.

## Deuda R7D / Auto-Paper
Machine-readable: `knowledge/00_CANON/debt_register_r7d_auto_paper.json`.

- `TD-R7D-001` P1 — total execution-cost continuity Research -> PAPER;
- `TD-R7D-002` P1 — fee-complete execution accounting antes de profitability/Auto-Paper claims;
- `TD-R7D-003` P2 — remaining-quantity reservation segura después de partial fills.

Además permanecen strategy-version y shadow/forward binding como blockers explícitos.

## Modelo de automatización
La IA puede generar hipótesis, variantes, experimentos y análisis. No puede tener un camino directo a POST.

Una estrategia que opere automáticamente deberá estar:
- versionada/fingerprinted;
- reproducible;
- promovida por evidencia research/forward;
- acotada por permiso determinista;
- evaluada por Portfolio + Capital Safety;
- stageada por OMS;
- enviada por writer PAPER one-shot;
- reconciliada contra broker truth;
- vigilada por Health/kill switch/loss limits.

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
- W79/W80 intentando crear `OrderIntent` o llamar broker/Safety/OMS;
- Strategy Lab intentando POST, mutar SQLite, persistir credenciales o sintetizar gates;
- strategy version drift;
- IA/model output intentando saltar Strategy Runtime/Safety/OMS;
- cualquier intento LIVE.

## Capital
Existe exposición PAPER derivada del first canary y PR #49 mantiene su tratamiento como obligación operacional separada. W78/W79 no envían órdenes externas y W80 tampoco podrá hacerlo.

**LIVE TRADING: BLOQUEADO.**
