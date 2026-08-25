# ADR-0017 — W85 PAPER Candidate Admission / Probation Gate

Fecha: 2026-08-24
Estado: **ACCEPTED / TECHNICAL IMPLEMENTATION CERTIFIED / CANONICAL DOCUMENTATION RECERTIFICATION PENDING**

## Contexto
W79–W84 cerraron progresivamente la cadena científica y económica previa a cualquier promoción operativa:

`W79 promotion governance`
`-> W80 durable assessment`
`-> W81 execution-cost continuity`
`-> W82 fee-complete deterministic qualification`
`-> W83 execution strategy-version binding`
`-> W84 source-authoritative + process-clock Shadow/Forward final verification`.

W84 termina deliberadamente en `PromotionShadowForwardFinalVerification` y mantiene:
- `paper_candidate_authorized = False`;
- `paper_execution_authorized = False`;
- capital authority `NONE`;
- LIVE `BLOCKED`.

Era necesario introducir una capa explícita entre evidencia científicamente/económicamente calificada y cualquier futura ejecución PAPER. El objetivo W85 es responder una pregunta más estrecha:

> ¿Puede esta exact chain certificada ser admitida como **PAPER candidate** durante una ventana finita y bajo una policy congelada, sin conceder todavía ninguna autoridad para enviar órdenes?

La revisión de W85 encontró dos trust gaps adicionales que debían cerrarse antes de considerar segura esa admisión:

1. una finalización histórica W84 correctamente auto-hasheada no debía ser reutilizada indefinidamente como prueba de frescura actual;
2. una final verification W85 no podía volver a derivar frescura desde el timestamp histórico `W84.process_verified_at`, porque eso reintroduciría la misma confianza temporal que el admission-time re-proof pretendía eliminar.

## Decisión
W85 se cierra como una cadena de cinco piezas separadas:

`exact W79→W84 final certified chain`
`-> frozen PaperCandidateAdmissionPolicy`
`-> admission-time canonical W84 source re-proof`
`-> durable PaperCandidateAdmissionReceipt`
`-> append-only lifecycle`
`-> PaperCandidateAdmissionFinalVerification V2`
`-> PaperCandidateFinalEligibility V2`.

La decisión fundamental se conserva:

`EVIDENCE_QUALIFIED != PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED`.

## 1. Frozen PAPER candidate admission policy
`src/autotrade/paper_candidate_admission.py` define una `PaperCandidateAdmissionPolicy` candidate-specific y hash-bound.

La policy gobierna únicamente condiciones de admisión y contención, entre ellas:
- identidad exacta de candidate/campaign/policy;
- freshness máxima para admisión;
- ventana finita de validez del candidate;
- probation descriptors y límites superiores explícitos;
- semántica de status fail-closed.

W85 deliberadamente **no introduce nuevos thresholds científicos de performance** después de observar DEVELOPMENT/HOLDOUT. Los thresholds científicos continúan siendo los preregistrados en W79. Esto evita post-outcome threshold shopping.

Los probation descriptors tampoco son capital authority ni sizing executable. Un valor como `probation_notional_cap_usd` es un techo descriptivo de contención para una futura capa; W85 no puede convertirlo en `OrderIntent`, presupuesto disponible, permiso OMS ni autorización de broker.

## 2. Durable PAPER candidate admission receipt
`SQLitePaperCandidateAdmissionRegistry` persiste una decisión durable, append-only/hash-bound en el SQLite core autoritativo.

La admisión conserva provenance exacta de la cadena W79–W84 y exige identidad consistente del candidate, policy, strategy/runtime y evidence chain.

La semántica incluye estados explícitos equivalentes a:
- `PASS`;
- `FAIL`;
- `BLOCKED`;
- `INCOMPLETE`.

Sólo un `PASS` íntegro puede producir `paper_candidate_authorized = True`.

Incluso en `PASS` permanecen obligatoriamente:
- `paper_execution_authorized = False`;
- `external_execution_authorized = False`;
- `runtime_execution_authorized = False`;
- capital authority `NONE`;
- LIVE `BLOCKED`.

La registry implementa idempotency/conflict semantics y rechaza duplicate/cross-campaign/cross-policy misuse.

## 3. Admission-time W84 canonical source re-proof V2
`src/autotrade/paper_candidate_admission_source_verification.py` introduce:

