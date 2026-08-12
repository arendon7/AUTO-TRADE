# AUTO-TRADE — Mac PAPER Runbook

Estado objetivo de este runbook: poder abrir AUTO-TRADE en un Mac, validar el sistema localmente y avanzar de forma explícita desde **cero red al broker** hasta, en una fase posterior y separada, un canary real de Alpaca PAPER.

## Regla operativa

- R6 es **PAPER-only**.
- LIVE permanece bloqueado.
- La IA no puede otorgar autoridad de capital ni aprobación humana.
- `UNKNOWN` nunca se reintenta con otro POST: va a reconciliación GET-only.
- Una evidencia PAPER no demuestra rentabilidad ni autoriza LIVE.
- Ningún paso de instalación o diagnóstico envía órdenes.

## Nivel 0 — Clonar y preparar el Mac

Mientras PR #14 siga DRAFT, usar la rama R6 explícita:

```bash
git clone https://github.com/arendon7/AUTO-TRADE.git
cd AUTO-TRADE
git switch reconstruction/r6-external-paper-protection
bash scripts/mac_bootstrap.sh
```

El bootstrap:

- exige Python 3.12+;
- crea `.venv`;
- instala el proyecto y dependencias de desarrollo;
- compila el código;
- ejecuta boundaries deterministas R6;
- ejecuta un conjunto focalizado de pruebas de rehearsal;
- elimina de su propio entorno las variables de credenciales Alpaca;
- fuerza `R6_EXTERNAL_PAPER_WRITE=DISABLED`;
- no llama al broker y no envía órdenes.

Para una validación completa adicional:

```bash
.venv/bin/python -m pytest
```

## Nivel 1 — Rehearsal local

Este nivel no requiere cuenta Alpaca ni credenciales.

Pruebas relevantes:

```bash
.venv/bin/python -m pytest -q \
  tests/test_r6_paper_readiness.py \
  tests/test_r6_paper_readiness_failclosed.py \
  tests/test_r6_operational_execute.py \
  tests/test_r6_operational_execute_validation.py \
  tests/test_r6_execute_paper_canary_cli.py
```

Boundaries principales:

```bash
.venv/bin/python scripts/check_r6_authority.py
.venv/bin/python scripts/check_r6_live_deny_boundary.py
.venv/bin/python scripts/check_r6_operational_lifecycle_boundary.py
.venv/bin/python scripts/check_r6_operational_execution_boundary.py
.venv/bin/python scripts/check_r6_readiness_boundary.py
```

Resultado esperado: PASS. Ninguno de estos comandos debe usar credenciales o broker I/O.

## Nivel 2 — Inspector de readiness

Cuando exista un workspace R6, su estado se consulta sin red ni capacidad de ejecución:

```bash
.venv/bin/python scripts/r6_inspect_paper_readiness.py \
  --workspace "$HOME/AUTO-TRADE-R6/workspace-001"
```

El inspector puede reportar, entre otros:

- `ACCOUNT_PREFLIGHT_REQUIRED`
- `PREPARATION_REQUIRED`
- `HUMAN_DECISION_REQUIRED`
- `EXPLICIT_EXECUTION_DECISION_REQUIRED`
- `EXPLICIT_EXECUTION_RESUME_REQUIRED`
- `RECONCILIATION_REQUIRED`
- `EVIDENCE_CAPTURE_REQUIRED`
- `QUALIFICATION_REVIEW_REQUIRED`
- `BLOCKED_INCONSISTENT_STATE`

Aunque el siguiente paso sea una decisión de ejecución, el report siempre conserva:

- `execution_authorized=false`
- `broker_write_performed=false`
- `network_used=false`
- `capital_authority=NONE`
- `profitability_claim=false`
- `live_trading=BLOCKED`

## Nivel 3 — Configurar Alpaca PAPER para el primer GET real

Este es el primer nivel que usa red del broker, pero **sólo permite `GET /v2/account`**.

Crear configuración local a partir del template:

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

Cargar esas variables en la terminal sólo cuando se vaya a hacer el preflight:

```bash
set -a
source .env
set +a
```

Crear un workspace fuera del repositorio, por ejemplo:

```bash
mkdir -p "$HOME/AUTO-TRADE-R6"
export R6_WORKSPACE="$HOME/AUTO-TRADE-R6/workspace-001"
```

Ejecutar el preflight GET-only:

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
- no persiste la API secret;
- no contiene API de escritura de órdenes.

Después:

```bash
.venv/bin/python scripts/r6_inspect_paper_readiness.py \
  --workspace "$R6_WORKSPACE"
```

El siguiente estado normal debe ser `PREPARATION_REQUIRED`.

## Nivel 4 — Preparación offline

La preparación construye un único canary acotado, lo liga a RiskDecision, MarketSnapshot, Safety, OMS, Portfolio, Health, account attestation, bracket y permit, y termina en:

```text
OPERATOR_DECISION_REQUIRED
```

No envía ninguna orden.

**Estado actual de ergonomía:** el servicio `PaperOperationalCanaryPreparer` y sus pruebas están implementados y certificados, pero la rama R6 todavía está terminando de exponer este paso como un comando Mac de operador de alto nivel. Hasta que ese launcher esté cerrado y certificado, no fabricar manualmente JSONs ni editar `core.sqlite3` para “avanzar” el workspace.

Para rehearsal, este nivel ya se prueba automáticamente con la suite. Para el primer canary externo real, completar primero el launcher de preparación y volver a pasar Core/R6/Knowledge.

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
3. símbolo, side, quantity/notional y limit price;
4. take-profit y stop-loss;
5. Safety/OMS/Portfolio/Health vigentes;
6. notional máximo del canary;
7. que no exista `UNKNOWN` pendiente;
8. que `R6_EXTERNAL_PAPER_WRITE` siga `DISABLED` hasta el instante de la decisión final.

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
