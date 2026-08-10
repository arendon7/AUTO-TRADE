# AUTO-TRADE

Sistema de trading algorítmico modular con agentes, investigación asistida por IA y ejecución gobernada por controles deterministas.

## Regla principal

La IA puede investigar, proponer, explicar y priorizar. **No puede saltarse límites de riesgo, modificar capital autorizado ni enviar órdenes fuera del OMS + Capital Safety Kernel.** La ejecución debe ser fail-closed.

## Cómo continuar el proyecto

1. Leer `AGENTS.md`.
2. Leer `knowledge/00_CANON/CONTEXTO_RAPIDO.md`.
3. Leer `knowledge/00_CANON/ESTADO_ACTUAL.md`.
4. Leer `knowledge/00_CANON/TAREA_ACTIVA.md`.
5. Consultar `graphify-out/graph.json` cuando exista y usar Graphify antes de releer masivamente el repositorio.
6. Implementar en una rama, probar, actualizar ADR/handoff y regenerar Graphify.

## Memoria del proyecto

- `main`: verdad técnica integrada.
- `stable`: futura última versión certificada para operación/paper/live.
- `knowledge/`: vault Obsidian y memoria humana canónica.
- `graphify-out/`: memoria estructural regenerable del código y documentación.
- `AGENTS.md`: contrato de trabajo para asistentes.

## Estado

Foundation v0.1: arquitectura y memoria operativa en construcción. No existe todavía autorización para trading con dinero real.