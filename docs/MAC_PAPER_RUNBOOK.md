# AUTO-TRADE — Mac PAPER Runbook

Objetivo: abrir AUTO-TRADE en un Mac, certificar el sistema localmente y avanzar de forma controlada hacia un primer **CONNECTIVITY_CANARY** en Alpaca PAPER. Este runbook separa deliberadamente conectividad/protecciones de trading de estrategia.

## Invariantes

- R6 es **PAPER-only**.
- LIVE permanece bloqueado.
- `R6_EXTERNAL_PAPER_WRITE=DISABLED` es el estado normal.
- IA/research no pueden otorgar autoridad de capital ni de operador.
- `UNKNOWN` nunca genera blind retry; sólo reconciliación.
- PAPER no demuestra rentabilidad.
- Connectivity canary **no equivale** a Strategy Health.
- `mac_start.sh`, `mac_safe_console.py` y `AUTO_TRADE_MAC.command` no exponen ejecución de órdenes.

## Flujo actual certificado

```text
Mac bootstrap / Doctor / rehearsal
        ↓
workspace privado
        ↓
GET /v2/account
        ↓
GET /v2/assets/{symbol}
        ↓
GET positions + open orders  → debe quedar 0 / 0
        ↓
GET IEX market snapshot
        ↓
CONNECTIVITY_CANARY candidate local
        ↓
STOP: CONNECTIVITY_PREPARATION_BRIDGE_REQUIRED
```

Hasta el STOP anterior:

- external POST authority: NO;
- external PAPER order: NO;
- Strategy Health creado: NO;
- operator authority: NO;
- credentials en el candidate builder: NO;
- LIVE: BLOCKED.

## Nivel 0 — Preparar el Mac

Mientras PR #14 siga DRAFT:

```bash
git clone https://github.com/arendon7/AUTO-TRADE.git
cd AUTO-TRADE
git switch reconstruction/r6-external-paper-protection
bash scripts/mac_start.sh
```

También puede abrirse `AUTO_TRADE_MAC.command` desde Finder.

El bootstrap crea `.venv`, instala Python 3.12+, compila, ejecuta boundaries y rehearsal. No usa broker I/O ni credenciales.

## Nivel 1 — Rehearsal local

```bash
bash scripts/mac_start.sh rehearsal
bash scripts/mac_start.sh safety-rehearsal
```

`safety-rehearsal` usa el `CapitalSafetyKernel.evaluate(...)` real, pero sigue siendo local-only: no crea OMS staging, operador, permit, writer ni capital authority.

## Nivel 2 — Workspace privado

No crear el workspace dentro del repo:

```bash
export R6_WORKSPACE="$HOME/AUTO-TRADE-R6/workspace-001"
bash scripts/mac_start.sh init-workspace "$R6_WORKSPACE"
bash scripts/mac_start.sh readiness "$R6_WORKSPACE"
```

Estado inicial esperado:

```text
ACCOUNT_PREFLIGHT_REQUIRED
```

## Nivel 3 — Cuatro gates PAPER GET-only

Crear `.env` local únicamente para los GET preflights:

```bash
cp .env.example .env
chmod 600 .env
```

Debe conservar:

```text
R6_EXTERNAL_PAPER_WRITE=DISABLED
```

Cargar temporalmente:

```bash
set -a
source .env
set +a
```

### 3A — Account

```bash
bash scripts/mac_start.sh account-preflight \
  "$R6_WORKSPACE" \
  '<ALPACA_PAPER_ACCOUNT_ID>'
```

Sólo `GET /v2/account`. Persiste evidencia sanitizada y no guarda API secret.

Readiness debe proyectar después:

```text
ASSET_PREFLIGHT_REQUIRED
```

### 3B — Asset / venue

Elegir explícitamente un US equity para el futuro connectivity canary:

```bash
bash scripts/mac_start.sh asset-preflight "$R6_WORKSPACE" AAPL
```

Comando subyacente:

```bash
.venv/bin/python scripts/r6_external_paper_asset_preflight.py \
  --workspace "$R6_WORKSPACE" \
  --symbol AAPL \
  --allow-paper-asset-read
```

Este gate:

- hace un solo GET PAPER de `/v2/assets/{symbol}`;
- exige `class=us_equity`, `status=active`, `tradable=true`;
- exige que 1 acción entera satisfaga `min_order_size` y `min_trade_increment`;
- usa `price_increment` del broker;
- bloquea IPO/PTP para el primer canary;
- liga la evidencia al account attestation + credential reference;
- no crea autoridad de trading.

Readiness normal:

```text
FLAT_ACCOUNT_PREFLIGHT_REQUIRED
```

### 3C — Cuenta PAPER plana

```bash
bash scripts/mac_start.sh flat-account-preflight "$R6_WORKSPACE"
```

Hace exactamente dos GETs: posiciones y órdenes abiertas. Para el primer canary exige:

```text
position_count = 0
open_order_count = 0
```

La evidencia es corta y fail-closed. Si expira, usar un workspace nuevo y repetir account → asset → flat → market.

Readiness normal:

```text
MARKET_DATA_PREFLIGHT_REQUIRED
```

