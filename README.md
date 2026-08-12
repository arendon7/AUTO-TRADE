# AUTO-TRADE

Sistema de trading algorítmico modular con investigación asistida por IA y ejecución gobernada por controles deterministas.

## Regla principal

La IA puede investigar, proponer, explicar y priorizar. **No puede saltarse límites de riesgo, modificar capital autorizado ni enviar órdenes fuera del OMS + Capital Safety Kernel.** La ejecución es fail-closed.

## Estado operativo

- R0–R5: baseline certificado en `main`.
- R6: external Alpaca PAPER en rama/PR DRAFT.
- R6 es PAPER-only; LIVE permanece bloqueado.
- El runtime single-shot, decisión humana durable, OMS handoff, reconciliación, trade updates, qualification y readiness local están implementados bajo boundaries permanentes.
- `TD-R6-013` está cerrado; la certificación final de R6 depende todavía de evidencia externa real `TD-R6-001..006`.
- Ninguna evidencia PAPER demuestra rentabilidad ni concede autoridad LIVE.
- No se ha enviado todavía un external PAPER order como parte de la certificación R6.

## Inicio rápido en Mac

Mientras PR #14 siga DRAFT:

```bash
git clone https://github.com/arendon7/AUTO-TRADE.git
cd AUTO-TRADE
git switch reconstruction/r6-external-paper-protection
bash scripts/mac_bootstrap.sh
```

El bootstrap crea `.venv`, instala el proyecto, ejecuta boundaries y rehearsal local, **no lee credenciales Alpaca, no llama al broker y fuerza `R6_EXTERNAL_PAPER_WRITE=DISABLED`**.

Runbook completo:

```text
docs/MAC_PAPER_RUNBOOK.md
```

Inspector local/read-only de un workspace existente:

```bash
.venv/bin/python scripts/r6_inspect_paper_readiness.py --workspace <WORKSPACE>
```

## Cómo continuar el proyecto

1. Leer `AGENTS.md`.
2. Leer `knowledge/00_CANON/CONTEXTO_RAPIDO.md`.
3. Leer `knowledge/00_CANON/ESTADO_ACTUAL.md`.
4. Leer `knowledge/00_CANON/TAREA_ACTIVA.md`.
5. Consultar `graphify-out/graph.json` cuando exista y usar Graphify antes de releer masivamente el repositorio.
6. Implementar en una rama, probar, actualizar ADR/handoff y regenerar Graphify.

## Memoria del proyecto

- `main`: verdad técnica integrada.
- `stable`: futura última versión certificada para operación/paper/live cuando corresponda.
- `knowledge/`: vault Obsidian y memoria humana canónica.
- `graphify-out/`: memoria estructural regenerable del código y documentación.
- `AGENTS.md`: contrato de trabajo para asistentes.
