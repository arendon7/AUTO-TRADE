# SOURCE OF TRUTH — AUTO-TRADE

Fecha: 2026-08-10
Estado: reconstrucción equivalente v0.28R activa

## Decisión vigente
El source histórico AUTO TRADING IA v0.28.0 se considera **irrecuperable para efectos del plan de trabajo**. La evidencia histórica v0.1–v0.28 se conserva como especificación de comportamiento e invariantes, pero ya no bloquea el desarrollo esperando el ZIP perdido.

La meta es construir **v0.28R**: una implementación nueva que iguale o supere las capacidades verificadas históricamente, sin deuda técnica oculta.

## Precedencia canónica
1. **Código/config/tests/contratos en `main` actual**: verdad ejecutable vigente.
2. **`RECONSTRUCTION_V028R_MATRIX.md` + ADR-0006**: especificación de equivalencia que falta reconstruir.
3. **Artefactos históricos v0.1–v0.28 / `LEGACY_RELEASE_MATRIX.md`**: evidencia de invariantes que la reconstrucción debe cubrir; no son source ejecutable.
4. **Graphify del SHA correspondiente**, cuando exista y `SOURCE_SHA` coincida: mapa estructural auxiliar.
5. **Obsidian `knowledge/`**: canon humano de estado, decisiones y handoffs.
6. Conversaciones/memoria informal: contexto auxiliar, nunca sustituto de código/evidencia.

## Regla de equivalencia
Una capacidad histórica solo puede marcarse reconstruida cuando exista evidencia actual igual o más fuerte:
- implementación;
- positive + negative tests;
- contracts/schemas cuando aplique;
- CI verde;
- failure-path review;
- documentación/handoff sincronizados.

El conteo histórico de tests/schemas es referencia, no una cuota artificial. No se añaden pruebas vacías para alcanzar números.

## Regla de deuda
No se permite cerrar un track con:
- P0/P1 conocidos;
- TODO críticos ocultos;
- bypasses temporales;
- tests negativos omitidos;
- reducción del gate de cobertura;
- diferencias históricas conocidas sin registrar.

Toda deuda P2+ pendiente debe estar explícita y justificada antes del merge.

## Estado de reconstrucciones previas
- Foundation v0.3 reconstruida forma el track R0 y permanece como base ejecutable.
- PR #4 contiene una base Research v0.4 útil; deja de ser un fallback congelado y pasa a ser **candidato R1**, sujeto a auditoría y completitud antes de merge.

## Regla de capital
Reconstruir hasta v0.28R **no** constituye promoción a dinero real. External PAPER futuro tampoco equivale a autorización LIVE.

**LIVE TRADING: BLOQUEADO.**
