# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-24
Estado: **W85 PAPER CANDIDATE ADMISSION TECHNICALLY CERTIFIED; CANONICAL DOCUMENTATION EXACT-HEAD RECERTIFICATION IN PROGRESS; W86 PAPER RUNTIME READINESS NEXT.**

## Base técnica W85 certificada
R0–R5 continúan como tracks formalmente certificados del machine registry. R6 first real PAPER canary y W78–W85 tienen certificaciones técnicas independientes.

Technical exact W85 head:

`c66855455dac4955a9d89994135f11c0a2c6da59`

Evidencia:
- Dedicated W85 `32803158593`: **67/67 PASS**;
- W85 permanent boundary PASS;
- inherited W84→W78 + Research Authority PASS;
- Core Safety `32803158524`: **3134/3134 PASS**;
- cobertura exacta `85.02334230099699%`;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- Research/R5/R6/R7/W78–W85 boundaries PASS;
- Debt Register PASS: 60 items / 9 open / 8 blocking on uncertified tracks;
- Canonical Knowledge PASS.

El cierre documental W85 es descendiente documentation-only del technical head y debe pasar recertificación exact-head antes de declarar W85 canónicamente cerrado.

## W85 — verdad técnica vigente
W85 crea una frontera explícita entre evidencia calificada y futura operación PAPER:

`exact certified W79→W84 chain`
`-> frozen PaperCandidateAdmissionPolicy`
`-> admission-time canonical W84 source re-proof`
`-> durable PaperCandidateAdmissionReceipt`
`-> append-only lifecycle`
`-> PaperCandidateAdmissionFinalVerification V2`
`-> PaperCandidateFinalEligibility V2`.

### Regla central
`EVIDENCE_QUALIFIED != PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED`.

Un PASS W85 puede autorizar únicamente la condición **PAPER candidate** para una exact chain y una ventana finita. No concede broker POST, OMS, Safety, OrderIntent, capital ni LIVE.

## W85 final trust boundary
El hardening final resuelve dos problemas que surgieron durante la revisión:

1. W85 no confía en el timestamp histórico de un `PromotionShadowForwardFinalVerification` W84 para probar frescura de admisión. Antes de admitir, vuelve a ejecutar el **finalizador canónico W84** sobre las mismas fuentes R5/measurement durables.
2. `PaperCandidateAdmissionFinalVerification V2` y `PaperCandidateFinalEligibility V2` arrastran y cotejan el exact W85 source-proof hash; no pueden volver a derivar frescura desde `w84_finalization.process_verified_at` histórico.

La frescura de admisión se liga al capture durable y al reloj interno de decisión W85.

`historical_finalization_timestamp_trusted_for_freshness = False` es una invariantes permanente.

## Lifecycle W85
Una admisión tiene una ventana `valid_until` finita.

Suspensión, revocación y expiración no acuñan nueva autoridad.

`REINSTATE` sólo reanuda administrativamente la **misma** admisión mientras su ventana original siga vigente. No puede:
- extender `valid_until`;
- renovar evidence;
- cambiar policy;
- ampliar probation envelope;
- otorgar PAPER execution authority.

## Estado operacional real
La suite W85 usa fixtures/evidence de prueba. La certificación demuestra que el mecanismo puede producir un receipt PASS seguro; no demuestra que la autoridad operacional actual contenga uno.

Por tanto:

**PAPER CANDIDATE ACTUAL: FALSE.**
**PAPER EXECUTION AUTHORIZED: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**

## Tarea siguiente — W86 PAPER Runtime Readiness Gate
Objetivo:

`exact final W85 candidate eligibility + fresh PAPER operational truth -> bounded PAPER runtime readiness decision`

W86 debe responder exclusivamente si el entorno PAPER está preparado para considerar una futura ejecución. **Readiness no es execution authority.**

### Diseño mínimo esperado
- entrada typed exacta desde la salida final W85, no hashes/strings sueltos;
- candidate W85 debe estar vigente, ACTIVE y no suspendido/revocado/expirado;
- broker/account identity exacta y PAPER-only;
- account/Portfolio/market truth fresca;
- product/venue/currency/symbol compatibility;
- strategy/runtime identity sin drift;
- open-order/exposure conflict checks;
- kill switch / Health / Safety prerequisites fail-closed;
- compatibilidad entre broker minimums y envelope W85;
- freshness budgets explícitos e internos;
- receipt/readiness decision hash-bound y temporalmente finito;
- no conversion automática de `probation_notional_cap_usd` a sizing;
- no `OrderIntent` construction;
- no OMS handoff;
- no broker POST/cancel/replace;
- capital authority NONE;
- LIVE BLOCKED.

### Negative tests obligatorios W86
- missing/tampered/stale W85 final eligibility;
- W85 lifecycle SUSPENDED/REVOKED/EXPIRED;
- candidate identity/policy/source-proof mismatch;
- stale account, Portfolio o market evidence;
- broker/account/environment mismatch;
- product/venue/currency incompatibility;
- broker minimum incompatible con W85 probation envelope;
- existing exposure/open-order conflict;
- kill switch active o Health/Safety critical unknown/stale;
- strategy/runtime drift;
- caller-supplied freshness timestamp;
- readiness receipt rehash-valid con provenance falsa;
- readiness intentando acuñar OrderIntent/OMS/execution/capital/LIVE authority.

## Gate de cierre W86
Mismo exact head:
- Dedicated W86 PASS;
- permanent W86 runtime-readiness boundary PASS;
- inherited W85→W78 boundaries PASS;
- Research/R5/R6/R7 boundaries PASS según corresponda;
- Core Safety PASS;
- coverage >=85%;
- Debt Register PASS;
- Canonical Knowledge PASS.

## Deuda independiente
- `TD-R7D-001`: CLOSED;
- `TD-R7D-002`: CLOSED;
- `TD-R7D-003`: **OPEN P2** — remaining-quantity reservation after partial fills.

W86 no debe cerrar `TD-R7D-003` salvo trabajo explícito separado.

## R6/R7 recordatorio operacional
R7 real PAPER close permanece en PR #49 y sigue separado de W78–W86. La exposición BTC/USD residual no autoriza ningún reintento automático.

La cadena futura continúa:

`PAPER Candidate Admission -> PAPER Runtime Readiness -> [future explicit execution authority] -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`.

IA/model output no puede saltarse esa cadena.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**
**PAPER CANDIDATE ACTUAL: FALSE.**
**PAPER EXECUTION AUTHORIZED: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
