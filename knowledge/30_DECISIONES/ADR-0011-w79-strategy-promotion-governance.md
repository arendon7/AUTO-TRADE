# ADR-0011 — W79 Strategy Promotion Governance y Strategy Lab read-only

Fecha: 2026-08-23
Estado: **ACCEPTED / W79 behavioral implementation CERTIFIED; no concede PAPER candidate ni autoridad de ejecución**

## Contexto
W78 añadió una capa determinista, sin red y sin writer, para medir sensibilidad de ejecución reutilizando el control plane canónico. El siguiente problema no era ejecutar más órdenes, sino evitar que una estrategia pudiera reinterpretarse como promovida después de observar HOLDOUT o execution sensitivity.

W79 introduce una frontera explícita entre:
1. diseño científico y preregistro de thresholds;
2. selección del candidato en DEVELOPMENT;
3. congelación de identidad del candidato antes del HOLDOUT final;
4. evaluación de evidencia;
5. cualquier futura decisión de autoridad PAPER.

La decisión central es que **evidencia de promoción y autoridad de capital son dominios distintos**.

## Decisión 1 — preregistro en dos etapas

### Etapa A — thresholds antes de DEVELOPMENT
Antes de ejecutar la campaña DEVELOPMENT se congela `StrategyPromotionThresholdPolicy`, incluyendo como mínimo:
- DEVELOPMENT campaign id;
- HOLDOUT campaign id separado;
- HOLDOUT trial id esperado;
- máximo Holm adjusted p;
- mínimo HOLDOUT net return;
- máximo HOLDOUT drawdown;
- mínimo HOLDOUT fills;
- mínimo execution fill ratio;
- máximo adverse slippage bps permitido.

La política es hash-bound y append-only.

### Etapa B — candidato después de DEVELOPMENT y antes de HOLDOUT
Después de que un Tournament DEVELOPMENT completo seleccione exactamente un candidato, se congela `StrategyPromotionPolicy` con:
- selected trial id;
- strategy id;
- strategy version;
- trial fingerprint;
- tournament fingerprint;
- threshold policy binding;
- DEVELOPMENT/HOLDOUT campaign binding.

El candidato debe quedar congelado antes de preregistrar/ejecutar el trial HOLDOUT final. No se permite escoger retrospectivamente el candidato después de ver HOLDOUT.

## Decisión 2 — una sola autoridad SQLite
Threshold policy, candidate policy y trial ledger deben compartir el mismo runtime SQLite autoritativo. Las escrituras de governance usan transacciones `BEGIN IMMEDIATE` y semántica append-only/idempotente por identidad.

No se admite una segunda base de datos paralela que permita reescribir la historia de selección.

## Decisión 3 — gates W79
El set canónico de gates W79 es:
- `DEVELOPMENT_SELECTION`;
- `EXECUTION_SENSITIVITY`;
- `FINAL_HOLDOUT`;
- `MULTIPLE_TESTING`.

Los estados admitidos son `PASS`, `FAIL`, `MISSING`, `BLOCKED`.

Un `StrategyPromotionEvidenceView` puede quedar `EVIDENCE_QUALIFIED`, pero W79 mantiene permanentemente:
- `paper_candidate_authorized=false`;
- `external_execution_authorized=false`;
- `live_trading=BLOCKED`.

Por tanto:

`EVIDENCE_QUALIFIED != PAPER_CANDIDATE`.

## Decisión 4 — blockers explícitos
W79 no puede eliminar ni reinterpretar los blockers siguientes:
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `FEE_ACCOUNTING_INCOMPLETE`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN`.

Mientras cualquiera permanezca abierto, no existe promotion authority hacia Auto-Paper.

## Decisión 5 — Strategy Lab es una proyección read-only
La experiencia Mac incorpora `/strategy-lab` únicamente como superficie de inspección.

El read model:
- abre `core.sqlite3` con SQLite URI `mode=ro`;
- activa `PRAGMA query_only=ON`;
- no crea tablas;
- no hace INSERT/UPDATE/DELETE;
- no usa credenciales;
- no consulta broker;
- no expone POST;
- no entra en `SAFE_ACTIONS`;
- no construye `OrderIntent`;
- no concede Capital Safety/OMS authority;
- no persiste resultados de gates.

Mientras no exista assessment durable, la UI debe declarar literalmente:

`gate_evidence_state=NOT_PERSISTED_BY_W79`.

No se permite sintetizar un PASS visual a partir de datos parciales.

## Decisión 6 — separación Research -> Execution -> Authority
La cadena de producto queda conceptualmente separada:

`Research evidence -> W79 promotion governance -> future PAPER candidate decision -> Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

Ni la IA ni Strategy Lab tienen un atajo hacia el writer.

## Boundaries permanentes
W79 debe fallar CI si aparece cualquiera de los siguientes cambios no autorizados:
- import/call de broker writer desde promotion governance;
- red, socket, Alpaca host o credenciales;
- creación de `OrderIntent` como acción de W79;
- Safety/OMS/TradingPipeline authority;
- Strategy Lab aceptado por `do_POST`;
- almacenamiento de credenciales en Strategy Lab;
- `localStorage`/`sessionStorage` para evidencia autoritativa;
- mutación SQLite desde el read model;
- gate evidence inventada o sintetizada;
- PAPER candidate `true`;
- LIVE distinto de `BLOCKED`.

Estos boundaries viven en workflows W79 y Core Safety.

## Certificación
Behavioral implementation head: `c5c264e64e931ef380801b1e0d1508ea2cac0dfa`.

Sobre ese head:
- W79 dedicated workflow PASS;
- 73 pruebas W79 PASS;
- 19 pruebas Mac Control Center PASS;
- Core Safety 2844/2844 PASS;
- branch coverage `85.15094919501644%` con floor 85% PASS;
- W79 promotion boundary PASS;
- Strategy Lab read-only boundary PASS;
- Mac Control Center boundary PASS;
- W78 execution boundary PASS;
- Research authority PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

Esta certificación afirma la semántica y boundaries W79; no afirma profitability, PAPER candidate ni LIVE readiness.

## Consecuencias

### Positivas
- evita selección retrospectiva después del HOLDOUT;
- hace auditable la identidad exacta de la estrategia candidata;
- conserva una línea clara entre evidencia y permiso de capital;
- permite una UI útil sin ampliar broker authority;
- deja preparado W80: persistencia durable de assessment/evidence receipts.

### Costos
- W79 no produce un PAPER candidate;
- los resultados de gates todavía no están persistidos como assessment autoritativo;
- fee-complete accounting, total execution-cost continuity y shadow/forward binding siguen abiertos;
- el close real R7B continúa siendo un gate operativo independiente.

## Siguiente decisión requerida — W80
El siguiente bloque no es Auto-Paper. Debe cerrar primero la **persistencia durable y reproducible del assessment de promoción**, vinculando gates a evidencia inmutable sin conceder ejecución. Sólo después puede diseñarse una decisión PAPER-candidate separada y fail-closed.

**LIVE TRADING: BLOQUEADO.**
