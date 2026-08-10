# MAPA DEL PROYECTO

## Capas

```text
External data/news/broker
        |
        v
Market Data -> Normalization -> Feature/Signal Research
        |                         |
        |                         v
        |                    Strategy Agent
        |                         |
        |                    OrderIntent
        |                         v
        +-----------------> Risk Agent
                                  |
                            RiskDecision
                                  v
                         CAPITAL SAFETY KERNEL
                                  |
                                  v
                                 OMS
                                  |
                                  v
                         Execution Adapter
                                  |
                                  v
                               Broker
                                  |
                    fills/orders/account state
                                  v
                         Reconciliation Engine
                                  |
                                  v
                    Portfolio + Event Ledger
                                  |
                          Monitoring/Alerts
```

## Autoridad
- Strategy Agent produce `OrderIntent`; no produce órdenes ejecutables.
- Risk Agent puede recomendar/rechazar, pero el Safety Kernel vuelve a validar límites duros.
- OMS controla idempotencia, máquina de estados y lifecycle.
- Broker adapter solo acepta órdenes validadas por OMS.
- Reconciliation compara estado esperado vs broker y puede bloquear operación.
- Orchestrator coordina; no tiene bypass.

## Plano de investigación
Research/Backtest vive separado del plano de ejecución. Sus artefactos pueden promoverse únicamente mediante gates explícitos.

## Promotion ladder
`hypothesis -> backtest -> protected holdout -> paper -> shadow/limited live -> live -> scaled`

Cada transición requiere evidencia; ninguna IA se auto-promueve.

## Memoria de desarrollo
- Obsidian/Markdown: intención, decisiones, estado, tareas, handoffs.
- Graphify: estructura real y relaciones del repo.
- Git/GitHub: historial y verdad versionada.
- Tests/CI: evidencia de comportamiento.
