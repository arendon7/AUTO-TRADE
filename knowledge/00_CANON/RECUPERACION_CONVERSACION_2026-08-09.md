# RECUPERACIÓN DE CONVERSACIÓN — SISTEMA DE TRADING ALGORÍTMICO

Fecha de recuperación: 2026-08-10

Este archivo conserva lo recuperado con evidencia de la conversación anterior para evitar reconstrucciones futuras. Distingue decisiones confirmadas de detalles no recuperados literalmente.

## Decisiones confirmadas

### Propósito
Construir un sistema de trading algorítmico/auto-trading con arquitectura basada en agentes, disciplina operacional, control de riesgo y mejora continua, sin dar a una IA control ilimitado del dinero.

### Agentes previstos
1. Market Data Agent: precios, velas, order book, volumen, noticias y señales externas.
2. Strategy Agent: análisis y propuestas de entrada/salida.
3. Risk Agent: riesgo por operación, exposición, drawdown y volatilidad.
4. Execution Agent: envío de órdenes bajo límites estrictos.
5. Portfolio Agent: posiciones, balance, PnL y asignación.
6. Monitoring/Alert Agent: anomalías, errores y alertas.
7. Research/Backtest Agent: pruebas históricas.
8. Orchestrator/Supervisor Agent: coordinación sin facultad de saltarse controles.

### Preferencias de plataforma
- Usar ChatGPT/OpenAI para el trabajo de investigación y desarrollo; no Claude.
- Reutilizar de Binario IA la arquitectura de skills, agentes, contratos, governance, memoria y quality gates.

### Architecture Baseline 1.0 recuperada
- ChatGPT/IA queda en research y asistencia, no como autoridad financiera final.
- Núcleo cuantitativo y de seguridad determinista.
- Holdout protegido.
- Portfolio de edges/estrategias con diversificación razonable.
- Capital Safety Kernel.
- OMS.
- Reconciliation.
- Event Ledger.
- Chaos testing.

### Objetivo de negocio/riesgo
Maximizar rentabilidad neta sostenible protegiendo capital. Controles fail-closed contra órdenes grandes o erróneas, duplicados, precios absurdos, pérdidas excesivas y fallos operativos.

## Estado recuperado
La conversación llegó al menos a una etapa denominada `v0.1 Foundation`. No se recuperó de forma fiable el texto literal posterior ni una implementación confirmada de código; por integridad, este repositorio no inventa esas piezas y continúa desde la base confirmada.

## Regla para futuras recuperaciones
Cuando una conversación termine:
- Actualizar `ESTADO_ACTUAL.md`.
- Actualizar `TAREA_ACTIVA.md`.
- Escribir un handoff.
- Registrar ADRs nuevos.
- Regenerar Graphify.
Así un chat nuevo no depende del historial completo.