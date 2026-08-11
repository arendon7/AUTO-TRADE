# RUNBOOK — LEGACY v0.28 SOURCE CONTINGENCY

## Estado
**Contingency only.** El camino principal es ADR-0006 + `RECONSTRUCTION_V028R_MATRIX.md`.

El source histórico v0.28 se considera irrecuperable para el plan actual. Este runbook se conserva únicamente por si en el futuro aparece una copia auténtica; nunca debe volver a bloquear v0.28R.

## Si aparece el ZIP/source en el futuro
1. No sustituir `main` automáticamente.
2. Copiarlo a un área aislada y calcular SHA256 antes de modificarlo.
3. Registrar filename, size, SHA256, fecha y origen.
4. Extraer en limpio.
5. Ejecutar compile, health, tests, Event Ledger verification y contract/schema inventory.
6. Comparar sus capacidades contra el `main` v0.28R vigente; no asumir que historical == better.
7. Importar únicamente en una rama forense/recovery separada.
8. Ejecutar Graphify deep y sellar `SOURCE_SHA`.
9. Portar solo mejoras/invariants que el árbol reconstruido no cubra ya igual o mejor.
10. Registrar cualquier hallazgo nuevo en `DEBT_REGISTER.md` o en la matriz antes de modificar el control plane.
11. Nunca habilitar PAPER/LIVE por el mero hecho de recuperar el package.

## Reference evidence
La certificación histórica v0.28 registraba 302 tests PASS, 207 schemas, SIMULATION, clean ZIP extraction + compile + health PASS y LIVE authority NONE. Son referencias de invariants, no quotas ni autoridad actual.

## Capital
**LIVE TRADING: BLOQUEADO.**
