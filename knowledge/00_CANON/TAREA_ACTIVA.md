# TAREA ACTIVA

## Objetivo
Recuperar, importar y recertificar el árbol fuente histórico **AUTO TRADING IA v0.28.0** antes de continuar desarrollando módulos nuevos o fusionar reconstrucciones inferiores.

## Secuencia activa
1. Mantener PR #4 en DRAFT como fallback; no fusionar.
2. Buscar el source/package v0.28 en fuentes disponibles y futuras cargas del usuario.
3. Al encontrarlo, ejecutar `knowledge/50_RUNBOOKS/RECOVER_LEGACY_V028.md`.
4. Registrar nombre exacto, tamaño y SHA256 del package antes de modificarlo.
5. Extraer en limpio y comprobar manifest/version/health.
6. Recertificar compile, tests, Event Ledger, contracts y startup barriers.
7. Comparar evidencia esperada: 302 tests PASS y 207 JSON Schemas; cualquier drift se documenta, nunca se oculta.
8. Importar el source en rama dedicada sin mezclar primero refactors del fallback.
9. Ejecutar Graphify deep y reconciliar su grafo contra `LEGACY_V028_RECOVERY.md`.
10. Actualizar Obsidian canon, ADRs y handoff.
11. Solo después comparar/portar mejoras útiles de Foundation v0.3 y PR #4.

## Tests negativos obligatorios en recovery
- PAPER startup permanece fail-closed salvo activación explícita del sandbox correspondiente.
- LIVE startup permanece fail-closed.
- No aparecen hosts LIVE Alpaca en production source.
- No aparecen withdrawal/transfer capabilities.
- Broker ambiguity no permite blind retry.
- HOLDOUT no puede ser usado por tuning/portfolio/shadow/PAPER.
- Automatic recovery no puede aumentar riesgo ni retirar una restricción defensiva sin acknowledgement humano.
- Broker-side protection no puede marcarse verificada solo porque exista un stop en Strategy DSL.
- Source/package alterado debe detectarse por checksum/evidencia.

## Definition of Done
- Source v0.28 localizado o, tras búsqueda suficientemente exhaustiva, declarado irrecuperable mediante ADR específico.
- Si localizado: checksum registrado, clean extraction PASS y árbol importado.
- Regression histórica pasa o cada diferencia queda explicada con evidencia.
- Graphify + Obsidian sincronizados con el source importado.
- `SOURCE_OF_TRUTH.md` actualizado para eliminar la ambigüedad actual.
- Ningún control histórico queda degradado silenciosamente.
- **LIVE TRADING permanece bloqueado.**
