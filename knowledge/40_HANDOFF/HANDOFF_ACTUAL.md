# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado: **R4 branch certified; PR #11 integration pending**

## Base integrada conocida
R3 está integrado y post-merge certificado en `main` `c585a84b5197076b210723bb70980b828e4e3026`.

## R4
Branch: `reconstruction/r4-portfolio-health`
PR: #11
Certification basis: `350efd43ac133c95a1997b4a821a2e0bab4afaf2`
Evidence: `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`
Result: **479 tests PASS / 86.45% coverage**, 10 contracts, Research/Advisory Authority PASS, Debt Register PASS, Knowledge Contract PASS.

Todos los P0/P1/P2 conocidos de R4 (`TD-R4-001..014`) están CLOSED. Todas las filas requeridas R4 de la capability matrix están PASS.

## Invariantes de cierre que no se deben perder
- Instrument Master autoritativo separado de research metadata.
- exact Decimal normalization; no tolerance weakening.
- Health recovery explicit + retry-safe + ACK-chain tamper-evident.
- unsynced authoritative worsening tightens immediately.
- Defensive Health Bridge automatic actions reduce/block only.
- Portfolio Manager advisory-only; no OrderIntent/OMS/broker authority.
- Safety + OMS remain mandatory; true risk reductions remain available under restrictive health states only when Safety classifies them as reducing.

## Próxima acción exacta
1. correr CI final sobre el branch canónico de cierre;
2. si verde, actualizar PR #11 y marcar ready;
3. merge únicamente por decisión explícita y contra el expected head SHA;
4. recertificar exact merge SHA en `main`;
5. crear R5 únicamente desde ese `main` verde.

## R5 después del merge
Read-only closed-kline stream -> duplicate/gap fail-closed -> DEGRADED socket semantics -> synchronized shadow -> forward evidence without HOLDOUT.

## Deuda no bloqueante
`TD-OPS-001` Graphify P3/OPS sigue OPEN; este runtime no puede fabricar un graph semántico/deep real.

## Capital
**LIVE TRADING: BLOQUEADO.**
R4 no añadió external PAPER/LIVE authority.
