# CONTEXTO RÁPIDO — AUTO-TRADE

Estado: **R0–R5 formalmente certified; R6 broker-truth cerrado; W78 execution qualification, W79 promotion governance, W80 durable assessments, W81 non-fee cost continuity, W82 fee-complete deterministic qualification, W83 execution strategy-version binding y W84 Shadow/Forward binding behaviorally certified. W85 PAPER Candidate Admission / Probation Gate es el siguiente layer.**

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
13. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`

## Heads clave
- R6 first canary: `0cbb782015eeed200b9851b53764ac6389c3d9ff`;
- W78: `2924456e33c2cc9e6579301b176267513a90861f`;
- W79 canonical: `b96da018641ddbe6e4bdf8ba9c26642a5174f465`;
- W80 final branch head/base W81: `fb6cc382b4cfc36cb68e1612e7c29b040332ba2e`;
- W81 canonical: `0d042bfb80c9ae0f89de035b6638938f831a3cba`;
- W82 certified closure/base W83: `d33a99727d9f326a35612ffa39007b436fe76625`;
- W83 certified base W84: `0d177a1cfb16cffbb1266ee07865db5f77f1fe50`;
- W84 behavioral certified head: `f1ed0f675224c515f74a099ddb0beeefd9c96629`.

## W84 en una frase
W84 demuestra que el exact candidate W83 fue medido prefix-only bajo un plan/policy preregistrado, que sus outcomes coinciden con R5 Shadow/Forward durable truth y que la certificación final ocurrió dentro del frozen decision-lag budget usando un reloj UTC interno del proceso.

## Salida canónica W84
Sólo:

`PromotionShadowForwardFinalVerification`.

Son intermedios y no deben alimentar W85 directamente:
- `PromotionShadowForwardResolution`;
- `PromotionShadowForwardSourceVerification`.

La source verification relee R5 y measurement receipts, recalcula las métricas y rechaza evidence rehash-valid que contradiga la fuente. El final verifier vuelve a ejecutar esa verificación y usa reloj interno; no acepta caller `verified_at`.

## Certificación behavioral W84
- Dedicated `32745537577`: **76/76 PASS**;
- W84 permanent boundary PASS;
- Core `32745537856`: **3067/3067 PASS**;
- exact coverage `85.27030933795895%`;
- measurement 93%; V2 binding 89%; source verification 89%; final verification 95%;
- Contract Registry 10 PASS;
- W78–W84/Research/R5/R6/R7 boundaries PASS;
- Debt Register PASS;
- Knowledge `32745537825`: PASS.

## W85
W85 construye la decisión explícita que separa qualification de admission:

`exact W79→W84 final certified chain -> frozen admission policy -> durable PAPER_CANDIDATE decision`.

La entrada W84 debe ser el exact `PromotionShadowForwardFinalVerification`.

W85 no puede conceder:
- broker POST;
- OMS/Safety handoff;
- `OrderIntent`;
- runtime execution;
- capital authority;
- LIVE.

## Lo que sigue abierto
- `TD-R7D-003`: **OPEN P2** — partial-fill reservation;
- PAPER candidate admission/probation: W85;
- posterior PAPER runtime readiness;
- R7B real PAPER close operativo separado.

## Authority
- `EVIDENCE_QUALIFIED != PAPER_CANDIDATE`;
- `PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED`;
- PAPER candidate actual: FALSE;
- capital authority desde W78–W85: NONE salvo decisión futura explícita de otro control layer;
- broker write desde capas científicas/admission: NO;
- broker-authoritative realized fee proof: FALSE;
- realized profitability authorized: FALSE;
- LIVE: BLOCKED.

**LIVE TRADING: BLOQUEADO.**
