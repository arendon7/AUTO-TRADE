# DEBT REGISTER — v0.28R

Fecha: 2026-08-10
Estado: ACTIVE
Regla: ningún track puede cerrarse con P0/P1 conocido. Toda deuda real debe tener ID y condición de cierre.

## Severidad
- `P0`: riesgo inmediato de pérdida/ejecución no controlada o corrupción crítica.
- `P1`: viola un invariant requerido del track activo o bloquea una promoción segura.
- `P2`: maintainability/reliability gap importante pero no rompe el invariant certificado actual.
- `P3`: mejora menor/tooling/documentación.

## Open debt

| ID | Sev | Area | Debt | Why it is debt | Close condition | Target |
|---|---|---|---|---|---|---|
| TD-R2-001 | P1 | OMS | partial-fill/cancel/replace lifecycle incompleto | execution lifecycle actual no cubre todavía todos los estados históricos requeridos | durable state machine + idempotency + reconciliation + crash/chaos tests | R2 |
| TD-R2-002 | P1 | Contracts | no existe aún registry completo de contratos machine-readable/versionados | cambios de mensajes/estado no tienen compatibility gate equivalente al histórico | schema registry + version rules + CI compatibility validation | R2 |
| TD-R2-003 | P1 | Risk | matriz completa order/position/strategy/portfolio exposure necesita recertificación conjunta | controles existen parcialmente pero no están certificados como conjunto R2 | policy matrix + boundary/race/restart tests | R2 |
| TD-R2-004 | P1 | Risk state | daily loss/drawdown/circuit durable semantics incompletas como track | riesgo temporal necesita persistencia y recovery explícitos | durable counters/state + restart/time-boundary/rollback tests | R2 |
| TD-R2-005 | P2 | Coverage | `persistence.py` y `reconciliation.py` tenían hotspots alrededor de 74% en CI R1 | control-plane crítico merece evidencia más profunda que el mínimo global | elevar cobertura relevante y cubrir failure branches de R2; no perseguir líneas sin valor | R2 |
| TD-OPS-001 | P3 | Graphify | `graphify-out/` aún no ha sido generado para el árbol actual | falta mapa estructural ejecutado, aunque canon/Git/tests siguen disponibles | deep graph generado en asistente compatible + `SOURCE_SHA` válido | opportunistic/R2 |
| TD-CI-001 | P3 | CI | GitHub Actions emite advertencia de Node 20 deprecated para actions actuales | no rompe CI hoy, pero es maintenance debt | actualizar actions a versiones compatibles y certificar workflows | R2/maintenance |

## Planned capability gaps — NOT classified as technical debt yet
R3–R6 aparecen como `TODO` en la matriz porque son trabajo futuro deliberadamente secuenciado, no deuda escondida del track ya certificado. Se convierten en deuda únicamente si su track se declara PASS sin implementarlos.

## Closed debt

| ID | Closed in | Evidence |
|---|---|---|
| TD-R1-DSL | R1 / `ed1c068...` | safe declarative Strategy DSL + injection/boundary tests |
| TD-R1-BOOTSTRAP | R1 / `ed1c068...` | reproducible moving-block bootstrap, 100% module coverage at certification |
| TD-R1-ADEQUACY | R1 / `ed1c068...` | SampleAdequacyPolicy + negative boundaries |
| TD-R1-VALIDATION | R1 / `ed1c068...` | durable append-only validation evidence + conflict tests |
| TD-R1-LATENCY | R1 / `ed1c068...` | explicit bar-delay latency assumption documented and no-same-bar invariant tested |

## Track closing rule
Before any track is marked `PASS`:
1. search this table for P0/P1 assigned to that track;
2. close them with code/evidence or keep the track non-PASS;
3. record newly discovered debt before merge;
4. never downgrade severity merely to satisfy a milestone.

## Capital
**LIVE TRADING: BLOQUEADO.**
