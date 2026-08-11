# R4 FINAL ADVERSARIAL AUDIT

Estado: **PRECERTIFICATION — NO CERTIFICAR / NO MERGE AÚN**

Branch: `reconstruction/r4-portfolio-health`
Base: R3 post-merge certificado `c585a84b5197076b210723bb70980b828e4e3026`
Último head técnico antes de esta nota: `24a8f2092578f4cb9ee6f3d6a970824125ff6188`

## Regla de cierre
R4 solo puede certificarse cuando:
1. no exista P0/P1/P2 OPEN de R4 en `debt_register.json`;
2. todas las filas R4 requeridas de la matriz estén `PASS`;
3. Core Safety y Knowledge Contract estén verdes sobre el mismo head de cierre;
4. cobertura total se mantenga >=85%;
5. Research/Advisory Authority Boundary esté verde;
6. no queden one-shot workflows/helpers temporales;
7. LIVE/PAPER externo permanezcan sin nueva autoridad.

## Fronteras atacadas
- Instrument Master -> Portfolio Manager: stale/halted/unknown/conflicting rules, tick/step/min/max y no-upsizing.
- Dependence -> sizing: universe identity, strategy/cluster/portfolio budgets y exact Decimal normalization.
- Robustness -> sizing: base, post-Health y post-venue recomputation; serialized evidence no es autoautoridad.
- Health -> Defensive Bridge: baseline/policy binding, entity namespace, stale/future/missing evidence, monotone worsening.
- Defensive Bridge -> Safety: NO_NEW_RISK / REDUCED solo puede mantener o reducir capacidad; true risk reduction permanece posible.
- Safety -> OMS: `safety_state.version` invalida approvals anteriores y OMS revalida Health al submit.
- Portfolio Manager authority: CI prohíbe OMS/broker/engine/OrderIntent/RiskDecision y llamadas de ejecución.

## Hallazgos tardíos registrados antes de reparar
### TD-R4-012 — recovery acknowledgement idempotency — P1 OPEN hasta CI final
Hallazgo: un retry del mismo acknowledgement podía ejecutar dos relajaciones consecutivas.
Repair aplicado en el árbol técnico:
- `recovery_id` requerido en Health y Defensive Bridge;
- request fingerprint durable ligado a actor + evidencia;
- same-request replay = no-op;
- conflicting ID reuse = fail closed;
- duplicate bridge replay no vuelve a incrementar `safety_state.version` ni crea un segundo recovery ledger event.

Evidencia de prueba: `tests/test_r4_recovery_ack_idempotency.py` más regresiones Health/Bridge existentes.

### TD-R4-013 — authoritative unsynced Health overlay — P1 OPEN hasta CI final
Hallazgo: un Health state autoritativo podía empeorar antes de que el job de sync actualizara la proyección del bridge.
Repair aplicado en el árbol técnico:
- cada `effective_control()` contrasta bridge + Health autoritativo actual;
- missing/stale/future/backward/conflicting authoritative Health falla cerrado;
- un Health más estricto y más nuevo se aplica inmediatamente aunque aún no se haya sincronizado;
- una recuperación más nueva nunca relaja por lectura: conserva el bridge más estricto hasta recovery/sync explícito;
- Safety y OMS heredan el overlay porque ambos consultan `effective_control()`.

Evidencia de prueba: `tests/test_r4_authoritative_health_overlay.py` más integración Safety/OMS.

## Estado de capital
- External PAPER authority: **NO añadida por R4**.
- LIVE authority: **NONE / BLOCKED**.
- Portfolio Manager: advisory capacity only.
- Health automation: reduce/block only.

## Próximo gate
Ejecutar CI completo sobre el head humano que contiene esta nota + los repairs anteriores. Si falla, TD-R4-012/013 permanecen OPEN. Si queda verde, emitir certificados de ambos repairs, cerrar deudas y repetir un último gate limpio antes de certificar R4.
