# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado: **R4 integrated; post-merge recertification hotfix active**

## Base integrada conocida
R3 está integrado y post-merge certificado en `main` `c585a84b5197076b210723bb70980b828e4e3026`.

## R4
Branch certification basis: `350efd43ac133c95a1997b4a821a2e0bab4afaf2`.
Branch evidence: `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`.
Branch result: **479 tests PASS / 86.45% coverage**, 10 contracts, Research/Advisory Authority PASS, Debt Register PASS, Knowledge Contract PASS.

PR #11 fue squash-merged en `main` como `aa6d80dc1682967edef367f726a620e41c0af118`.

## Post-merge audit
- Knowledge Contract `31461659067`: PASS.
- Core Safety `31461659063`: FAIL.
- Defecto 1: `tests/test_r4_certification_contract.py` exigía `480`, aunque el run certificado real fue `479 / 86.45%`.
- Defecto 2: `.github/workflows/r4-final-readiness-one-shot.yml` quedó accidentalmente incluido en el squash merge.

La reparación canónica está en `hotfix/r4-post-merge-recertification`. R5 permanece bloqueado hasta que ese hotfix se fusione y el SHA exacto de `main` quede verde.

## Invariantes R4 que no se deben perder
- Instrument Master autoritativo separado de research metadata.
- exact Decimal normalization; no tolerance weakening.
- Health recovery explicit + retry-safe + ACK-chain tamper-evident.
- unsynced authoritative worsening tightens immediately.
- Defensive Health Bridge automatic actions reduce/block only.
- Portfolio Manager advisory-only; no OrderIntent/OMS/broker authority.
- Safety + OMS remain mandatory; true risk reductions remain available under restrictive health states only when Safety classifies them as reducing.

## Próxima acción exacta
1. certificar la rama hotfix con Core Safety + Knowledge Contract;
2. fusionar sólo contra el expected head SHA verde;
3. recertificar exact merge SHA en `main`;
4. crear R5 únicamente desde ese `main` verde;
5. registrar deuda R5 antes de implementar.

## Deuda no bloqueante
`TD-OPS-001` Graphify P3/OPS sigue OPEN; no fabricar un graph semántico/deep sin runtime soportado y `SOURCE_SHA` verificable.

## Capital
**LIVE TRADING: BLOQUEADO.**
R4 no añadió external PAPER/LIVE authority.
