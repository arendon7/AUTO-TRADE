# TAREA ACTIVA

## Objetivo
Completar **R1 — Market Data + Strategy DSL + Research Engine** como primer track de la reconstrucción v0.28R, reutilizando PR #4 solo donde cumpla la matriz de equivalencia.

## Secuencia activa
1. Auditar PR #4 contra `RECONSTRUCTION_V028R_MATRIX.md`.
2. Sincronizar PR #4 con el canon actual para evitar reintroducir docs obsoletas.
3. Conservar su market-data contract, backtester, costs, splits, experiment registry y robustness gates cuando pasen auditoría.
4. Completar Strategy DSL/config seguro: parser restringido, schema/validación, hash canónico, sin broker/network/risk authority.
5. Certificar `BAR_CLOSE -> NEXT_BAR` y ausencia de look-ahead con tests adversariales.
6. Completar modelo de costos: fees, spread, slippage y latency/delay explícitos; cero costo solo por opt-in deliberado.
7. Completar TRAIN/VALIDATION/protected HOLDOUT con enforcement que impida tuning normal sobre HOLDOUT.
8. Completar sample adequacy y validation gates explícitos.
9. Implementar moving-block bootstrap reproducible.
10. Completar walk-forward chronological rolling/expanding y robustness policy.
11. Asegurar Experiment/Validation Registry durable e inmutable por fingerprint/result hash.
12. Añadir contratos machine-readable iniciales para R1 cuando aporte valor; el registry completo de contratos se cierra en R2.
13. Ejecutar threat/failure-path review específico de research leakage y reproducibilidad.
14. Ejecutar CI sin bajar cobertura.
15. Actualizar matriz v0.28R: solo filas certificadas pasan a PASS.
16. Fusionar R1 y certificar nuevamente el SHA resultante en `main`.

## Negative tests obligatorios R1
- timestamps naive/desordenados/duplicados se rechazan.
- OHLCV imposible se rechaza.
- gaps no se imputan silenciosamente.
- señales no pueden ejecutarse sobre la misma barra que las originó.
- strategy config no puede importar/ejecutar código, acceder a red, broker u OMS.
- costo cero accidental se rechaza.
- volumen/capacidad imposible no produce fill optimista.
- split temporal con solapamiento se rechaza.
- HOLDOUT no entra al loop normal de tuning.
- permit/acto final de HOLDOUT no puede reutilizarse.
- misma especificación experimental con resultado distinto genera conflicto.
- bootstrap/walk-forward son reproducibles con seed/config registrados.
- parámetros inválidos/NaN/inf no contaminan métricas o registry.

## Definition of Done R1
- Todas las filas R1 de la matriz están `PASS`.
- No hay P0/P1 conocidos.
- Toda deuda P2+ está explícita y justificada.
- Tests positivos/negativos PASS.
- Coverage gate >= 85% y no se reduce.
- Knowledge Contract PASS.
- Canon, ADR/handoff y matriz sincronizados.
- CI verde también sobre el merge SHA en `main`.
- **LIVE TRADING permanece bloqueado.**
