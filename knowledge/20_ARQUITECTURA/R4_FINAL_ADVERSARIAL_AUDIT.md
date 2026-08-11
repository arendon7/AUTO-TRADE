# R4 FINAL ADVERSARIAL AUDIT

Estado: **FINAL SCAN CLEAN — TRACK CERTIFICATION PENDING / NO MERGE AÚN**

Branch: `reconstruction/r4-portfolio-health`
Base: R3 post-merge certificado `c585a84b5197076b210723bb70980b828e4e3026`
Head inmediatamente anterior a esta nota: `1df2435950239e7dc0ab25b4d0ff0fcbed9e07e6`

## Condiciones del scan final
Verificadas antes de este commit humano:
1. **Cero P0/P1/P2 OPEN con `track=R4`** en `knowledge/00_CANON/debt_register.json`.
2. Todas las capacidades requeridas R4 están `PASS` en `RECONSTRUCTION_V028R_MATRIX.md`:
   - authoritative Instrument Master;
   - versioned Portfolio State / reconciliation infrastructure;
   - correlation-aware portfolio research;
   - allocation perturbation + leave-one-out;
   - TRAIN-calibrated regimes;
   - Strategy/Portfolio Health & Drift;
   - Defensive Health Bridge;
   - deterministic Portfolio Manager / sizing + cross-strategy budgets.
3. No queda ningún workflow temporal `r4-*` bajo `.github/workflows/`.
4. No queda ningún helper temporal `r4_*` bajo `scripts/`.
5. Los workflows `r2-*` aún presentes son heredados del árbol base y no son temporales creados por R4.
6. La búsqueda de `paper-api.alpaca.markets`, `api.alpaca.markets`, `submit_order`, `api_key`, `secret_key` y autoridad LIVE externa no encontró nuevas rutas de ejecución R4.
7. `TD-OPS-001` Graphify permanece OPEN P3/OPS y no es deuda bloqueante del track R4.
8. PR #11 sigue DRAFT; R4 todavía no está en `certified_tracks`.

## Fronteras adversariales cerradas
- Instrument Master -> Portfolio Manager: stale/halted/unknown/conflicting rules, tick/step/min/max y no-upsizing.
- Dependence -> sizing: universo exacto, estrategia/cluster/portfolio budgets y normalización Decimal exacta.
- Robustness -> sizing: recomputación base, post-Health y post-venue; evidencia serializada no es autoautoridad.
- Health -> Defensive Bridge: baseline/policy binding, namespaces STRATEGY/PORTFOLIO, stale/future/missing evidence y worsening monotónico.
- Defensive Bridge -> Safety: REDUCED/NO_NEW_RISK solo mantiene o reduce capacidad; exits realmente risk-reducing permanecen posibles bajo Safety.
- Safety -> OMS: `safety_state.version` invalida approvals anteriores y OMS revalida Health al submit.
- Portfolio Manager authority: CI prohíbe OMS/broker/engine, `OrderIntent`/`RiskDecision` y llamadas de ejecución.
- Recovery durability: `recovery_id` retry-safe, overlay Health autoritativo y ACK history tamper-evident.

## Hallazgos tardíos cerrados con evidencia
### TD-R4-012 — recovery acknowledgement idempotency — CLOSED
- `recovery_id` requerido en Health y Defensive Bridge.
- same request replay = no-op; conflicting reuse = fail closed.
- retry no relaja dos niveles ni vuelve a incrementar `safety_state.version`/ledger.
- Certificado: `knowledge/60_EVIDENCE/R4_RECOVERY_IDEMPOTENCY_CERTIFICATION.json`.

### TD-R4-013 — authoritative unsynced Health overlay — CLOSED
- cada `effective_control()` contrasta bridge + Health autoritativo actual;
- worsening más nuevo endurece inmediatamente aun sin sync;
- recovery más nuevo no relaja por lectura;
- Safety y OMS heredan el overlay.
- Certificado: `knowledge/60_EVIDENCE/R4_AUTHORITATIVE_HEALTH_OVERLAY_CERTIFICATION.json`.

### TD-R4-014 — Health recovery ACK tamper-evidence — CLOSED
- `health_recovery_acks_v3`: `ack_seq`, `previous_ack_hash`, `ack_hash`;
- `recovery_ack_head` forma parte del fingerprint/hash de `HealthControlState`;
- `get()`, assessment y recovery verifican la cadena completa;
- delete/mutation/gap/reorder/head mismatch fallan cerrados;
- ACK HEALTHY versiona evidencia aunque no cambie severidad;
- evidencia pre-chain existente exige migración/rebaseline explícito.
- Certificado: `knowledge/60_EVIDENCE/R4_HEALTH_ACK_CHAIN_CERTIFICATION.json`.

## Último CI completo anterior al cierre documental 014
Head `5297610d2ed460755c24c13663516c0a05d261e3`:
- **480 tests PASS**;
- **86.58% coverage**;
- Contract Registry: 10 PASS;
- Research/Advisory Authority: PASS;
- Debt Register: PASS;
- Knowledge Contract: PASS.

Ese CI certificó el repair técnico TD-R4-014. El presente commit humano incluye además su cierre documental y el scan final limpio, por lo que **requiere su propio CI completo antes de crear `R4_CERTIFICATION.json`**.

## Estado de capital
- External PAPER authority añadida por R4: **NONE**.
- LIVE authority: **NONE / BLOCKED**.
- Portfolio Manager: advisory capacity only.
- Health automation: reduce/block only.

## Próximo gate
1. Ejecutar Core Safety + Knowledge Contract sobre este head humano.
2. Si ambos quedan verdes y cobertura >=85%, crear `knowledge/60_EVIDENCE/R4_CERTIFICATION.json` ligado a este SHA exacto.
3. Solo después añadir `R4` a `certified_tracks`, sincronizar canon hacia R5 y volver a ejecutar un último CI de branch.
4. Solo con ese último CI verde: sacar PR #11 de DRAFT, merge por squash y recertificar el SHA exacto de `main` antes de crear/abrir R5.
