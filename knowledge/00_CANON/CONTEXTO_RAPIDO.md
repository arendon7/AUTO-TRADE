# CONTEXTO RÁPIDO — AUTO-TRADE

Estado: **R0–R5 formalmente certified; R6 broker-truth cerrado; W78 execution qualification, W79 promotion governance, W80 durable assessments, W81 non-fee cost continuity, W82 fee-complete deterministic qualification, W83 execution strategy-version binding y W84 Shadow/Forward promotion binding técnicamente certificados. `TD-R7D-001/002` CLOSED. W85 PAPER Candidate Admission / Probation Gate ACTIVE.**

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
- W84 behavioral certified head: `abf25f4b699f145a629955efe73a798966f29845`.

## W84 en una frase
W84 demuestra que los outcomes Shadow/Forward atribuidos al exact candidate W83 provienen de mediciones deterministas, prefix-only y hash-bound del exact StrategySpec/runtime/config/history; cada observación R5 debe llevar el exact measurement hash y el complete tail/fixed horizon/freshness contract impide omission y optional stopping.

Certificación behavioral W84:
- Dedicated `32740076750`: **44/44 PASS**;
- W84 permanent boundary PASS;
- Core `32740076693`: **3035/3035 PASS**;
- exact coverage `85.20576561520785%`;
- `forward_shadow_measurement.py` 93%;
- `promotion_shadow_forward_binding.py` 89%;
- W78–W84/Research boundaries PASS;
- Debt Register + Canonical Knowledge PASS;
- Knowledge Contract `32740076824`: PASS.

`SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` queda resuelto únicamente para la exact W83 candidate/artifact/runtime + W84 plan/policy/measurement + R5 Shadow/Forward identity.

## Lo que sigue abierto
- `TD-R7D-003` partial-fill reservation — P2;
- PAPER candidate admission/probation — objetivo W85;
- R7B real PAPER close operativo separado.

## W85
W85 debe añadir una decisión explícita de admission/probation que consuma la cadena certificada W79→W84 sin inferir autoridad de ejecución.

Objetivo conceptual:

`qualified evidence chain -> explicit durable admission policy -> PAPER_CANDIDATE decision`

pero manteniendo:

`PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED`.

El eventual admission receipt debe ser hash-bound, auditable, replay-safe, candidate-specific y sin broker/network/OMS/Safety/writer authority.

## Authority
- `EVIDENCE_QUALIFIED != PAPER_CANDIDATE`;
- PAPER candidate actual: FALSE;
- PAPER candidate futuro no implica execution authorization;
- capital authority desde capas científicas/admission: NONE;
- broker write desde Research/W78–W85: NO;
- broker-authoritative realized fee proof desde W82: FALSE;
- realized profitability authorized: FALSE;
- LIVE: BLOCKED.

**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**
**LIVE TRADING: BLOQUEADO.**
