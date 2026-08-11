# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **v0.28R R0–R5 certified; R5 PR #13 pending integration**.

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md`
5. `knowledge/00_CANON/debt_register.json`
6. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`
7. `knowledge/60_EVIDENCE/R5_CERTIFICATION.json`
8. `knowledge/60_EVIDENCE/R5_LIVE_CLOSED_KLINE_STREAM_EVIDENCE.json`

## R5 certification
Basis `0d4f75d083a055b83646bb861f08731aecace560` from post-R4-green `main` `c294aa69f35b64559e3aea58a1c0661e66599db8`: **606 tests PASS / 86.49% coverage**; 10 contracts; Research Authority, R5 Authority, Debt Register and Knowledge Contract PASS. R5 blocking debt open: 0.

## Regla operativa inmediata
No empezar R6 desde la rama R5. Primero PR #13 -> merge -> CI verde sobre SHA exacto de `main`; después crear R6 y registrar su deuda.

## Próximo track
R6 = external Alpaca PAPER gateway + bounded canary + PAPER evidence qualification + broker-side protection. PAPER only; LIVE remains forbidden.

## Authority
AI/research/Portfolio Manager/stream/shadow/forward no tienen autoridad de ejecución. Safety + OMS continúan siendo fronteras deterministas obligatorias.

**LIVE TRADING: BLOQUEADO.**
