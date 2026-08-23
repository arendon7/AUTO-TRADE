# CONTEXTO RÁPIDO — AUTO-TRADE

Estado: **R0–R5 formalmente certified; R6 broker-truth cerrado; W78 execution qualification, W79 promotion governance, W80 durable assessments y W81 non-fee cost continuity técnicamente certificados. W82 Fee-Complete Execution Accounting es el siguiente hito.**

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
10. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`

## Heads clave
- R6 first canary: `0cbb782015eeed200b9851b53764ac6389c3d9ff`;
- W78: `2924456e33c2cc9e6579301b176267513a90861f`;
- W79 canonical: `b96da018641ddbe6e4bdf8ba9c26642a5174f465`;
- W80 final branch head: `fb6cc382b4cfc36cb68e1612e7c29b040332ba2e`;
- W81 behavioral implementation: `a335e301c1252e32c225282b8bcbe8442787c6f2`.

## W81 en una frase
W81 evita que un spread observado favorable haga que W78 use menor fricción non-fee que la preregistrada en Research, y sólo permite resolver ese blocker si la evidencia pertenece al W80 assessment/candidato exacto.

Certificación W81 behavioral head:
- 27/27 W81 PASS;
- Core 2917/2917 PASS;
- coverage 85.04500398769511%;
- W78/W79/W80/W81/Research boundaries PASS.

`TD-R7D-001=CLOSED`.

## Lo que sigue abierto
- `TD-R7D-002` / `FEE_ACCOUNTING_INCOMPLETE` — P1;
- `TD-R7D-003` partial-fill reservation — P2;
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- R7B real PAPER close operativo separado.

## W82
Crear fee-complete accounting sin inventar fees ni contaminar `Fill`. Separar:

`Research fee assumption -> simulated fee evidence -> authoritative PAPER fee evidence (si existe) -> fee-completeness receipt`

Un missing/partial fee set debe permanecer fail-closed.

## Authority
- PAPER candidate: FALSE;
- capital authority desde Strategy Lab: NONE;
- broker write desde Research/W78–W82 científico: NO;
- LIVE: BLOCKED.

**LIVE TRADING: BLOQUEADO.**
