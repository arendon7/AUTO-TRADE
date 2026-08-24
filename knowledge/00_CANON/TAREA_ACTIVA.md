# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-24

## Objetivo inmediato
**W85 — diseñar y demostrar una decisión explícita, durable y fail-closed de `PAPER_CANDIDATE` admission/probation que consuma la cadena W79→W84 certificada, sin convertir evidencia científica en broker write, sin saltarse Capital Safety/OMS y sin autorizar LIVE.**

## Stack actual
- R0–R5: tracks formalmente certificados; R5 sigue siendo el último track formal del machine debt register principal;
- R6 first real PAPER canary broker-truth: cerrado;
- R7B real PAPER close: PR #49, obligación operacional independiente;
- W78 deterministic execution qualification: certificado;
- W79 Strategy Promotion Governance + Strategy Lab read-only: certificado;
- W80 Durable Promotion Assessment: certificado;
- W81 Execution Cost Continuity: certificado; `TD-R7D-001` CLOSED;
- W82 Fee-Complete Execution Accounting: certificado; `TD-R7D-002` CLOSED;
- W83 Execution Strategy-Version Binding: certificado; `EXECUTION_STRATEGY_VERSION_UNBOUND` resuelto para la exact identidad ligada;
- W84 Shadow/Forward Promotion Binding: **behavioral + permanent-boundary certified; `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` resuelto para la exact identidad ligada**;
- W85 PAPER Candidate Admission / Probation Gate: **ACTIVE / NEXT IMPLEMENTATION WAVE**.

## W84 — cierre técnico
W84 cerró una discontinuidad que una R5 hash-chain íntegra no resolvía: `StrategyShadowObservation.source_fingerprint` podía ser un SHA-256 opaco sin demostrar que el retorno provenía del exact StrategySpec/runtime W83.

La continuidad certificada ahora es:

`exact W83 candidate/artifact/runtime -> frozen W84 measurement plan/policy -> deterministic prefix-only measurement receipts -> exact R5 Shadow observations -> exact R5 Forward tail -> W84 promotion resolution`.

### Measurement provenance
`src/autotrade/forward_shadow_measurement.py`:
- congela W83 candidate/spec/runtime;
- añade al runtime identity `backtest.py`, `costs.py`, `domain.py` y exact Python patch;
- congela `BacktestConfig`, initial cash e history dataset antes de outcomes;
- exige history terminado exactamente en `planned_at` y activation posterior/alineada;
- calcula cada período con `BacktestEngine` sobre un **prefijo** de datos terminado en ese período;
- encadena receipts por hash;
- exige que cada R5 `source_fingerprint` sea exactamente el `measurement_hash` correspondiente;
- no tiene broker/network/OMS/Safety/capital authority.

### Anti-selection / anti-optional-stopping
`src/autotrade/promotion_shadow_forward_binding.py`:
- Shadow de promoción = 100% exact selected candidate;
- `FrozenShadowConfig.source_config_hash = W84 policy_hash`;
- `FrozenForwardPolicy.frozen_parameters_hash = W84 policy_hash`;
- `FrozenForwardPolicy.source_code_hash = W84 measurement_runtime_hash`;
- exact horizon N: `<N=PENDING`, `N=evaluate`, `>N=FAIL/OVERRUN`;
- complete eligible Shadow tail debe coincidir con Forward en orden exacto;
- capture lag + assessment delay < un market period;
- `data cutoff <= capture <= assessment`;
- sólo puede retirar `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

## Behavioral exact-head W84
Behavioral head:

`abf25f4b699f145a629955efe73a798966f29845`

Dedicated W84 run `32740076750`:
- **44/44 W84 PASS**;
- W84 permanent boundary PASS;
- W83/W82/W81/W80/W79/W78 boundaries PASS;
- Research Authority PASS.

Core Safety run `32740076693`:
- **3035/3035 PASS**;
- exact coverage `85.20576561520785%`;
- `forward_shadow_measurement.py` 93%;
- `promotion_shadow_forward_binding.py` 89%;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- R5/R6/R7/W78-W84 boundaries PASS;
- Debt Register PASS: 60 items / 9 open / 8 blocking on uncertified tracks;
- Canonical Knowledge PASS.

Knowledge Contract `32740076824`: PASS.

ADR: `knowledge/30_DECISIONES/ADR-0016-w84-shadow-forward-promotion-binding.md`.

## Qué resolvió W84
Sólo para la identidad exacta certificada:

`SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

No es transferible a otra campaign, trial, StrategySpec, runtime, BacktestConfig, history dataset, Shadow config, Forward campaign o evidence tail.

