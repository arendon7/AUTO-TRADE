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
bash scripts/mac_start.sh
```

`mac_start.sh` es el punto de entrada recomendado. Si `.venv` no existe, ejecuta primero el bootstrap seguro; después abre el Mac Doctor. El launcher **no contiene ninguna opción de ejecución de órdenes** y fuerza `R6_EXTERNAL_PAPER_WRITE=DISABLED`.

Primer ensayo recomendado, todavía sin credenciales ni red al broker:

```bash
bash scripts/mac_start.sh rehearsal
bash scripts/mac_start.sh init-workspace "$HOME/AUTO-TRADE-R6/workspace-001"
bash scripts/mac_start.sh readiness "$HOME/AUTO-TRADE-R6/workspace-001"
```

El workspace se crea fuera del repositorio, con permisos privados, sin DBs de trading, sin credenciales y debe comenzar en `ACCOUNT_PREFLIGHT_REQUIRED`.

Comandos seguros principales:

```bash
bash scripts/mac_start.sh doctor
bash scripts/mac_start.sh rehearsal
bash scripts/mac_start.sh init-workspace "$HOME/AUTO-TRADE-R6/workspace-001"
bash scripts/mac_start.sh readiness "$HOME/AUTO-TRADE-R6/workspace-001"
```

Cuando ya hayas configurado credenciales Alpaca PAPER, los únicos pasos de red disponibles desde Safe Start son GET-only y requieren una acción explícita:

```bash
bash scripts/mac_start.sh account-preflight "$HOME/AUTO-TRADE-R6/workspace-001" '<ALPACA_PAPER_ACCOUNT_ID>'
bash scripts/mac_start.sh market-preflight "$HOME/AUTO-TRADE-R6/workspace-001" AAPL
```

El bootstrap/rehearsal no leen credenciales Alpaca, no llaman al broker y mantienen deshabilitado el write gate. Account/market preflight sí usan red, pero no tienen superficie de escritura de órdenes.

**Límite actual antes del primer canary real:** estamos cerrando la inicialización autoritativa de cartera PAPER y la candidatura/`RiskDecision` producida por Capital Safety Kernel para que la preparación offline no requiera fabricar estado a mano. Hasta cerrar ese gate, se puede ensayar por completo el recorrido local y los dos GET-only, pero no se debe forzar manualmente `core.sqlite3` ni artifacts para avanzar.

Runbook completo:

```text
docs/MAC_PAPER_RUNBOOK.md
```

Inspector local/read-only de un workspace existente:

```bash
.venv/bin/python scripts/r6_inspect_paper_readiness.py --workspace <WORKSPACE>
```

El recorrido externo posterior permanece separado: primero account preflight GET-only, luego market-data IEX GET-only, y sólo después preparación offline. Ningún paso de bootstrap/rehearsal/Safe Start avanza automáticamente hacia una orden.

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
