# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **v0.28R R0–R4 certified; R4 PR #11 pending integration**.

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md`
5. `knowledge/00_CANON/debt_register.json`
6. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`
7. `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`

## Certificación R4
Basis `350efd43ac133c95a1997b4a821a2e0bab4afaf2`: 480 tests PASS /86.58% coverage; 10 contracts; authority/debt/knowledge gates PASS. R4 blocking debt open: 0.

## Regla operativa inmediata
No empezar R5 desde la rama R4. Primero PR #11 -> merge -> CI verde sobre SHA exacto de `main`; después crear rama R5 desde ese SHA.

## Próximo track
R5 = read-only closed-kline streaming + gap/idempotency semantics + synchronized shadow + forward evidence without HOLDOUT.

## Authority
AI/research/Portfolio Manager no tienen autoridad de ejecución. Safety + OMS continúan siendo fronteras obligatorias. External PAPER/LIVE no está habilitado.

**LIVE TRADING: BLOQUEADO.**
