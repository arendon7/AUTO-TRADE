# SOURCE OF TRUTH — AUTO-TRADE

Fecha: 2026-08-10
Estado: recuperación histórica activa

## Problema resuelto por este documento
Una conversación agotada ocultó que AUTO TRADING IA había avanzado mucho más que el repositorio GitHub recién creado. Para impedir nuevos retrocesos, toda sesión debe distinguir entre **implementación actualmente presente**, **evidencia histórica certificada** y **reconstrucciones de respaldo**.

## Precedencia canónica

1. **Árbol fuente histórico v0.28.0, cuando sea recuperado y recertificado**: máxima verdad de implementación histórica.
2. **Artefactos de certificación históricos v0.1–v0.28**: verdad de evidencia sobre capacidades que existieron y pasaron sus gates en aquel árbol.
3. **`main` actual (`721dd64...`, Foundation reconstruida v0.3)**: implementación ejecutable disponible hoy, pero es un fallback incompleto respecto del v0.28 histórico.
4. **PR #4 / `research/backtester-v0.4`**: reconstrucción útil y testeada, pero no debe fusionarse mientras la recuperación v0.28 esté pendiente.
5. Conversaciones/memoria informal: contexto auxiliar; nunca sustituye código, reportes o tests.

## Regla de no-regresión
No se debe reconstruir o fusionar un módulo inferior si existe evidencia verificable de que un módulo equivalente más maduro ya fue certificado históricamente, salvo que:
- el source histórico resulte irrecuperable;
- se documente explícitamente la divergencia;
- exista ADR de reconstrucción;
- los nuevos tests igualen o superen los invariantes históricos relevantes.

## Estado del source v0.28
- La certificación histórica declara extracción limpia del ZIP + compile + health PASS.
- El ZIP/source no está actualmente indexado en File Library, Google Drive, SharePoint/OneDrive ni en el repo GitHub accesible.
- Por tanto: **v0.28 está verificado históricamente pero todavía no importado al repositorio canónico actual**.

## Regla de capital
Ningún antecedente histórico, reporte, PASS, PAPER canary o broker-side protection equivale a autorización LIVE.

**LIVE TRADING: BLOQUEADO.**
