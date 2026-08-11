# R4 FINAL ADVERSARIAL AUDIT

Estado: **FINAL GATE PENDING — NO CERTIFICAR / NO MERGE AÚN**

Branch: `reconstruction/r4-portfolio-health`
Base: R3 post-merge certificado `c585a84b5197076b210723bb70980b828e4e3026`
Último head técnico antes de esta nota: `fb6c5f252819b0aaff66588f4008c6509791afff`

## Regla de cierre
R4 solo puede certificarse cuando:
1. no exista P0/P1/P2 OPEN de R4 en `debt_register.json`;
2. todas las filas R4 requeridas de la matriz estén `PASS`;
3. Core Safety y Knowledge Contract estén verdes sobre el mismo head de cierre;
4. cobertura total se mantenga >=85%;
5. Research/Advisory Authority Boundary esté verde;
6. no queden one-shot workflows/helpers R4 temporales;
7. LIVE/PAPER externo permanezcan sin nueva autoridad.

## Fronteras atacadas
- Instrument Master -> Portfolio Manager: stale/halted/unknown/conflicting rules, tick/step/min/max y no-upsizing.
- Dependence -> sizing: universe identity, strategy/cluster/portfolio budgets y exact Decimal normalization.
- Robustness -> sizing: base, post-Health y post-venue recomputation; serialized evidence no es autoautoridad.
- Health -> Defensive Bridge: baseline/policy binding, entity namespace, stale/future/missing evidence, monotone worsening.
- Defensive Bridge -> Safety: NO_NEW_RISK / REDUCED solo puede mantener o reducir capacidad; true risk reduction permanece posible.
- Safety -> OMS: `safety_state.version` invalida approvals anteriores y OMS revalida Health al submit.
- Portfolio Manager authority: CI prohíbe OMS/broker/engine/OrderIntent/RiskDecision y llamadas de ejecución.
- Recovery durability: idempotency, current authoritative overlay y tamper-evident ACK history.

## Hallazgos tardíos registrados antes de reparar
### TD-R4-012 — recovery acknowledgement idempotency — CLOSED con evidencia
Hallazgo: un retry del mismo acknowledgement podía ejecutar dos relajaciones consecutivas.
Repair certificado:
- `recovery_id` requerido en Health y Defensive Bridge;
- request fingerprint durable ligado a actor + evidencia;
- same-request replay = no-op;
- conflicting ID reuse = fail closed;
- duplicate bridge replay no vuelve a incrementar `safety_state.version` ni crea un segundo recovery ledger event.

Certificado: `knowledge/60_EVIDENCE/R4_RECOVERY_IDEMPOTENCY_CERTIFICATION.json`.

### TD-R4-013 — authoritative unsynced Health overlay — CLOSED con evidencia
Hallazgo: un Health state autoritativo podía empeorar antes de que el job de sync actualizara la proyección del bridge.
Repair certificado:
- cada `effective_control()` contrasta bridge + Health autoritativo actual;
- missing/stale/future/backward/conflicting authoritative Health falla cerrado;
- un Health más estricto y más nuevo se aplica inmediatamente aunque aún no se haya sincronizado;
- una recuperación más nueva nunca relaja por lectura: conserva el bridge más estricto hasta recovery/sync explícito;
- Safety y OMS heredan el overlay porque ambos consultan `effective_control()`.

Certificado: `knowledge/60_EVIDENCE/R4_AUTHORITATIVE_HEALTH_OVERLAY_CERTIFICATION.json`.

### TD-R4-014 — Health recovery ACK tamper-evidence — P1 OPEN hasta este CI final
Hallazgo: `recovery_id` era durable e idempotente, pero su fila ACK no estaba anclada al hash de `HealthControlState`. La desaparición/corrupción de esa fila podía degradar la protección de replay bajo el mismo modelo de corrupción durable que ya auditamos en Portfolio State/Fills.

Repair aplicado en el árbol técnico:
- cadena `health_recovery_acks_v3` con `ack_seq`, `previous_ack_hash` y `ack_hash`;
- `recovery_ack_head` forma parte del fingerprint/hash durable de `HealthControlState`;
- `get()`, `apply_assessment()` y `acknowledge_recovery()` verifican la cadena completa antes de continuar;
- eliminación, mutación, gap/reorder y mismatch de head fallan cerrados;
- un ACK HEALTHY que no cambia severidad sí versiona evidencia para anclar su `recovery_id`;
- estado/ACK pre-chain con evidencia existente no se migra silenciosamente: exige migración/rebaseline explícito.

Pruebas: `tests/test_r4_health_ack_chain_integrity.py` + regresiones Health/Bridge/overlay/idempotency.

## Última evidencia verde anterior
Head `32584b277febb5680038c9c6e06379f99e648cdb`:
- 472 tests PASS;
- 86.42% coverage;
- Contract Registry 10 PASS;
- Research/Advisory Authority PASS;
- Debt Register PASS;
- Knowledge Contract PASS.

Ese head certificó 012/013, pero es anterior al repair 014 y por tanto **no** basta para certificar R4.

## Estado de capital
- External PAPER authority: **NO añadida por R4**.
- LIVE authority: **NONE / BLOCKED**.
- Portfolio Manager: advisory capacity only.
- Health automation: reduce/block only.

## Próximo gate
Ejecutar CI completo sobre el head humano que contiene esta nota y el ACK-chain. Si falla, `TD-R4-014` permanece OPEN. Si queda verde: emitir certificado 014, cerrar la deuda, comprobar cero P0/P1/P2 R4 abiertos + todas las filas R4 PASS + ausencia de helpers temporales, y recién entonces generar `R4_CERTIFICATION.json`.
