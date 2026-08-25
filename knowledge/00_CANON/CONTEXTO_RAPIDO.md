# CONTEXTO RÁPIDO — AUTO-TRADE

Estado: **R0–R5 formalmente certified; R6 broker-truth cerrado; W78 execution qualification, W79 promotion governance, W80 durable assessments, W81 non-fee cost continuity, W82 fee-complete deterministic qualification, W83 execution strategy-version binding, W84 Shadow/Forward final verification y W85 PAPER Candidate Admission técnicamente certificados. Cierre canónico W85 en recertificación exact-head. W86 PAPER Runtime Readiness es el siguiente layer.**

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/debt_register.json`
5. `knowledge/00_CANON/debt_register_r7d_auto_paper.json`
6. `knowledge/30_DECISIONES/ADR-0010-w78-deterministic-paper-execution-model.md`
7. `knowledge/30_DECISIONES/ADR-0011-w79-strategy-promotion-governance.md`
8. `knowledge/30_DECISIONES/ADR-0012-w80-durable-promotion-assessment.md`
9. `knowledge/30_DECISIONES/ADR-0013-w81-execution-cost-continuity.md`
10. `knowledge/30_DECISIONES/ADR-0014-w82-fee-complete-execution-accounting.md`
11. `knowledge/30_DECISIONES/ADR-0015-w83-execution-strategy-version-binding.md`
12. `knowledge/30_DECISIONES/ADR-0016-w84-shadow-forward-promotion-binding.md`
13. `knowledge/30_DECISIONES/ADR-0017-w85-paper-candidate-admission.md`
14. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`

## Heads clave
- R6 first canary: `0cbb782015eeed200b9851b53764ac6389c3d9ff`;
- W78: `2924456e33c2cc9e6579301b176267513a90861f`;
- W79 canonical: `b96da018641ddbe6e4bdf8ba9c26642a5174f465`;
- W80 final branch head/base W81: `fb6cc382b4cfc36cb68e1612e7c29b040332ba2e`;
- W81 canonical: `0d042bfb80c9ae0f89de035b6638938f831a3cba`;
- W82 certified closure/base W83: `d33a99727d9f326a35612ffa39007b436fe76625`;
- W83 certified base W84: `0d177a1cfb16cffbb1266ee07865db5f77f1fe50`;
- W84 behavioral certified head: `f1ed0f675224c515f74a099ddb0beeefd9c96629`;
- W85 technical certified head: `c66855455dac4955a9d89994135f11c0a2c6da59`.

## W85 en una frase
W85 convierte una exact chain W79→W84 certificada en una **admisión PAPER candidate durable, finita y revocable**, pero sólo después de re-probar W84 contra source truth en admission time; candidate admission sigue sin conceder ejecución, capital, OMS, Safety, broker write ni LIVE.

## Salida W85
La frontera final se compone de:
- `PaperCandidateAdmissionReceipt` durable;
- lifecycle append-only;
- `PaperCandidateAdmissionFinalVerification V2`;
- `PaperCandidateFinalEligibility V2`.

El source proof W85 vuelve a ejecutar `finalize_promotion_shadow_forward_resolution(...)` y exige:

`historical_finalization_timestamp_trusted_for_freshness = False`.

La frescura final deriva del durable `source_capture_at` y del admission-time clock W85, no del `process_verified_at` histórico W84.

## Certificación técnica W85
- Dedicated `32803158593`: **67/67 PASS**;
- W85 permanent boundary PASS;
- inherited W84→W78 + Research PASS;
- Core `32803158524`: **3134/3134 PASS**;
- exact coverage `85.02334230099699%`;
- W85 source verification 88%; final admission verification 77%; final eligibility 81%;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- Research/R5/R6/R7/W78–W85 boundaries PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

## W85 lifecycle
`REINSTATE` no es una nueva admisión. Sólo puede reanudar la misma admisión dentro de su `valid_until` original. No extiende validez, no renueva evidence y no amplía probation envelope.

## Estado real de autoridad
Los tests prueban la capacidad de producir un PASS, no que hoy exista un receipt operacional PASS vigente.

- PAPER candidate actual: **FALSE**;
- PAPER execution authorized: **FALSE**;
- capital authority: **NONE**;
- broker write desde Research/promotion/admission: **NO**;
- broker-authoritative realized fee proof: FALSE;
- realized profitability authorized: FALSE;
- LIVE: **BLOCKED**.

## W86 — siguiente layer
W86 debe construir un **PAPER Runtime Readiness Gate** sobre una exact elegibilidad final W85 vigente.

Debe verificar, como mínimo:
- candidate ACTIVE y no expirado/revocado/suspendido;
- exact PAPER account/environment identity;
- account/Portfolio/market freshness;
- product/venue/currency/symbol compatibility;
- strategy/runtime identity;
- exposure/open-order conflicts;
- kill switch / Health / Safety prerequisites;
- broker minimum vs W85 probation envelope;
- temporal provenance y fail-closed semantics.

W86 tampoco puede conceder execution authority. Readiness sólo certifica que el entorno está apto para pasar a una frontera posterior y explícita.

## Lo que sigue abierto
- recertificación exact-head del cierre documental W85;
- W86 PAPER Runtime Readiness;
- futura execution-authority layer separada;
- `TD-R7D-003`: **OPEN P2** — partial-fill reservation;
- R7B real PAPER close operativo separado en PR #49.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**
**PAPER CANDIDATE ACTUAL: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
