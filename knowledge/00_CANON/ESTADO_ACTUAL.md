# ESTADO ACTUAL

Fecha: 2026-08-10
Fase: v0.28R Reconstruction — R1 active

## Base ejecutable actual
`main` contiene Foundation v0.3 reconstruida y certificada:
- SQLite/WAL durable state;
- hash-chained Event Ledger;
- OMS/idempotency cross-process;
- versioned portfolio + atomic risk reservations;
- persistent kill switch;
- DurablePaperBroker;
- startup reconciliation + crash recovery;
- coverage gate 85%.

Esta base constituye **R0** de la nueva reconstrucción.

## Decisión sobre el source histórico
El source ZIP/tree v0.28 no pudo recuperarse y el usuario confirma que no dispone de una copia recuperable. Ya no se mantiene el proyecto bloqueado esperando ese paquete.

Se adopta ADR-0006: reconstrucción equivalente **v0.28R**, capability-by-capability, usando la evidencia histórica como especificación de invariantes y no como código.

## Evidencia histórica conservada
`LEGACY_RELEASE_MATRIX.md` conserva tracks verificables hasta v0.28, incluyendo:
- research/validation/HOLDOUT;
- Trial Ledger, PBO/DSR y Tournament;
- Capital Safety/OMS/reconciliation;
- real market-data intake;
- portfolio robustness;
- health/drift + Defensive Health Bridge;
- closed-kline stream;
- synchronized shadow/forward evidence;
- external PAPER Canary/Evidence;
- broker-side equity bracket protection.

Última referencia histórica: v0.28 con 302 tests PASS / 207 schemas, runtime SIMULATION y LIVE authority NONE. Esos números no son cuotas; los invariantes son el objetivo.

## Matriz de reconstrucción
`RECONSTRUCTION_V028R_MATRIX.md` es ahora el backlog canónico de equivalencia.

Tracks:
- R0 Foundation durable: base actual.
- R1 Market Data + Strategy DSL + Research Engine: **ACTIVE**.
- R2 Capital Safety + OMS maturity.
- R3 Real market data + research governance.
- R4 Portfolio robustness + health.
- R5 Streaming + shadow/forward evidence.
- R6 External PAPER + broker-side protection.

`v0.28R` solo se declara cuando R0–R6 estén PASS sin P0/P1 conocidos.

## PR #4
PR #4 contiene una base Research v0.4 útil: market contracts, event-driven backtester, explicit costs, temporal splits, protected holdout, experiment registry, walk-forward y robustness gates. Su último head certificado reportó 122 tests PASS y 89.40% coverage.

Deja de ser un fallback congelado. Pasa a ser **candidato R1**, pero no se fusiona hasta completar las filas R1 faltantes, especialmente safe Strategy DSL, moving-block bootstrap, sample adequacy/validation completeness y sincronización del canon actual.

## Graphify + Obsidian
La integración fue corregida:
- Graphify semantic/deep build se ejecuta dentro de un coding assistant soportado;
- `graphify-out/SOURCE_SHA` controla frescura;
- este ChatGPT no afirma haber generado un grafo inexistente;
- Obsidian `knowledge/` + Git/CI mantienen continuidad y evidencia.

## Deuda conocida actual
No se oculta como deuda cerrada:
- lifecycle completo partial fill/cancel/replace se cerrará en R2;
- source histórico exacto seguirá perdido, pero ya no es dependencia del plan;
- Graphify graph todavía debe ser generado en runtime/asistente compatible;
- R1 aún está PARTIAL hasta completar y certificar PR #4.

## Estado de capital
**LIVE TRADING: BLOQUEADO.**
La reconstrucción v0.28R no constituye una promoción LIVE.

## Próximo hito
Completar R1 sin deuda P0/P1 y certificarlo sobre `main` antes de iniciar R2.
