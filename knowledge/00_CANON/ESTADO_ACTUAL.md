# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-10
Estado canónico: **v0.28R reconstruction — R0–R4 CERTIFIED; PR #11 integration pending**

## Fuente de verdad
El proyecto histórico v0.28 alcanzó mayor madurez que la reconstrucción inicial, pero su source package no fue recuperado. La ruta activa es la reconstrucción equivalente v0.28R regida por `SOURCE_OF_TRUTH.md`, `RECONSTRUCTION_V028R_MATRIX.md` y `debt_register.json`.

## Certificaciones actuales
- R0: deterministic safety/durability baseline — PASS.
- R1: Market Data + Strategy DSL + Research Integrity — PASS.
- R2: Capital Safety + OMS maturity — PASS.
- R3: bounded real-data/research governance — PASS e integrado en `main` `c585a84b5197076b210723bb70980b828e4e3026`.
- R4: portfolio/regimes/health governance — **PASS en branch**, certificado sobre `350efd43ac133c95a1997b4a821a2e0bab4afaf2` con 480 tests PASS / 86.58% coverage y todos los gates verdes.

## R4 certificado
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

## Estado de integración
- PR #11 permanece pendiente de merge hasta que el head canónico de cierre vuelva a pasar Core Safety + Knowledge Contract.
- Después del merge, el SHA exacto de `main` debe recertificarse antes de crear R5.
- No iniciar R5 desde la rama pre-merge.

## Deuda
- R4 P0/P1/P2 OPEN: **0**.
- `TD-OPS-001` Graphify P3/OPS: OPEN, no bloqueante.

## Próximo track
**R5 — read-only streaming + synchronized shadow + forward evidence.**
No ejecutar implementación R5 hasta completar integración/post-merge de R4.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER authority añadida por R4: NONE.
