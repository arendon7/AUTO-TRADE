# HANDOFF ACTUAL

Fecha: 2026-08-10
Branch: `recovery/legacy-v0.28-canon`
Base main: `721dd64a7a87a276c7f85ca34467b8a62f09d563`

## Cambio de dirección crítico
La reconstrucción inicial asumía que el proyecto histórico había quedado cerca de Foundation. Esa suposición fue corregida al recuperar certificaciones reales que demuestran releases hasta **AUTO TRADING IA v0.28.0**.

Por eso:
- Foundation v0.3 actual permanece como fallback ejecutable;
- PR #4 Research v0.4 fue convertido a DRAFT/FALLBACK;
- no se seguirá reconstruyendo hacia arriba sin intentar primero recuperar el source v0.28.

## Evidencia histórica recuperada
v0.28 certificó:
- 302/302 PASS;
- 207 JSON Schemas;
- SIMULATION;
- Event Ledger válido;
- clean ZIP extraction + compile + health PASS;
- external PAPER Canary/Evidence;
- broker-side equity bracket protection sandbox + nested legs + PAPER trade_updates;
- LIVE authority NONE.

Los reportes previos prueban además research completo, HOLDOUT protegido, Trial Ledger/PBO/DSR, capital safety/OMS/reconciliation, real data intake, portfolio robustness, health/drift, defensive bridge y forward/shadow evidence.

## Búsqueda del source realizada
Sin source package encontrado en:
- File Library (sí existen reportes);
- Google Drive;
- SharePoint/OneDrive;
- GitHub actual.

No se inventará el árbol v0.28 desde reportes.

## Memoria/proceso corregidos
Se añadieron:
- `knowledge/00_CANON/SOURCE_OF_TRUTH.md`;
- `knowledge/00_CANON/LEGACY_V028_RECOVERY.md`;
- `knowledge/50_RUNBOOKS/RECOVER_LEGACY_V028.md`;
- ADR-0005;
- `AGENTS.md` actualizado con jerarquía de verdad y regla de no-regresión.

## Próximo paso exacto
Recuperar/importar source v0.28 cuando aparezca y recertificarlo antes de seguir con nueva funcionalidad. Si definitivamente no aparece, crear ADR para reconstrucción equivalente desde la arquitectura histórica, preservando todos los invariants verificables.

## Startup para la próxima sesión
`AGENTS.md -> SOURCE_OF_TRUTH -> CONTEXTO_RAPIDO -> ESTADO_ACTUAL -> TAREA_ACTIVA -> LEGACY_V028_RECOVERY -> HANDOFF_ACTUAL -> Graphify si existe -> trabajo`.

## Capital
**LIVE TRADING: BLOQUEADO.**
