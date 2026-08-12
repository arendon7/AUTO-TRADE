# AUTO-TRADE — Mac PAPER Runbook

Objetivo: operar R6 en un Mac de forma reproducible y fail-closed hasta el borde de una primera **CONNECTIVITY_CANARY** en Alpaca PAPER. Este documento no autoriza un POST real y no convierte una canary de conectividad en evidencia de rentabilidad.

## Invariantes

- R6 es **PAPER-only**; LIVE permanece **BLOCKED**.
- `R6_EXTERNAL_PAPER_WRITE=DISABLED` es el estado normal.
- Finder/Safe Console no tienen comando de envío de órdenes.
- IA/research no pueden otorgar autoridad de capital ni de operador.
- `UNKNOWN` nunca se reintenta: implica reconciliación GET-only.
- Primera canary: US equity, BUY, LIMIT parent, 1 acción entera, bracket TP/SL, cuenta PAPER plana.
- `READY` en `pre-canary-status` significa únicamente “listo para el siguiente gate nombrado”; **nunca** `external_post_authorized=true`.
- PAPER connectivity evidence != Strategy Health != rentabilidad.

## Flujo R6 certificado

```text
Mac bootstrap / Doctor / rehearsal
        ↓
workspace privado
        ↓
GET account → GET asset → GET positions/open orders → GET IEX market
        ↓
CONNECTIVITY_CANARY candidate local
        ↓
offline preparation / bracket package
        ↓
PRIMERA decisión humana interactiva
        ↓
review receipt exacto, offline e inmutable
        ↓
SEGUNDA intención humana ligada al receipt
        ↓
reviewed Final Freshness: 5 GETs + Safety, ventana <= 5 s
        ↓
certified staging: OMS SUBMITTING + durable UNKNOWN-before-POST
        ↓
como máximo un POST PAPER en el mismo proceso
        ↓
reinicio/ambigüedad/UNKNOWN => GET-only reconciliation; jamás repost
        ↓
bracket verification + trade_updates + terminality/fills/slippage + qualification
```

El Finder llega únicamente a gates seguros/offline. Las dos decisiones humanas y Final Freshness se ejecutan en terminal separada. El POST no forma parte del menú Finder.

## 0 — Preparar el Mac

Mientras PR #14 siga DRAFT:

```bash
git clone https://github.com/arendon7/AUTO-TRADE.git
cd AUTO-TRADE
git switch reconstruction/r6-external-paper-protection
bash scripts/mac_start.sh
```

También puede abrirse `AUTO_TRADE_MAC.command` desde Finder. El bootstrap crea `.venv`, instala dependencias, compila y ejecuta los boundaries/rehearsals locales. No necesita broker I/O.

## 1 — Rehearsal local

```bash
bash scripts/mac_start.sh rehearsal
bash scripts/mac_start.sh safety-rehearsal
```

`safety-rehearsal` usa `CapitalSafetyKernel.evaluate(...)` real, pero es local-only: no crea staging, writer, operator authority ni broker mutation.

## 2 — Workspace privado

El workspace debe vivir fuera del repositorio y no puede ser symlink.

```bash
export R6_WORKSPACE="$HOME/AUTO-TRADE-R6/workspace-001"
bash scripts/mac_start.sh init-workspace "$R6_WORKSPACE"
bash scripts/mac_start.sh pre-canary-status "$R6_WORKSPACE"
```

Estado inicial esperado: `ACCOUNT_PREFLIGHT_REQUIRED`.

`pre-canary-status` es local, credential-free y read-only. Siempre informa:

```text
network_used=false
broker_write_performed=false
execution_authorized=false
external_post_authorized=false
capital_authority=NONE
live_trading=BLOCKED
```

Si encuentra un symlink, JSON inseguro/tampered o cadena local incompleta, devuelve `NOT_READY`. Si detecta `UNKNOWN`, devuelve exclusivamente `RECONCILIATION_ONLY`.

## 3 — Cuatro preflights PAPER GET-only

Las credenciales PAPER sólo deben cargarse para las lecturas explícitas. Mantener siempre:

```bash
export R6_EXTERNAL_PAPER_WRITE=DISABLED
export APCA_API_KEY_ID='<PAPER_KEY_ID>'
export APCA_API_SECRET_KEY='<PAPER_SECRET>'
```

No guardar secretos en el repo ni en el workspace.

### 3A — Account

```bash
bash scripts/mac_start.sh account-preflight \
  "$R6_WORKSPACE" \
  '<ALPACA_PAPER_ACCOUNT_ID>'
```

Sólo `GET /v2/account`. La evidencia queda sanitizada.