- `W84AdmissionSourcePackage`;
- `W84AdmissionSourceProof`;
- `verify_w84_sources_for_candidate_admission(...)`.

W85 **no consume directamente** el `PromotionShadowForwardSourceVerification` intermedio de W84. En su lugar vuelve a ejecutar el **finalizador canónico W84**:

`finalize_promotion_shadow_forward_resolution(...)`.

Ese rerun:
1. relee R5 Shadow/Forward durable truth;
2. revalida los measurement receipts;
3. vuelve a aplicar las invariantes W84;
4. usa el reloj interno del finalizador W84;
5. produce una nueva finalización canónica sobre las mismas fuentes.

W85 compara esa nueva finalización con la finalización histórica W84 para exigir que no hayan cambiado:
- base resolution;
- evidence hash;
- policy hash;
- W83 resolution/binding;
- measurement plan/runtime;
- measurement capture;
- resolved/remaining blocker sets.

El source proof W85 liga dos relojes diferentes de forma explícita:
- `canonical_finalization_verified_at`: reloj interno del rerun W84;
- `verified_at`: reloj interno de decisión/admisión W85.

La frescura W85 se deriva de:

`verified_at - durable source_capture_at`.

Y exige explícitamente:

`historical_finalization_timestamp_trusted_for_freshness = False`.

Por tanto `W84.process_verified_at` histórico no es fuente de frescura para W85.

## 4. Lifecycle finito y no renovable implícitamente
`src/autotrade/paper_candidate_admission_lifecycle.py` separa la admisión inicial de su estado administrativo posterior.

La admisión tiene una ventana finita `valid_until`. Suspensión/revocación/expiración se proyectan sin otorgar autoridad adicional.

`REINSTATE` significa únicamente reanudación administrativa de la **misma admisión** dentro de su ventana original. No puede:
- extender `valid_until`;
- renovar evidence;
- alterar policy;
- incrementar probation budget;
- acuñar una nueva admisión;
- otorgar PAPER execution authority.

Si la ventana expiró o la evidence requiere una nueva decisión, debe existir un nuevo proceso de admisión; no se recicla el receipt anterior.

## 5. Final Admission Verification V2
`src/autotrade/paper_candidate_admission_final_verification.py` endurece la frontera final después de la admisión durable.

La V2 exige que la final verification esté ligada al exact source-proof W85 y deriva la edad desde:

`receipt.w84_admission_source_capture_at -> receipt.admitted_at`.

No usa `w84_finalization.process_verified_at` histórico como autoridad de frescura.

La verificación final conserva explícitamente:
- source-proof hash;
- admission-time source capture;
- admission-time verified clock;
- candidate/policy/provenance identity;
- no execution/capital/LIVE escalation.

Un objeto final coherentemente rehasheado pero con provenance distinta falla cerrado.

## 6. Final PAPER Candidate Eligibility V2
`src/autotrade/paper_candidate_eligibility_final.py` proyecta la elegibilidad final de candidate sin convertirse en execution gate.

La proyección exige coincidencia exacta entre:
- durable admission receipt;
- lifecycle truth;
- final admission verification V2;
- W85 source-proof hash/provenance.

No basta con presentar un `admission_hash` válido. La provenance source-proof debe coincidir con la durable truth exacta.

La salida puede expresar candidate eligibility, pero sigue sin autoridad para:
- construir `OrderIntent`;
- consultar o escribir broker;
- pasar a OMS/Safety;
- reservar capital;
- habilitar runtime execution;
- habilitar LIVE.

## 7. Permanent W85 boundary
`scripts/check_w85_paper_candidate_admission_boundary.py` está cableado tanto al Dedicated W85 workflow como a Core Safety.

El boundary exige, entre otras cosas:
- contrato W85 V2 para source proof/final verification/final eligibility;
- canonical W84 finalizer rerun antes de admisión;
- prohibición de consumir el verifier intermedio W84 como authority downstream;
- `historical_finalization_timestamp_trusted_for_freshness = False`;
- ausencia de freshness derivada desde el historical `w84_finalization.process_verified_at`;
- source-proof hash ligado hasta final eligibility;
- no broker/network/credentials/OMS/Safety/paper-close/writer authority;
- no `OrderIntent(` construction;
- no PAPER execution authority;
- capital authority `NONE`;
- LIVE `BLOCKED`.

