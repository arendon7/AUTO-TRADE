# ESTADO ACTUAL

Fecha: 2026-08-10
Fase: Legacy Recovery / Canon Reconciliation

## Verdad actual del repositorio
`main` está en `721dd64...` y contiene la reconstrucción Foundation v0.3 certificada en este repo:
- SQLite/WAL durable state;
- hash-chained Event Ledger;
- OMS/idempotency cross-process;
- versioned portfolio + atomic risk reservations;
- persistent kill switch;
- DurablePaperBroker;
- startup reconciliation y crash recovery;
- 70 tests PASS y 86.38% coverage en la certificación v0.3.

PR #4 contiene una reconstrucción Research v0.4 con 122 tests PASS y 89.40% coverage, pero fue convertida a **DRAFT/FALLBACK** y no debe fusionarse todavía.

## Descubrimiento histórico crítico
Se recuperaron artefactos de certificación que demuestran que la conversación/proyecto anterior ya había avanzado hasta **AUTO TRADING IA v0.28.0**.

Última certificación histórica verificada:
- track `Broker-Side Protection Sandbox`;
- runtime `SIMULATION`;
- 302/302 tests PASS;
- 207 JSON Schemas;
- Event Ledger válido;
- clean ZIP extraction + compile + health PASS;
- External PAPER canary/evidence y broker-side bracket protection sandbox;
- LIVE capital authority `NONE`.

Los reportes intermedios verifican además Market Data Foundation, Strategy DSL, Fast/Canonical Backtest, Validation, Trial Ledger, PBO/DSR, Final HOLDOUT, Capital Safety, OMS/reconciliation, Local Paper, external PAPER gateway, real-data intake, Research Control Center, Portfolio Research/Robustness, Health/Drift, Defensive Health Bridge, Shadow/Forward Evidence y Portfolio Shadow.

## Estado del source histórico
El package/ZIP v0.28 **todavía no está recuperado**.

Búsqueda realizada sin coincidencia del source:
- File Library: conserva reportes/certificaciones, no el ZIP;
- Google Drive: sin package coincidente;
- SharePoint/OneDrive: sin package coincidente;
- GitHub `arendon7/AUTO-TRADE`: repo reciente, no contiene el árbol histórico.

Por integridad no se reconstruirá el v0.28 inventando source desde los reportes.

## Arquitectura de memoria ya implantada
- `SOURCE_OF_TRUTH.md`: precedencia canónica y regla de no-regresión.
- `LEGACY_V028_RECOVERY.md`: mapa histórico verificado.
- `AGENTS.md`: startup obligatorio desde el canon.
- `knowledge/`: vault Obsidian humano/canónico.
- `graphify-out/`: grafo estructural regenerable cuando se disponga de runtime Graphify.
- `RECOVER_LEGACY_V028.md`: procedimiento de importación y recertificación del source.
- Git + CI/tests: historial y evidencia de comportamiento.

## Estado de capital
**LIVE TRADING: BLOQUEADO.**
La existencia histórica de external PAPER y broker-side protection no concede autoridad LIVE.

## Próximo hito
1. Recuperar/importar el source package v0.28 si aparece.
2. Recertificarlo contra su evidencia histórica (302 tests / 207 schemas o explicar drift).
3. Ejecutar Graphify deep sobre el árbol recuperado.
4. Reconciliar únicamente después las mejoras útiles de Foundation v0.3 / PR #4.
5. Si el source resulta definitivamente irrecuperable, abrir ADR específico para reconstrucción equivalente v0.28 desde evidencia, sin downlevel silencioso.
