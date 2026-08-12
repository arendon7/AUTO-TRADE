# AUTO-TRADE — Mac PAPER Runbook

Estado objetivo de este runbook: poder abrir AUTO-TRADE en un Mac, validar el sistema localmente y avanzar de forma explícita desde **cero red al broker** hasta, en una fase posterior y separada, un canary real de Alpaca PAPER.

## Regla operativa

- R6 es **PAPER-only**.
- LIVE permanece bloqueado.
- La IA no puede otorgar autoridad de capital ni aprobación humana.
- `UNKNOWN` nunca se reintenta con otro POST: va a reconciliación GET-only.
- Una evidencia PAPER no demuestra rentabilidad ni autoriza LIVE.
- Ningún paso de instalación o diagnóstico envía órdenes.
- Account preflight, market-data preflight, preparación, decisión humana y ejecución son comandos separados.
- `mac_start.sh` y `mac_safe_console.py` **no exponen ningún comando de ejecución de órdenes**.

## Ruta que ya puedes ensayar en el Mac

Sin cuenta Alpaca, sin credenciales y sin red al broker:

```bash
git clone https://github.com/arendon7/AUTO-TRADE.git
cd AUTO-TRADE
git switch reconstruction/r6-external-paper-protection
bash scripts/mac_start.sh
bash scripts/mac_start.sh rehearsal
bash scripts/mac_start.sh init-workspace "$HOME/AUTO-TRADE-R6/workspace-001"
bash scripts/mac_start.sh readiness "$HOME/AUTO-TRADE-R6/workspace-001"
```

Resultado esperado del workspace nuevo:

```text
ACCOUNT_PREFLIGHT_REQUIRED
```

Hasta aquí:

- credenciales usadas: NO;
- broker network I/O: NO;
- broker write: NO;
- capital authority: NONE;
- `R6_EXTERNAL_PAPER_WRITE=DISABLED`;
- LIVE: BLOCKED.

## Nivel 0 — Clonar y preparar el Mac

Mientras PR #14 siga DRAFT, usar la rama R6 explícita:

```bash
git clone https://github.com/arendon7/AUTO-TRADE.git
cd AUTO-TRADE
git switch reconstruction/r6-external-paper-protection
bash scripts/mac_start.sh
```

`mac_start.sh` es el entrypoint recomendado. Si `.venv` todavía no existe llama a `bash scripts/mac_bootstrap.sh`; después entra únicamente a la consola segura.

El bootstrap:

- exige Python 3.12+;
- crea `.venv`;
- instala el proyecto y dependencias de desarrollo;
- compila código/tests/scripts;
- ejecuta boundaries deterministas R6 y Mac;
- ejecuta pruebas focalizadas del recorrido PAPER y de Safe Start;
- elimina de su propio entorno las variables de credenciales Alpaca;
- exige que la shell no llegue con `R6_EXTERNAL_PAPER_WRITE=ENABLED`;
- fuerza `R6_EXTERNAL_PAPER_WRITE=DISABLED` dentro del bootstrap;
- no llama al broker y no envía órdenes.

Para una validación completa adicional:

```bash
.venv/bin/python -m pytest
```

## Nivel 1 — Rehearsal local

Este nivel no requiere cuenta Alpaca ni credenciales.

Forma recomendada:

```bash
bash scripts/mac_start.sh rehearsal
```

Equivalente directo:

```bash
bash scripts/mac_rehearsal.sh
```

El rehearsal:

- se niega a correr si la shell tiene `R6_EXTERNAL_PAPER_WRITE=ENABLED`;
- exige la `.venv` ya creada por bootstrap;
- elimina las variables de credenciales Alpaca dentro del proceso;
- fuerza `R6_EXTERNAL_PAPER_WRITE=DISABLED`;
- ejecuta Mac Doctor, contract/debt checks y boundaries R6/Mac;
- ejecuta las pruebas focalizadas del recorrido PAPER;
- prueba también que Safe Start y la creación de workspace no pueden adquirir autoridad;
- no ejecuta account preflight, market-data preflight, decisión humana ni writer;
- no usa broker I/O.

Boundaries principales:

```bash
.venv/bin/python scripts/check_r6_authority.py
.venv/bin/python scripts/check_r6_live_deny_boundary.py
.venv/bin/python scripts/check_r6_market_data_boundary.py
.venv/bin/python scripts/check_r6_operational_lifecycle_boundary.py
.venv/bin/python scripts/check_r6_operational_execution_boundary.py
.venv/bin/python scripts/check_r6_readiness_boundary.py
.venv/bin/python scripts/check_mac_rehearsal_boundary.py
.venv/bin/python scripts/check_mac_safe_console_boundary.py
```