El Dedicated W85 vuelve a ejecutar además los boundaries heredados W84→W78 y Research Authority.

## 8. Adversarial coverage
La suite W85 cubre, entre otros:
- missing/tampered W84 final truth;
- policy/candidate/campaign identity mismatch;
- stale admission inputs;
- idempotent retry vs conflicting duplicate;
- durable receipt/side-column/hash tamper;
- lifecycle suspend/revoke/expire/reinstate constraints;
- authority escalation attempts;
- canonical W84 rerun disabled o incompleto;
- incomplete durable measurement package;
- clock causality negativa;
- historical timestamp reuse;
- source-proof hash/age tamper;
- rehash-valid provenance drift;
- final verification/source proof disagreement;
- final eligibility disagreement with durable receipt;
- broker/OMS/Safety/LIVE boundary violations.

No se bajó el floor de coverage ni se excluyó W85 para alcanzar certificación.

## 9. Technical certification
Technical exact head:

`c66855455dac4955a9d89994135f11c0a2c6da59`

Dedicated W85 run `32803158593`: **SUCCESS**.
- **67/67 W85 PASS**;
- W85 permanent boundary PASS;
- W84/W83/W82/W81/W80/W79/W78 boundaries PASS;
- Research Authority PASS.

Core Safety run `32803158524`: **SUCCESS**.
- **3134/3134 PASS**;
- exact measured coverage `85.02334230099699%` >= 85%;
- W85 source verification: 88%;
- W85 final admission verification: 77%;
- W85 final eligibility: 81%;
- Contract Registry: 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- Research/R5/R6/R7/W78–W85 boundaries PASS;
- Debt Register PASS: 60 items / 9 open / 8 blocking on uncertified tracks;
- Canonical Knowledge PASS.

Knowledge Contract para ese technical head: PASS.

El commit documental que contiene este ADR es un descendiente documentation-only y debe recertificarse exact-head. Los run IDs finales de esa recertificación deben registrarse en la verdad del PR #57 para evitar autorreferencia infinita dentro del ADR.

## 10. Qué W85 sí y no certifica
W85 certifica la **capacidad del sistema** para emitir una admisión PAPER candidate durable y fail-closed cuando una exact chain real satisface todas las condiciones.

La suite usa fixtures/evidence de prueba. Por tanto la certificación de código **no implica que exista actualmente un candidate operacional admitido**.

Hasta que la autoridad operacional real contenga un receipt W85 PASS vigente:

**PAPER CANDIDATE ACTUAL: FALSE.**

Incluso si en el futuro existe un PAPER candidate PASS:

**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**

## 11. Consecuencias para W86
La siguiente wave debe ser **W86 PAPER Runtime Readiness Gate**.

W86 debe consumir únicamente una elegibilidad/admisión W85 final íntegra y vigente y responder una pregunta distinta:

> ¿Está el entorno PAPER actual preparado, fresco, consistente y dentro del envelope para considerar una futura ejecución?

W86 no debe convertir automáticamente:
- `probation_notional_cap_usd` en sizing;
- candidate admission en broker POST;
- evidence qualification en capital authority.

Como mínimo W86 deberá fail-close ante:
- candidate no vigente/suspendido/revocado/expirado;
- broker/account identity mismatch;
- stale account/portfolio/market truth;
- product/venue incompatibility;
- broker minimum incompatible con el envelope W85;
- kill switch o Health/Safety no aptos;
- open-order/exposure conflict;
- strategy/runtime identity drift;
- fee/cost provenance stale si corresponde;
- cualquier intento de acuñar ejecución desde readiness.

La autoridad de ejecución, si alguna vez se diseña, debe seguir siendo otra frontera explícita posterior a readiness y pasar por Capital Safety + OMS + one-shot writer + reconciliation.

## 12. Deuda y tracks separados
- `TD-R7D-001`: CLOSED;
- `TD-R7D-002`: CLOSED;
- `TD-R7D-003`: **OPEN P2** — partial-fill remaining-quantity reservation;
- R7 PR #49 sigue siendo el track operativo separado para cerrar la exposición PAPER residual existente;
- W85 no modifica ni autoriza reintentos R7.

No se realizó ningún broker POST como parte de W85.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**
**PAPER CANDIDATE ACTUAL: FALSE.**
**PAPER EXECUTION AUTHORIZED: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
