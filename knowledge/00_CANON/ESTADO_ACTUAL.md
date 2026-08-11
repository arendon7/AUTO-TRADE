# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado canónico: **v0.28R reconstruction — R0–R4 CERTIFIED; R4 integrated but post-merge recertification temporarily FAILED**

## Fuente de verdad
El proyecto histórico v0.28 alcanzó mayor madurez que la reconstrucción inicial, pero su source package no fue recuperado. La ruta activa es la reconstrucción equivalente v0.28R regida por `SOURCE_OF_TRUTH.md`, `RECONSTRUCTION_V028R_MATRIX.md` y `debt_register.json`.

## Certificaciones actuales
- R0: deterministic safety/durability baseline — PASS.
- R1: Market Data + Strategy DSL + Research Integrity — PASS.
- R2: Capital Safety + OMS maturity — PASS.
- R3: bounded real-data/research governance — PASS e integrado en `main` `c585a84b5197076b210723bb70980b828e4e3026`.
- R4: portfolio/regimes/health governance — PASS en branch, certificado sobre `350efd43ac133c95a1997b4a821a2e0bab4afaf2` con **479 tests PASS / 86.45% coverage**.

## Integración R4
PR #11 fue fusionado por squash en `main` como `aa6d80dc1682967edef367f726a620e41c0af118`.

Post-merge:
- Knowledge Contract run `31461659067`: PASS.
- Core Safety run `31461659063`: FAIL.
- Fallos: contrato permanente R4 esperaba erróneamente `480` frente a evidencia real `479`, y el árbol fusionado conservó `.github/workflows/r4-final-readiness-one-shot.yml`.

Estos son defectos de integración/certificación, no regresiones funcionales de Portfolio/Regime/Health. R5 permanece bloqueado hasta que el hotfix quede fusionado y el SHA exacto de `main` recertifique verde.

## R4 certificado funcionalmente
Incluye:
- Instrument Master autoritativo/versionado;
- Portfolio State/fill durability audit;
- dependence/correlation + cross-strategy/cluster budgets;
- allocation perturbation + leave-one-out con exact Decimal normalization;
- TRAIN/DEVELOPMENT-only regime calibration;
- Strategy/Portfolio Health & Drift durable;
- retry-safe/tamper-evident recovery acknowledgements;
- authoritative unsynced Health overlay;
- reduce/block-only Defensive Health Bridge integrado con Safety/OMS;
- deterministic advisory-only Portfolio Manager/sizing con post-Health/post-venue revalidation;
- permanent Research/Advisory Authority CI boundary.

## Deuda
- R4 P0/P1/P2 OPEN: **0**.
- `TD-OPS-001` Graphify P3/OPS: OPEN, no bloqueante.

## Próximo track
**R5 — read-only streaming + synchronized shadow + forward evidence.**
No iniciar R5 hasta completar hotfix + recertificación post-merge verde del SHA exacto de `main`.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER authority añadida por R4: NONE.
