# RUNBOOK — GRAPHIFY + OBSIDIAN

## Objetivo
Reducir tiempo de relectura, mejorar coherencia entre sesiones y hacer trazable la relación entre arquitectura, decisiones y código.

## Obsidian
Abrir `knowledge/` como vault. No es una segunda fuente de verdad técnica: conserva contexto, ADRs, estado, tareas y handoffs.

### Estructura
- `00_CANON/`: contexto mínimo vigente.
- `20_ARQUITECTURA/`: mapas e invariantes.
- `30_DECISIONES/`: ADRs.
- `40_HANDOFF/`: continuidad entre sesiones.
- `50_RUNBOOKS/`: procedimientos.
- `90_TEMPLATES/`: plantillas.

## Graphify
Paquete oficial: `graphifyy`; comando: `graphify`.

### Instalación recomendada
```bash
uv tool install graphifyy
graphify install --platform agents --project
```

Esto instala la skill en el proyecto para frameworks compatibles con Agent Skills.

### Primer mapa
```bash
graphify .
```

Salida esperada:
```text
graphify-out/
  graph.json
  GRAPH_REPORT.md
  graph.html
```

### Actualización incremental
```bash
graphify . --update
```

### Análisis profundo cuando cambie arquitectura
```bash
graphify . --mode deep
```

### Consultas útiles AUTO-TRADE
```bash
graphify query "what can reach broker execution?"
graphify path "Strategy" "Broker"
graphify query "which components can increase portfolio risk?"
graphify query "what depends on CapitalSafetyKernel?"
graphify query "where are risk limits enforced?"
graphify query "what writes LedgerEvent?"
```

## Política de uso por sesión
1. Leer CANON + tarea.
2. Consultar Graphify para identificar componentes/relaciones afectadas.
3. Abrir solo archivos necesarios.
4. Implementar y probar.
5. Actualizar Obsidian si cambia estado/decisión.
6. Ejecutar `graphify . --update`.

## Política de seguridad
Graphify es una memoria estructural, no un mecanismo de autorización. Un edge inferido nunca puede usarse como sustituto de tests o validación de seguridad. Los límites financieros viven en código/config versionada.

## Cuando el grafo esté desactualizado
Si el SHA/estado del grafo no coincide con el código, tratar Graphify como stale y regenerar antes de usarlo para análisis de impacto.
