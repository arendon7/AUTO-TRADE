# AUTO-TRADE

Sistema de trading algorítmico modular con investigación asistida por IA y ejecución gobernada por controles deterministas.

## Regla principal

La IA puede investigar, proponer, explicar y priorizar. **No puede saltarse límites de riesgo, modificar capital autorizado ni enviar órdenes fuera del OMS + Capital Safety Kernel.** La ejecución es fail-closed.

## Estado operativo

- R0–R5: baseline certificado en `main`.
- R6: external Alpaca PAPER en rama/PR DRAFT.
- R6 es PAPER-only; LIVE permanece bloqueado.
- Runtime single-shot, decisión humana durable, OMS handoff, reconciliación, bracket protection, trade updates, qualification y readiness están implementados bajo boundaries permanentes.
- `TD-R6-007..013` están cerrados estructuralmente.
- La certificación final de R6 depende todavía de evidencia externa real `TD-R6-001..006`.
- Ninguna evidencia PAPER demuestra rentabilidad ni concede autoridad LIVE.
- External PAPER orders enviados hasta ahora: **0**.

## Inicio rápido en Mac — doble clic

El paquete Mac generado por CI incluye `AUTO_TRADE_MAC.command`.

1. Descomprime el paquete.
2. Lee `LEEME_PRIMERO_MAC.md`.
3. Haz doble clic en `AUTO_TRADE_MAC.command`.
4. Si Gatekeeper bloquea el primer arranque, usa clic derecho → **Abrir** y confirma una sola vez.

No desactives Gatekeeper globalmente.

El menú de doble clic sólo ofrece superficies seguras:
- crear workspace privado;
- Doctor;
- rehearsal offline;
- **Capital Safety rehearsal local**;
- readiness;
- abrir runbook;
- mostrar la secuencia GET-only.

No contiene ninguna opción de ejecución de órdenes.

## Inicio rápido desde Git/Terminal

Mientras PR #14 siga DRAFT:

```bash
git clone https://github.com/arendon7/AUTO-TRADE.git
cd AUTO-TRADE
git switch reconstruction/r6-external-paper-protection
bash scripts/mac_start.sh
```

`mac_start.sh` es el entrypoint seguro por Terminal. Si `.venv` no existe, ejecuta automáticamente el bootstrap seguro y después abre Mac Doctor. Fuerza `R6_EXTERNAL_PAPER_WRITE=DISABLED`.

### Primer ensayo sin cuenta Alpaca

```bash
bash scripts/mac_start.sh rehearsal
bash scripts/mac_start.sh safety-rehearsal
bash scripts/mac_start.sh safety-rehearsal --kill-switch
bash scripts/mac_start.sh init-workspace "$HOME/AUTO-TRADE-R6/workspace-001"
bash scripts/mac_start.sh readiness "$HOME/AUTO-TRADE-R6/workspace-001"
```

`Safety rehearsal` usa el `CapitalSafetyKernel` real con límites fijos de ensayo. El usuario puede cambiar candidato/escenario, pero no elevar los límites duros desde CLI. Incluso si el kernel devuelve `APPROVED`, el rehearsal mantiene:
- broker network: NO;
- broker write: NO;
- OMS staging: NO;
- operator authority: NO;
- external execution authority: NO;
- capital authority: NONE;
- profitability claim: false;
- LIVE: BLOCKED.

El workspace se crea fuera del repositorio, con permisos privados, sin DBs de trading, sin credenciales y debe comenzar en `ACCOUNT_PREFLIGHT_REQUIRED`.

## Primeros GET reales de Alpaca PAPER

Sólo después de configurar credenciales **PAPER** y manteniendo el write gate deshabilitado:

```bash
bash scripts/mac_start.sh account-preflight \
  "$HOME/AUTO-TRADE-R6/workspace-001" \
  '<ALPACA_PAPER_ACCOUNT_ID>'

bash scripts/mac_start.sh flat-account-preflight \
  "$HOME/AUTO-TRADE-R6/workspace-001"

bash scripts/mac_start.sh market-preflight \
  "$HOME/AUTO-TRADE-R6/workspace-001" \
  AAPL
```

Secuencia obligatoria del primer canary:

```text
account -> flat-account -> market -> readiness
```

Flat-account realiza exactamente los GET auditados de posiciones y órdenes abiertas. Para el primer canary exige 0 posiciones + 0 órdenes abiertas y su evidencia es de corta duración. Market preflight usa IEX y sigue sin tener autoridad de órdenes.

## Límite actual antes del primer canary real

Ya podemos ensayar en Mac:
- bootstrap real en macOS ARM64;
- rehearsal offline;
- Finder launcher;
- Capital Safety Kernel local;
- workspace/readiness;
- account GET;
- flat-account two-GET;
- IEX market GET.

Lo siguiente no es “forzar un POST”. Debemos preservar la separación entre:

1. **connectivity/protection canary** — prueba infraestructura PAPER, no rentabilidad;
2. **strategy trading** — requiere una estrategia US-equity promovida por research/backtest/holdout/shadow/forward + Health.

No se debe fabricar manualmente Strategy Health, `RiskDecision`, `core.sqlite3` o artifacts para avanzar readiness.

## Documentación Mac

Primer uso:

```text
LEEME_PRIMERO_MAC.md
```

Runbook técnico:

```text
docs/MAC_PAPER_RUNBOOK.md
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