W84 conserva obligatoriamente:
- `TD-R7D-003` OPEN;
- broker-authoritative realized fee proof FALSE;
- realized profitability unauthorized;
- PAPER candidate FALSE;
- runtime/external execution authority FALSE;
- capital authority NONE;
- LIVE BLOCKED.

## Problema exacto W85
La cadena científica/económica ya puede demostrar una candidate identity con:
- preregistered DEVELOPMENT/HOLDOUT governance;
- durable promotion assessment;
- execution-cost continuity;
- fee-complete deterministic economics;
- exact strategy artifact/runtime binding;
- exact Shadow/Forward post-activation binding.

Pero **evidence qualification no equivale a admission**.

Falta una decisión explícita que responda:

`¿esta exact cadena de evidencia satisface una policy previamente definida para entrar a PAPER probation como candidate, sin otorgarle todavía broker write autónomo?`

W85 debe evitar dos errores opuestos:
1. que W84 PASS se convierta implícitamente en PAPER candidate;
2. que un caller pueda fabricar un bool `paper_candidate_authorized=True` sin una policy/receipt durable y auditable.

## Contrato mínimo W85
Diseñar una capa de admission/probation separada que vincule, como mínimo:
1. exact W79 StrategyPromotionPolicy/threshold policy;
2. exact W80 durable assessment head/hash;
3. exact W81 cost-continuity resolution;
4. exact W82 fee-accounting resolution;
5. exact W83 strategy-version resolution;
6. exact W84 Shadow/Forward resolution + evidence/policy/measurement hashes;
7. una `PaperCandidateAdmissionPolicy` congelada antes de la decisión;
8. requisitos explícitos de probation/readiness que no dependan de broker-write authority;
9. un `PaperCandidateAdmissionReceipt` durable/hash-bound;
10. causalidad temporal y replay/idempotency definidos;
11. razón explícita PASS/FAIL/BLOCKED/INCOMPLETE;
12. ningún side effect hacia OMS/writer/broker.

## Preguntas de diseño W85 a resolver en código
- ¿La admisión es irreversible para una evidencia exacta o debe poder expirar por freshness/policy version?
- ¿Qué requisitos son de **candidate admission** y cuáles pertenecen a runtime PAPER readiness posterior?
- ¿Cómo representar revocation/suspension sin mutar historia?
- ¿Debe la policy exigir un probation budget máximo separado del capital authority real?
- ¿Qué facts pueden ser durable/static y cuáles deben revalidarse inmediatamente antes de cualquier futura ejecución?
- ¿Cómo evitar que `PAPER_CANDIDATE` sea confundido con `PAPER_EXECUTION_AUTHORIZED`?

Regla inicial: **separar esos estados explícitamente**.

## Fail-closed W85
Negative tests mínimos:
- missing/unknown W84 resolution;
- W84 evidence no PASS;
- wrong candidate/spec/runtime/measurement chain;
- stale or mismatched admission policy;
- replay para otra candidate/campaign;
- admission construida antes de evidencia necesaria;
- policy/result hash tamper;
- duplicate/conflicting admission para la misma authority key;
- attempt to mint runtime/external execution authority;
- broker/network/writer/Safety/OMS calls/imports;
- `OrderIntent` construction;
- LIVE distinto de BLOCKED.

## Gate de cierre W85
No cerrar W85 hasta demostrar en el mismo exact head:
- dedicated W85 PASS;
- permanent admission authority boundary PASS;
- W84→W78 inherited boundaries PASS;
- Research Authority PASS;
- Core Safety completo PASS;
- coverage >=85%;
- Debt Register PASS;
- Canonical Knowledge PASS;
- admission semantic claramente distinta de execution authorization.

## Debt R7D separado
- `TD-R7D-001`: CLOSED;
- `TD-R7D-002`: CLOSED;
- `TD-R7D-003`: **OPEN P2** — safe remaining-quantity reservation after partial fills.

W85 no debe cerrar `TD-R7D-003` por inferencia.

## Authority permanente
La cadena futura continúa siendo:

`Research -> Promotion Evidence -> Durable Assessment -> Economic Qualification -> Strategy Version Binding -> Shadow/Forward Binding -> PAPER Candidate Admission -> PAPER Runtime Readiness -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

Un eventual `PAPER_CANDIDATE` significa **eligible for a later PAPER readiness/execution process**, no permiso de POST.

IA/model output no puede saltarse esa cadena ni convertirse directamente en order authority.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**
**PAPER CANDIDATE ACTUAL: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
