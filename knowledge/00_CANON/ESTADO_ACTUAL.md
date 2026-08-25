# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-24

## Estado global
R0–R5 permanecen formalmente certificados en el machine debt register principal. R6 broker-truth y W78–W85 son certificaciones técnicas posteriores e independientes.

W85 PAPER Candidate Admission / Probation Gate está **técnicamente certificado** sobre:

`c66855455dac4955a9d89994135f11c0a2c6da59`

La documentación canónica de cierre es un descendiente documentation-only y debe pasar su propia recertificación exact-head antes de declarar el cierre canónico definitivo.

## W85 — verdad técnica vigente
W85 introduce por primera vez una autoridad explícita de **candidate admission** separada de toda ejecución:

`W79→W84 exact certified chain`
`-> frozen admission policy`
`-> admission-time W84 canonical source re-proof`
`-> durable PaperCandidateAdmissionReceipt`
`-> append-only lifecycle`
`-> final admission verification V2`
`-> final candidate eligibility V2`.

### Admission no es execution
Un receipt W85 PASS puede marcar:

`paper_candidate_authorized = True`

pero debe conservar siempre:
- `paper_execution_authorized = False`;
- `external_execution_authorized = False`;
- `runtime_execution_authorized = False`;
- capital authority `NONE`;
- LIVE `BLOCKED`.

`probation_notional_cap_usd` y demás probation descriptors son límites de contención, no sizing ni capital disponible.

## Admission-time source re-proof
La revisión encontró que reutilizar un `PromotionShadowForwardFinalVerification` W84 histórico como si su `process_verified_at` siguiera demostrando frescura actual sería incorrecto.

Por eso `paper_candidate_admission_source_verification.py` vuelve a ejecutar el **finalizador canónico W84** contra las mismas fuentes R5/measurement durables antes de acuñar admisión.

El proof liga:
- finalización histórica W84;
- nueva finalización canónica W84;
- exact W83 identity/binding;
- plan/runtime de measurement;
- durable source capture;
- admission-time W85 clock;
- proof hash V2.

La regla permanente es:

`historical_finalization_timestamp_trusted_for_freshness = False`.

## Final verification / final eligibility V2
`PaperCandidateAdmissionFinalVerification V2` ya no calcula freshness desde `w84_finalization.process_verified_at` histórico. Usa la provenance durable que quedó dentro del admission receipt y exige el exact source-proof hash.

`PaperCandidateFinalEligibility V2` coteja ese source-proof contra la durable admission truth y el lifecycle vigente. Un objeto coherentemente rehasheado con provenance diferente falla cerrado.

## Lifecycle
La admisión es temporalmente finita.

Suspensión/revocación/expiración no crean autoridad adicional.

`REINSTATE` sólo puede reactivar administrativamente la misma admisión dentro de su ventana original. No puede extender `valid_until`, renovar evidence, cambiar policy ni ampliar probation envelope.

## Permanent W85 safety/authority contract
- no broker/network/credentials authority;
- no OMS/Safety/writer authority;
- no `OrderIntent(` construction;
- no broker POST/cancel/replace;
- no direct use del W84 intermediate source verifier como final downstream authority;
- canonical W84 finalizer rerun obligatorio en admission time;
- historical W84 timestamp nunca es freshness authority W85;
- source-proof hash debe llegar hasta final eligibility;
- PAPER candidate sólo puede ser true para un exact W85 PASS íntegro;
- PAPER execution siempre false en W85;
- capital authority NONE;
- LIVE BLOCKED.

## Certificación técnica W85
Dedicated W85 run `32803158593`: **SUCCESS**.
- **67/67 W85 PASS**;
- W85 boundary PASS;
- W84/W83/W82/W81/W80/W79/W78 + Research Authority PASS.

Core Safety run `32803158524`: **SUCCESS**.
- **3134/3134 PASS**;
- exact coverage `85.02334230099699%`;
- `paper_candidate_admission_source_verification.py`: 88%;
- `paper_candidate_admission_final_verification.py`: 77%;
- `paper_candidate_eligibility_final.py`: 81%;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- Research/R5/R6/R7/W78-W85 boundaries PASS;
- Debt Register PASS: 60 items / 9 open / 8 blocking on uncertified tracks;
- Canonical Knowledge PASS.

## Estado operacional actual
La certificación W85 prueba la implementación con fixtures/evidence de prueba. No existe evidencia aquí de un `PaperCandidateAdmissionReceipt` operacional PASS vigente.

Por eso el estado actual sigue siendo:

**PAPER CANDIDATE ACTUAL: FALSE.**
**PAPER EXECUTION AUTHORIZED: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**

## Próximo trabajo
W86 — **PAPER Runtime Readiness Gate**.

W86 debe consumir sólo una elegibilidad W85 final, íntegra y vigente y probar que el entorno PAPER actual está preparado, sin acuñar ejecución.

La frontera mínima incluye:
- W85 candidate ACTIVE/vigente;
- exact PAPER account/environment identity;
- fresh account/Portfolio/market truth;
- product/venue/currency/symbol compatibility;
- strategy/runtime identity continuity;
- open-order/exposure conflicts;
- kill switch / Health / Safety prerequisites;
- broker minimum vs W85 envelope compatibility;
- internal freshness budgets;
- hash-bound finite readiness decision;
- no sizing derivado automáticamente del probation envelope;
- no OrderIntent/OMS/broker-write/capital/LIVE authority.

Una eventual execution authority debe ser una capa posterior explícita.

## Deuda y tracks independientes
- `TD-R7D-001`: CLOSED;
- `TD-R7D-002`: CLOSED;
- `TD-R7D-003`: **OPEN P2**;
- R7 real PAPER close en PR #49 permanece operacionalmente separado;
- stack científico/admission W78–W85 permanece apilado y DRAFT en orden.

## Cadena futura
`Research -> Promotion Evidence -> Durable Assessment -> Economic Qualification -> Strategy Version Binding -> Shadow/Forward Final Verification -> PAPER Candidate Admission -> PAPER Runtime Readiness -> [future explicit execution authority] -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`.

IA/model output no puede saltarse esa cadena.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**
**PAPER CANDIDATE ACTUAL: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
