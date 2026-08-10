# RUNBOOK — GRAPHIFY + OBSIDIAN

## Objetivo
Reducir tiempo de relectura, mejorar coherencia entre sesiones y hacer trazable la relación entre arquitectura, decisiones, evidencia y código.

## Modelo de memoria
- **Git/code/config/tests**: verdad técnica ejecutable del árbol actualmente importado.
- **Obsidian (`knowledge/`)**: verdad humana canónica sobre contexto, decisiones, estado y handoff.
- **Graphify (`graphify-out/`)**: mapa estructural/semántico regenerable del árbol que fue analizado.
- **Certificaciones históricas**: evidencia de capacidades de árboles anteriores; no sustituyen el source.

Durante Legacy Recovery leer primero `knowledge/00_CANON/SOURCE_OF_TRUTH.md`.

## Obsidian
Abrir `knowledge/` como vault.

### Estructura
- `00_CANON/`: source of truth, contexto, estado y tarea.
- `20_ARQUITECTURA/`: mapas e invariantes.
- `30_DECISIONES/`: ADRs.
- `40_HANDOFF/`: continuidad entre sesiones.
- `50_RUNBOOKS/`: procedimientos.
- `90_TEMPLATES/`: plantillas.

Obsidian no debe duplicar source code ni guardar secretos.

## Graphify — comportamiento actual verificado
Paquete PyPI: `graphifyy`.
CLI: `graphify`.

Graphify instala una skill en asistentes de coding compatibles. El **build inicial / semantic pass se ejecuta dentro del asistente**, no mediante `graphify .` en un shell normal.

Salida esperada:
```text
graphify-out/
  graph.json
  GRAPH_REPORT.md
  graph.html
  SOURCE_SHA        # sello añadido por AUTO-TRADE después del build
```

Los tres primeros archivos son salida Graphify; `SOURCE_SHA` es nuestro control de frescura.

## Instalación
```bash
bash scripts/setup_graphify.sh
```

Para fijar explícitamente un asistente soportado:
```bash
bash scripts/setup_graphify.sh codex
# o cursor / claude / gemini / etc. según soporte actual de Graphify
```

No usar `--platform agents --project`: no corresponde al CLI actual.

## Construcción inicial
Abrir el repo en un coding assistant soportado por Graphify.

### Codex
```text
$graphify . --mode deep
```

### Otros asistentes que usan slash-command
```text
/graphify . --mode deep
```

Después:
```bash
bash scripts/refresh_graphify.sh stamp
```

Comprobar frescura:
```bash
bash scripts/refresh_graphify.sh verify
```

## Actualización incremental
Dentro del asistente:

Codex:
```text
$graphify . --update
```

Otros asistentes:
```text
/graphify . --update
```

Después volver a sellar:
```bash
bash scripts/refresh_graphify.sh stamp
```

## Watch local AST
Graphify puede mantener actualizado el code graph estructural mediante:
```bash
bash scripts/refresh_graphify.sh watch
```

Esto **no sustituye** un semantic/deep pass del asistente cuando cambió arquitectura o intención.

## Consultas una vez exista `graph.json`
El CLI puede consultar el grafo ya generado:
```bash
graphify query "what can reach broker execution?"
graphify path "Strategy" "Broker"
graphify query "which components can increase portfolio risk?"
graphify query "what depends on CapitalSafetyKernel?"
graphify query "where are risk limits enforced?"
graphify query "what writes LedgerEvent?"
```

## Uso desde este ChatGPT
Esta conversación ChatGPT no tiene actualmente una skill/plugin Graphify ejecutable ni un filesystem del repo conectado al CLI. Por tanto no debe afirmar que ejecutó `/graphify` cuando no lo hizo.

Cuando `graphify-out/` esté comprometido en GitHub, ChatGPT sí puede usar esos artifacts como memoria estructural mediante el conector GitHub, además de Obsidian/Markdown.

## Política por sesión
1. Leer `SOURCE_OF_TRUTH` + CANON + tarea + handoff.
2. Si `graphify-out/` existe, verificar `SOURCE_SHA` contra el SHA relevante.
3. Si está fresco, usar el grafo para acotar archivos/relaciones.
4. Si está stale, tratarlo como orientación solamente y regenerarlo antes de decisiones de impacto amplio.
5. Implementar cambios pequeños y probar.
6. Actualizar Obsidian si cambió estado/decisión.
7. Regenerar Graphify cuando un asistente/runtime compatible esté disponible.
8. Sellar el grafo con `refresh_graphify.sh stamp`.

## Política de seguridad
- Graphify es memoria estructural, no autorización financiera.
- Un edge inferido/ambiguo nunca sustituye tests, reconciliation o Safety Kernel.
- El grafo del `main` fallback no puede interpretarse como grafo del v0.28 histórico faltante.
- Los límites financieros viven en code/config versionada.

## Recovery v0.28
Cuando aparezca el source v0.28:
1. importarlo y recertificarlo;
2. ejecutar Graphify **deep** sobre ese árbol;
3. sellar el SHA;
4. reconciliar `GRAPH_REPORT.md` contra `LEGACY_RELEASE_MATRIX.md` y certificaciones históricas;
5. actualizar `SOURCE_OF_TRUTH.md`.