Resultado esperado: PASS. Ninguno de esos comandos debe usar credenciales o broker I/O.

## Nivel 2 — Crear e inspeccionar un workspace privado

No crear workspaces dentro del repositorio. La ruta recomendada es `$HOME/AUTO-TRADE-R6/...`.

```bash
export R6_WORKSPACE="$HOME/AUTO-TRADE-R6/workspace-001"
bash scripts/mac_start.sh init-workspace "$R6_WORKSPACE"
```

El inicializador:

- rechaza `R6_EXTERNAL_PAPER_WRITE=ENABLED`;
- rechaza una shell con credenciales Alpaca cargadas;
- rechaza rutas dentro del repositorio;
- rechaza directorios existentes no vacíos;
- crea sólo el directorio privado `0700`;
- no crea `core.sqlite3`, submission DB, permit DB ni operator DB;
- no usa red;
- termina en `ACCOUNT_PREFLIGHT_REQUIRED`.

Inspección read-only:

```bash
bash scripts/mac_start.sh readiness "$R6_WORKSPACE"
```

También puede usarse el doctor:

```bash
bash scripts/mac_start.sh doctor --workspace "$R6_WORKSPACE"
```

El inspector market-aware puede reportar, entre otros:

- `ACCOUNT_PREFLIGHT_REQUIRED`
- `MARKET_DATA_PREFLIGHT_REQUIRED`
- `PREPARATION_REQUIRED`
- `HUMAN_DECISION_REQUIRED`
- `EXPLICIT_EXECUTION_DECISION_REQUIRED`
- `EXPLICIT_EXECUTION_RESUME_REQUIRED`
- `RECONCILIATION_REQUIRED`
- `EVIDENCE_CAPTURE_REQUIRED`
- `QUALIFICATION_REVIEW_REQUIRED`
- `BLOCKED_INCONSISTENT_STATE`

Aunque el siguiente paso sea una decisión de ejecución, el report conserva:

- `execution_authorized=false`
- `broker_write_performed=false`
- `network_used=false`
- `capital_authority=NONE`
- `profitability_claim=false`
- `live_trading=BLOCKED`

## Nivel 3 — Configurar Alpaca PAPER y hacer los GET explícitos

Este es el primer nivel que usa red externa. Sigue sin existir autoridad de órdenes.

Crear configuración local:

```bash
cp .env.example .env
chmod 600 .env
```

Editar `.env` y colocar únicamente las credenciales de la cuenta **Alpaca PAPER**:

```text
APCA_API_KEY_ID=...
APCA_API_SECRET_KEY=...
R6_EXTERNAL_PAPER_WRITE=DISABLED
```

Cargar variables sólo cuando se vaya a hacer un preflight:

```bash
set -a
source .env
set +a
```

### 3A — Account preflight: GET `/v2/account`

Desde Safe Start:

```bash
bash scripts/mac_start.sh account-preflight \
  "$R6_WORKSPACE" \
  '<ALPACA_PAPER_ACCOUNT_ID>'
```

Comando subyacente auditado:

```bash
.venv/bin/python scripts/r6_external_paper_preflight.py \
  --workspace "$R6_WORKSPACE" \
  --expected-account-id '<ALPACA_PAPER_ACCOUNT_ID>' \
  --allow-paper-account-read
```

Este comando:

- sólo tiene autoridad para `GET /v2/account`;
- valida cuenta/entorno PAPER;
- guarda un artifact sanitizado;
- no persiste API key/secret;
- no contiene API de escritura de órdenes.

Después:

```bash
bash scripts/mac_start.sh readiness "$R6_WORKSPACE"
```

El siguiente estado normal debe ser:

```text
MARKET_DATA_PREFLIGHT_REQUIRED
```

### 3B — Equity market-data preflight: un GET IEX

Elegir explícitamente el símbolo US equity del futuro canary. Ejemplo:

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

Este comando:

- exige el opt-in `--allow-paper-market-read`;
- se niega a correr si `R6_EXTERNAL_PAPER_WRITE=ENABLED`;
- usa `data.alpaca.markets`;
- fija `feed=iex` y `currency=USD`;
- realiza un solo GET del snapshot equity;
- valida bid/ask/last, timestamps, freshness y skew quote/trade;
- usa como `MarketSnapshot.observed_at` el componente más antiguo de quote/trade;
- guarda `market_snapshot.json` sanitizado e idempotente;
- no persiste key/secret;
- no tiene acceso al trading host ni a `/v2/orders`;
- no concede autoridad de capital ni ejecución.

Volver a consultar readiness:

```bash
bash scripts/mac_start.sh readiness "$R6_WORKSPACE"
```

Con account + market evidence válidos y sin package todavía, el siguiente estado normal debe ser:

```text
PREPARATION_REQUIRED
```

Si se intenta mezclar un `market_snapshot.json` con un package ya preparado cuyo `market_fingerprint` sea distinto, readiness falla cerrado.

## Nivel 4 — Preparación offline

La preparación construye un único canary acotado, lo liga a RiskDecision, MarketSnapshot, Safety, OMS, Portfolio, Health, account attestation, bracket y permit, y termina en:

```text
OPERATOR_DECISION_REQUIRED
```

No envía ninguna orden.

**Estado actual:** `PaperOperationalCanaryPreparer` está implementado y certificado, pero el primer canary externo no debe depender de fabricar `core.sqlite3`, `RiskDecision`, Portfolio o Health a mano.

Antes de habilitar este nivel desde la consola Mac estamos cerrando dos piezas:

1. **PAPER portfolio/account reconciliation preflight GET-only**: comprobar posiciones y órdenes abiertas; para el primer canary la ruta preferida será cuenta PAPER vacía, fail-closed si existe exposición desconocida.
2. **R6 candidate → Safety launcher**: construir el `OrderIntent` candidato de forma explícita pero producir el `RiskDecision` exclusivamente con Capital Safety Kernel + estado durable + market evidence, y después invocar el preparer certificado.

Hasta que esas dos piezas estén cerradas y triple-certificadas:

- no fabricar manualmente JSON de `RiskDecision`/MarketSnapshot;
- no editar `core.sqlite3` para forzar un estado;
- no copiar artifacts entre attempts para avanzar readiness;
- no usar el writer para “probar que funciona”.

Para rehearsal, el servicio de preparación ya se prueba automáticamente con la suite. Para el primer canary externo real, completar primero esta inicialización autoritativa y volver a pasar Core/R6/Knowledge.

## Nivel 5 — Decisión humana separada

Sólo para un workspace preparado y con provenance vigente:

```bash
.venv/bin/python scripts/r6_issue_operator_decision.py \
  --workspace "$R6_WORKSPACE" \
  --operator-id '<OPERADOR>'
```

El comando exige TTY y challenge exacto. Registra una decisión humana de corta duración. **No envía una orden.**

## STOP — antes de cualquier orden PAPER real

No ejecutar el siguiente nivel sólo por haber llegado hasta aquí.

El canary real debe ser una decisión explícita separada después de revisar:

1. readiness del workspace;
2. cuenta PAPER exacta;
3. portfolio PAPER reconciliado y exposición conocida;
4. market evidence IEX exacta y fresca;
5. símbolo, side, quantity/notional y limit price;
6. take-profit y stop-loss;
7. Safety/OMS/Portfolio/Health vigentes;
8. notional máximo del canary;
9. que no exista `UNKNOWN` pendiente;
10. que `R6_EXTERNAL_PAPER_WRITE` siga `DISABLED` hasta el instante de la decisión final.

## Nivel 6 — Ejecución PAPER single-shot

Este comando existe, pero **no debe ejecutarse durante instalación/rehearsal**:

```bash
R6_EXTERNAL_PAPER_WRITE=ENABLED \
.venv/bin/python scripts/r6_execute_paper_canary.py \
  --workspace "$R6_WORKSPACE" \
  --execute-paper-canary
```

Además del flag y la variable de entorno, exige TTY y un challenge exacto ligado al `attempt_id` y `package_hash`.

El writer puede realizar como máximo el POST certificado de ese attempt. Antes del I/O vuelve a verificar los guards finales. Si la situación del POST queda ambigua, el estado durable pasa a `UNKNOWN` y queda prohibido repetir el POST a ciegas.

## Nivel 7 — Reconciliación, trade_updates y qualification

Después de un POST real:

- `UNKNOWN` → reconciliación GET-only por `client_order_id`;
- broker order encontrado → GET nested y validación exacta del bracket;
- `trade_updates` PAPER → stream autenticado receive-only;
- eventos → ledger durable/tamper-evident;
- terminalidad + fills + slippage + latency → `PaperQualificationEvaluator`;
- qualification PAPER mantiene `capital_authority=NONE`, `profitability_claim=false`, `live_trading=BLOCKED`.

Los debts `TD-R6-001..006` sólo pueden cerrarse con esta evidencia externa real, no con mocks ni tests.

## Si algo no coincide

No editar SQLite, hashes, manifests ni artifacts para forzar el siguiente estado. Ejecutar readiness, conservar el workspace y revisar el error. Un estado inconsistente es deliberadamente fail-closed.

## Estado de seguridad al terminar este runbook sin Nivel 6

- external PAPER order sent: **NO**
- capital authority: **NONE**
- LIVE trading: **BLOCKED**
- broker write: **NO**