### 3B — Asset / venue

```bash
bash scripts/mac_start.sh asset-preflight "$R6_WORKSPACE" AAPL
```

Exige el asset exacto `us_equity`, `active`, `tradable`, compatible con una acción entera y con las restricciones R6 del primer canary.

### 3C — Cuenta PAPER plana

```bash
bash scripts/mac_start.sh flat-account-preflight "$R6_WORKSPACE"
```

Hace exactamente dos GETs: posiciones y órdenes abiertas. Debe probar:

```text
position_count = 0
open_order_count = 0
```

Si existe exposición o la evidencia expira, el flujo se bloquea.

### 3D — Market IEX

```bash
bash scripts/mac_start.sh market-preflight "$R6_WORKSPACE" AAPL
```

Un solo GET IEX; el símbolo debe coincidir con el asset attested.

Después:

```bash
unset APCA_API_KEY_ID APCA_API_SECRET_KEY
bash scripts/mac_start.sh pre-canary-status "$R6_WORKSPACE"
```

Estado esperado: `CONNECTIVITY_CANDIDATE_REQUIRED`.

## 4 — Candidate local CONNECTIVITY_CANARY

```bash
bash scripts/mac_start.sh build-connectivity-candidate "$R6_WORKSPACE"
```

Safe Console elimina credenciales y fuerza write disabled. Este paso crea el baseline connectivity, un `RiskDecision` real del Capital Safety Kernel y una orden OMS `VALIDATED` con propósito `CONNECTIVITY_CANARY`.

Límites estructurales del primer candidate:

- BUY;
- LIMIT;
- exactamente 1 acción;
- notional cap `min(USD 10, 0.1% portfolio_value PAPER, buying_power)`;
- un símbolo broker-attested;
- max open orders 1;
- Strategy Health no se fabrica;
- `external_post_authorized=false`;
- `capital_authority=NONE`;
- LIVE bloqueado.

Comprobar:

```bash
bash scripts/mac_start.sh pre-canary-status "$R6_WORKSPACE"
```

Estado esperado: `OFFLINE_PREPARATION_REQUIRED`.

## 5 — Preparación determinista offline

```bash
bash scripts/mac_start.sh prepare-connectivity-candidate "$R6_WORKSPACE"
```

Genera el paquete/bracket esperado y bindings durables desde el candidate. Este comando:

- rechaza workspace symlink antes de `resolve()`;
- rechaza credenciales;
- rechaza `R6_EXTERNAL_PAPER_WRITE=ENABLED`;
- no usa red;
- no crea nueva autoridad humana;
- no stagea OMS;
- no puede hacer POST.

Luego:

```bash
bash scripts/mac_start.sh pre-canary-status "$R6_WORKSPACE"
```

Estado esperado: `FIRST_HUMAN_DECISION_REQUIRED`.

## 6 — PRIMERA decisión humana — terminal separada

Este paso **no** está disponible como opción del Finder. Mantener el entorno credential-free y write-disabled:

```bash
unset APCA_API_KEY_ID APCA_API_SECRET_KEY
export R6_EXTERNAL_PAPER_WRITE=DISABLED

.venv/bin/python scripts/r6_issue_connectivity_operator_decision.py \
  --workspace "$R6_WORKSPACE" \
  --operator-id '<HUMAN_OPERATOR_ID>'
```

Requiere TTY real y escribir exactamente el challenge mostrado. No acepta pipes/CI/agentes. La decisión es corta, durable y single-use; por sí sola no stagea OMS ni autoriza POST.

## 7 — Review receipt exacto

Ahora sí puede volver al Safe Console/Finder:

```bash
bash scripts/mac_start.sh review-receipt "$R6_WORKSPACE"
```

El receipt congela exactamente lo revisado por la persona:

- symbol / side / qty;
- LIMIT parent;
- TP / SL;
- notional y effective cap;
- Safety version;
- market snapshot;
- flat account 0/0;
- hashes/fingerprints de account, asset, flat, market y preparation.

Es offline, credential-free y no autorizante. Para verlo:

```bash
python -m json.tool "$R6_WORKSPACE/connectivity_operator_review_receipt.json"
```

Después:

```bash
bash scripts/mac_start.sh pre-canary-status "$R6_WORKSPACE"
```

Estado esperado: `SECOND_HUMAN_EXECUTION_INTENT_REQUIRED`.

## 8 — SEGUNDA intención humana receipt-bound — terminal separada

También queda fuera del Finder:

```bash
unset APCA_API_KEY_ID APCA_API_SECRET_KEY
export R6_EXTERNAL_PAPER_WRITE=DISABLED

.venv/bin/python scripts/r6_issue_connectivity_execution_intent.py \
  --workspace "$R6_WORKSPACE" \
  --operator-id '<HUMAN_OPERATOR_ID>'
```

El challenge incluye el hash del contexto **y** el hash del receipt. El binding durable prueba qué payload exacto se revisó. Aún aquí:

```text
oms_staging_authorized=false
external_post_authorized=false
capital_authority=NONE
```

`pre-canary-status` debe pasar a `REVIEWED_FINAL_FRESHNESS_REQUIRED`.

## 9 — Reviewed Final Freshness — exactamente cinco GETs

Este gate es time-sensitive y queda fuera del Finder. Cargar de nuevo sólo credenciales PAPER; mantener writer disabled:

```bash
export R6_EXTERNAL_PAPER_WRITE=DISABLED
export APCA_API_KEY_ID='<PAPER_KEY_ID>'
export APCA_API_SECRET_KEY='<PAPER_SECRET>'

.venv/bin/python scripts/r6_connectivity_bound_final_freshness.py \
  --workspace "$R6_WORKSPACE" \
  --allow-paper-final-freshness-read
```

Hace exactamente cinco lecturas: account, asset, positions, open orders e IEX snapshot; además vuelve a evaluar Safety y liga la nueva evidencia a receipt + segunda intención humana. La ventana es deliberadamente muy corta.

Este comando **no** stagea OMS y **no** autoriza POST. `pre-canary-status` puede mostrar `READY_FOR_SEPARATE_CERTIFIED_RUNTIME_REVIEW` sólo si la ventana sigue viva. Esa etiqueta jamás es autoridad de ejecución.

## 10 — STOP seguro antes del POST

Para pruebas locales/GET-only, detenerse aquí.

El runtime de staging/one-shot existe y está estructuralmente certificado, pero permanece fuera de Finder/Safe Console. No ejecutar una orden real sólo porque el status llega al último gate.

Antes de decidir una primera canary PAPER real deben revisarse, en ese mismo intento:

1. cuenta PAPER correcta y plana;
2. asset exacto y tradable;
3. market/freshness vigente;
4. payload del receipt;
5. notional y cap;
6. TP/SL del bracket;
7. ambas decisiones humanas;
8. Safety/kill/circuit state;
9. presupuesto de exactamente un intento;
10. procedimiento posterior de reconciliación/evidencia listo.

Cualquier futura ejecución real requerirá una decisión humana explícita separada. No se hará automáticamente desde ChatGPT, CI, Finder ni Safe Console.

## 11 — Semántica del one-shot ya certificado

Cuando eventualmente se autorice una canary real:

- staging persiste OMS `SUBMITTING` + submission `UNKNOWN` **antes** del I/O;
- inmediatamente antes del I/O se releen Safety y durable UNKNOWN;
- existe como máximo un transport write fuera de cualquier retry loop;
- HTTP 2xx no se convierte por sí solo en ACK local;
- crash/reinicio/ambigüedad/UNKNOWN **jamás** reenvía la orden;
- reinicio implica GET-only reconciliation por `client_order_id`;
- una orden hallada debe pasar validación estricta de parent + nested bracket antes de ACKNOWLEDGED.

## 12 — Reconciliación y evidencia externa

Tras un futuro intento real:

```text
UNKNOWN / ambiguous -> GET-only reconciliation
broker order found   -> identity + nested bracket verification
trade_updates        -> authenticated receive-only evidence
terminality/fills    -> slippage/latency/qualification
```

`TD-R6-001..006` sólo pueden cerrarse con evidencia PAPER externa real. Ninguna qualification PAPER promociona LIVE ni demuestra rentabilidad.

## Strategy trading — ruta separada

La connectivity canary valida infraestructura broker/order/bracket/reconciliation/trade_updates. Trading basado en estrategia sigue requiriendo research/backtest/holdout/shadow/forward y Strategy/Portfolio Health reales. La autoridad `CONNECTIVITY_CANARY` nunca puede convertirse en Strategy Health ni en permiso LIVE.

## Si algo no coincide

No editar hashes, SQLite ni evidence JSON para “hacer avanzar” el estado. No copiar artifacts entre attempts. Conservar el workspace para diagnóstico; usar `pre-canary-status`/readiness y tratar cualquier inconsistencia como fail-closed.

## Estado seguro esperado antes de una decisión explícita de canary real

- external PAPER order sent: **NO**
- external PAPER order enviado por el proyecto: **0**
- external POST authority desde Finder/Safe Console: **NO**
- capital authority desde status/receipt: **NONE**
- LIVE trading: **BLOCKED**
