# RUNBOOK — RECOVER LEGACY v0.28 SOURCE

## Objetivo
Importar el source histórico certificado v0.28 sin perder evidencia, sin degradar seguridad y sin confundir el árbol actual reconstruido con el release histórico.

## 1. Recepción y custodia
Cuando aparezca el ZIP/source:
1. Copiarlo a un directorio de recuperación aislado.
2. Calcular SHA256 antes de modificarlo.
3. Registrar nombre exacto, tamaño, SHA256, fecha y origen.
4. No sobreescribir el ZIP original.

## 2. Extracción limpia
Extraer en un directorio vacío y verificar:
- estructura raíz;
- manifest/version/release docs;
- `engine.py`/health entrypoint si existe;
- tests;
- contracts/schemas;
- ausencia de archivos inesperados fuera del paquete.

## 3. Recertificación histórica antes de importar
Ejecutar sobre la extracción, sin credenciales reales:
- `python -m compileall`;
- health check;
- suite completa de tests, en batches si el runner lo requiere;
- Event Ledger verification;
- export/count de JSON Schemas;
- PAPER startup negative test;
- LIVE startup negative test;
- búsqueda de hosts LIVE Alpaca en production source;
- búsqueda de withdrawal/transfer routes;
- comprobación de que external PAPER/remote data/ChatGPT están disabled by default.

Objetivo de comparación histórica v0.28: 302 tests y 207 JSON Schemas. Si no coincide, registrar drift; **no falsificar el PASS**.

## 4. Importación Git
Crear rama `recovery/import-v0.28-source` desde el `main` vigente.

Preferencia:
- conservar el árbol histórico bajo su estructura original;
- no mezclar inicialmente refactors del fallback v0.3/v0.4;
- un commit de importación claramente identificado;
- mantener `knowledge/`, `AGENTS.md`, Graphify scripts y recovery evidence alrededor del árbol, adaptándolos solo si existen colisiones de paths.

## 5. Reconciliación con memoria Graphify + Obsidian
Tras importar:
1. ejecutar Graphify deep;
2. generar `graphify-out/graph.json`, `GRAPH_REPORT.md`, `graph.html`;
3. mapear módulos históricos contra reportes v0.1–v0.28;
4. actualizar `SOURCE_OF_TRUTH.md`;
5. crear ADR de cualquier divergencia encontrada;
6. actualizar handoff y tarea activa.

## 6. Comparación con reconstrucciones
Comparar v0.28 importado con:
- `main` Foundation v0.3 reconstruida;
- PR #4 Research v0.4 fallback.

Solo portar una mejora reconstruida si:
- no existe ya en v0.28;
- mejora un invariant o maintainability;
- tiene tests;
- no reduce cobertura ni capacidades históricas;
- queda documentada por ADR cuando cambie arquitectura.

## 7. Gate de promoción del source recuperado
No convertir el import en nuevo `main` hasta que:
- compile;
- health PASS;
- regression histórica pase o el drift quede explicado;
- safety barriers equivalentes sigan intactos;
- Graphify/Obsidian canon esté sincronizado;
- CI no habilite PAPER/LIVE por defecto.

## 8. Regla LIVE
Recuperar un release histórico, aunque haya tenido external PAPER canary y broker-side protection, **no es autorización LIVE**.

**LIVE TRADING: BLOQUEADO.**