### 3D — Market IEX

```bash
bash scripts/mac_start.sh market-preflight "$R6_WORKSPACE" AAPL
```

Comando subyacente auditado:

```bash
.venv/bin/python scripts/r6_external_paper_market_preflight.py \
  --workspace "$R6_WORKSPACE" \
  --symbol AAPL \
  --allow-paper-market-read
```

El `--allow-paper-market-read` es obligatorio. El símbolo debe coincidir exactamente con `asset_attestation.json`. El artifact `market_snapshot.json` queda sanitizado.

## Nivel 4 — Candidate local CONNECTIVITY_CANARY

Después de completar los cuatro GETs:

```bash
bash scripts/mac_start.sh build-connectivity-candidate "$R6_WORKSPACE"
```

La consola segura elimina `APCA_API_KEY_ID` y `APCA_API_SECRET_KEY` del proceso hijo antes de construir la candidata. La invocación directa también se niega a correr si detecta credenciales:

```bash
unset APCA_API_KEY_ID APCA_API_SECRET_KEY
.venv/bin/python scripts/r6_build_connectivity_candidate.py \
  --workspace "$R6_WORKSPACE"
```

Este paso crea un `core.sqlite3` real con:

1. Instrument Master ligado a la evidencia del broker y restringido por política whole-share R6.
2. Portfolio baseline de sesión de conectividad, derivado de la evidencia `0 positions / 0 open orders`.
3. `RiskDecision` producido por el `CapitalSafetyKernel` real.
4. orden OMS durable en estado exacto `VALIDATED`.
5. autoridad durable `CONNECTIVITY_CANARY`, ligada al Event Ledger.
6. `connectivity_candidate.json`.

Límites del primer candidate:

- side: BUY;
- type: LIMIT;
- quantity: exactamente 1 acción;
- notional cap: `min(USD 10, 0.1% del portfolio_value PAPER, buying_power)`;
- un solo símbolo attested;
- max open orders: 1;
- LIVE: bloqueado.

Semántica crítica:

- `strategy_health_required=false`;
- `strategy_health_created=false`;
- `strategy_trading_authorized=false`;
- `operator_authority_created=false`;
- `external_post_authorized=false`;
- `capital_authority=NONE`;
- `profitability_claim=false`.

Los `daily_pnl=0` y `drawdown=0` de este baseline significan exclusivamente **inicio de una sesión connectivity con cuenta broker-proven-flat**. No son historial ni Health de una estrategia.

Resultado esperado:

```text
CONNECTIVITY_PREPARATION_BRIDGE_REQUIRED
```

## Nivel 5 — Connectivity preparation bridge

**Aún no es un comando operador autorizado.**

El siguiente incremento estructural debe permitir que preparación, OMS stage y final guard reconozcan la autoridad durable `CONNECTIVITY_CANARY` sin fabricar Strategy Health. La ruta normal de estrategia debe continuar exigiendo Health real.

Hasta que ese bridge tenga Core + R6 Authority + Knowledge + macOS verdes:

- no editar SQLite;
- no fabricar Health;
- no fabricar `RiskDecision`;
- no copiar artifacts entre attempts;
- no activar writer para “probar”.

## Strategy trading — ruta separada

Un connectivity canary sólo prueba infraestructura/protecciones. Trading basado en estrategia requiere adicionalmente evidencia real de research/backtest/holdout/shadow/forward y Strategy/Portfolio Health según las políticas canónicas. El connectivity authority nunca puede promover una estrategia ni LIVE.

## STOP — antes de cualquier orden PAPER real

No ejecutar un POST sólo porque los pasos anteriores pasan.

Antes del primer POST PAPER deben existir y estar certificados, como mínimo:

1. connectivity preparation bridge durable;
2. final pre-write guard específico de propósito;
3. revalidación fresca de account/asset/flat/market;
4. Safety + OMS bindings exactos;
5. decisión humana explícita y single-use;
6. bracket protector exacto;
7. idempotencia/UNKNOWN/reconciliation;
8. captura `trade_updates` y qualification.

El comando de ejecución existente permanece fuera de Safe Start y no forma parte de este runbook hasta cerrar el bridge.

## Reconciliación posterior a un futuro POST

Cuando se autorice eventualmente un canary real:

- `UNKNOWN` → reconciliación GET-only por `client_order_id`;
- broker order → nested bracket validation;
- `trade_updates` PAPER → authenticated receive-only stream;
- fills/slippage/latency/terminality → qualification;
- cualquier PAPER qualification mantiene `capital_authority=NONE`, `profitability_claim=false`, `live_trading=BLOCKED`.

`TD-R6-001..006` sólo pueden cerrarse con evidencia externa real.

## Si algo no coincide

No modificar hashes, SQLite, manifests ni evidence JSON para avanzar. Conservar el workspace, ejecutar readiness y tratar cualquier inconsistencia como fail-closed.

## Estado al terminar este runbook en el STOP

- external PAPER order sent: **NO**
- external PAPER order enviado por el proyecto: **0**
- capital authority: **NONE**
- broker write: **NO**
- LIVE trading: **BLOCKED**
