# CONTEXTO RÁPIDO — AUTO-TRADE

## Objetivo
Construir un sistema de auto-trading algorítmico basado en agentes que busque **rentabilidad neta sostenible** sin entregar a una IA control ilimitado del dinero.

## Arquitectura acordada
- Market Data Agent
- Strategy Agent
- Risk Agent
- Execution Agent
- Portfolio Agent
- Monitoring/Alert Agent
- Research/Backtest Agent
- Orchestrator/Supervisor Agent
- Capital Safety Kernel determinista
- OMS (Order Management System)
- Reconciliation Engine
- Event Ledger auditable

El orquestador coordina, pero no puede omitir controles del kernel, OMS o reconciliación.

## Baseline 1.0 recuperado
- ChatGPT se usa para investigación/desarrollo; no como autoridad final de ejecución.
- Quant y safety críticos deben ser deterministas y testeables.
- Holdout protegido contra sobreajuste.
- Diseñar un portfolio de edges, no depender de una sola estrategia.
- Chaos/failure testing para fallos de red, broker, precios, duplicados y estados parciales.
- Ejecución fail-closed.

## Restricción principal
Nunca permitir una operación grande o errónea por fallo de IA, software, datos, broker o configuración. Deben existir límites redundantes y kill switches.

## Memoria
`knowledge/` es un vault de Obsidian. `graphify-out/` será el grafo regenerable de Graphify. El código y configuración versionada siguen siendo la verdad operativa.
