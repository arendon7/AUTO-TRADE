# CONTEXTO RÁPIDO — AUTO-TRADE

Estado: **R0–R5 formalmente certified; R6 broker-truth cerrado; W78 execution qualification, W79 promotion governance, W80 durable assessments, W81 non-fee cost continuity, W82 fee-complete deterministic qualification y W83 execution strategy-version binding técnicamente certificados. `TD-R7D-001/002` CLOSED. W84 Shadow/Forward Promotion Binding ACTIVE.**

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
12. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`

## Heads clave
- R6 first canary: `0cbb782015eeed200b9851b53764ac6389c3d9ff`;
- W78: `2924456e33c2cc9e6579301b176267513a90861f`;
- W79 canonical: `b96da018641ddbe6e4bdf8ba9c26642a5174f465`;
- W80 final branch head/base W81: `fb6cc382b4cfc36cb68e1612e7c29b040332ba2e`;
- W81 canonical: `0d042bfb80c9ae0f89de035b6638938f831a3cba`;
- W82 certified closure/base W83: `d33a99727d9f326a35612ffa39007b436fe76625`;
- W83 behavioral exact head: `177517a29d677a34dc4a711b56b955bb5cf2cd51`.

## W83 en una frase
W83 prueba que el candidate seleccionado no es sólo un `strategy_version` string: su exact `TrialSpec`/`StrategySpec`, parameters, dataset, W82-qualified intent semantics y el runtime source-set cargado (`dsl.py + strategy.py + market.py + exact Python patch`) deben coincidir antes de retirar `EXECUTION_STRATEGY_VERSION_UNBOUND`.

Certificación behavioral W83:
- Dedicated `32688103622`: **25/25 PASS**;
- Core `32688103642`: **2991/2991 PASS**;
- exact coverage `85.04640770024064%`;
- W78–W83/Research boundaries PASS;
- Debt Register + Canonical Knowledge PASS;
- Knowledge Contract `32688103696`: PASS.

`EXECUTION_STRATEGY_VERSION_UNBOUND` queda resuelto únicamente para el exact bound candidate/runtime identity.

## Lo que sigue abierto
- `TD-R7D-003` partial-fill reservation — P2;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` — objetivo W84;
- R7B real PAPER close operativo separado.

## W84
Reutilizar la infraestructura R5 existente, no construir un segundo shadow engine:
- `FrozenShadowConfig` + `SQLitePortfolioShadowRegistry`;
- `FrozenForwardPolicy` + `SQLiteForwardEvidenceRegistry`.

Probar:

`exact W83 candidate/runtime identity == frozen Shadow identity == frozen Forward policy identity == verified post-activation evidence chain`.

La identidad/config/thresholds deben estar congelados antes de los outcomes forward. No selection/recalibration ex post.

El eventual W84 resolution sólo puede remover `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`. Cerrar ese blocker tampoco debe acuñar PAPER candidate automáticamente.

## Authority
- `EVIDENCE_QUALIFIED != PAPER_CANDIDATE`;
- PAPER candidate: FALSE;
- capital authority desde capas científicas: NONE;
- broker write desde Research/W78–W84: NO;
- broker-authoritative fee proof desde W82: FALSE;
- realized profitability authorized: FALSE;
- LIVE: BLOCKED.

**LIVE TRADING: BLOQUEADO.**
