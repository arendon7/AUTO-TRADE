# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **v0.28R R0–R4 certified; R4 merged, post-merge recertification repair active**.

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md`
5. `knowledge/00_CANON/debt_register.json`
6. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`
7. `knowledge/60_EVIDENCE/R4_CERTIFICATION.json`
8. `knowledge/60_EVIDENCE/R4_POST_MERGE_INTEGRATION_AUDIT.json`

## Certificación R4
Branch basis `350efd43ac133c95a1997b4a821a2e0bab4afaf2`: **479 tests PASS / 86.45% coverage**; 10 contracts; authority/debt/knowledge gates PASS. R4 blocking debt open: 0.

## Integración
PR #11 fue squash-merged a `main` como `aa6d80dc1682967edef367f726a620e41c0af118`.
Ese SHA NO está post-merge certificado: Knowledge Contract PASS, Core Safety FAIL por un assert documental `480` vs `479` y un one-shot R4 que quedó en el árbol.

## Regla operativa inmediata
Reparar esos dos artefactos mediante `hotfix/r4-post-merge-recertification`, fusionar sólo con CI verde y recertificar el SHA exacto de `main`. Solo entonces crear R5.

## Próximo track
R5 = read-only closed-kline streaming + gap/idempotency semantics + synchronized shadow + forward evidence without HOLDOUT.

## Authority
AI/research/Portfolio Manager no tienen autoridad de ejecución. Safety + OMS continúan siendo fronteras obligatorias. External PAPER/LIVE no está habilitado.

**LIVE TRADING: BLOQUEADO.**
