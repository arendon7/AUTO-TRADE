# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-12
Estado: **R0–R5 certified; R6 structurally closed / PRE-FIRST-CANARY; Mac guided UAT active**

## Base y rama activa
- post-R5-green `main`: `75dcbef65b061f742745ba7be0665521967e0587`;
- R6 base: `reconstruction/r6-external-paper-protection`;
- Mac UX: `work/r6-mac-control-center`;
- PR #29: DRAFT.

Último checkpoint UX completamente certificado antes de sincronizar este handoff:
`2c2e5ebef9bf01f13cf9bed477e670ba79fa2b29`.

5/5 PASS:
- Core Safety `31649170421`;
- Knowledge Contract `31649170446`;
- Mac Rehearsal `31649170429`;
- R6 PAPER Authority `31649170448`;
- Mac FULL Standalone `31649170468`.

FULL Standalone pasó build, Control Center safety boundary, instalación desde árbol quarantined, localhost smoke y self-heal tanto en arm64 como x86_64.

## UAT real observada
En el paquete Mac anterior el operador consiguió abrir la app, Doctor PASS y Capital Safety rehearsal PASS. El defecto principal fue de producto/UX: todos los botones técnicos podían pulsarse sin respetar dependencias.

Eso produjo bloqueos correctos pero incomprensibles:
- Preparation antes de candidate;
- Candidate antes de account attestation;
- Review receipt antes de preparation/first human decision.

No fue una regresión de Python ni del Safety Kernel.

## Cambio Mac ya implementado
`web/mac_dashboard.html` ahora funciona como guía de ensayo:
- `Empieza aquí`;
- `1 · Probar la app sin broker` ejecuta workspace -> Doctor -> Capital Safety rehearsal;
- `2 · Conectar Alpaca PAPER` guía Account -> Asset -> Flat -> Market;
- gating visual/deshabilitación de acciones fuera de orden;
- Candidate/Preparation sólo cuando readiness los habilita;
- errores conocidos traducidos a explicación operacional;
- traceback disponible sólo como diagnóstico técnico;
- estado/timeline explicado en lenguaje de usuario;
- `Strategy Lab` visible como siguiente capa, pero no falsamente presentado como disponible.

La frontera de seguridad no cambió:
- localhost only;
- external PAPER write disabled;
- no Final Freshness/staging/writer/POST/LIVE desde dashboard;
- credenciales PAPER efímeras en la página;
- Capital Safety/OMS siguen siendo autoridad determinista.

## Cómo probar la próxima build Mac
1. abrir la instalación FULL;
2. pulsar `Probar la app sin broker`;
3. exigir PASS en workspace, Doctor y Capital Safety;
4. comprobar que la UI no deje saltar gates;
5. si se decide probar broker: introducir credenciales PAPER y Account ID;
6. completar Account -> Asset -> Flat -> Market;
7. construir connectivity candidate y preparation sólo cuando se habiliten;
8. STOP antes de H1 salvo decisión humana separada para la primera canary real.

## Alpaca / TradingView / estrategia
R6 se ensaya con AUTO-TRADE + Alpaca PAPER. TradingView no es requisito.

TradingView puede incorporarse después como visualización o input advisory, nunca como ejecución bypass.

R1–R5 contienen capacidades certificadas de research/backtest/holdout/walk-forward/portfolio/Health/shadow/forward, pero todavía no están expuestas como experiencia Mac amigable. Después de estabilizar esta UAT R6, el siguiente producto es `Strategy Lab`, que debe reutilizar esas capacidades reales y preservar toda la gobernanza de promoción.

## Deuda
CLOSED structurally: `TD-R6-007..013`.

OPEN/blocking — requieren evidencia externa PAPER real:
- `TD-R6-001` account/environment;
- `TD-R6-002` submit ambiguity/idempotency/reconciliation;
- `TD-R6-003` bounded real PAPER canary;
- `TD-R6-004` terminality/fills/slippage/qualification;
- `TD-R6-005` nested US-equity bracket;
- `TD-R6-006` authenticated PAPER trade_updates.

OPEN/nonblocking:
- `TD-OPS-001` Graphify semantic/deep evidence.

## Capital
- external PAPER order sent: **0**;
- capital authority desde Control Center: **NONE**;
- connectivity canary != strategy edge;
- PAPER != profitability proof;
- LIVE fuera de R6/v0.28R.

**LIVE TRADING: BLOQUEADO.**
