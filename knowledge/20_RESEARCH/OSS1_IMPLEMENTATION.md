# OSS-1 IMPLEMENTATION — SAFE STRATEGY DSL + FROZEN TOURNAMENT

Fecha: 2026-09-04
Rama: `research/oss-strategy-foundation`
Autoridad: RESEARCH ONLY

## Resultado de la ola

OSS-1 amplía la Safe Strategy DSL existente sin introducir plugins dinámicos, imports arbitrarios, broker, OMS, credenciales, red ni autoridad de capital.

La DSL soporta ahora seis `kind` deterministas:

1. `moving_average_cross` — baseline histórico/control.
2. `trend_ema_atr` — cruce EMA condicionado por ATR.
3. `time_series_momentum` — momentum temporal multi-horizonte.
4. `mean_reversion_zscore` — reversión a la media por z-score.
5. `donchian_breakout` — breakout contra canal previo con filtro ATR.
6. `volatility_regime` — filtro de régimen por relación de volatilidad y tendencia local.

Todas las estrategias producen únicamente `ResearchSignal` y conservan `initial_stop_pct` como metadato de research. Un stop declarado por Strategy DSL no se interpreta como protección broker-side.

## Integridad temporal

No se modificó el mecanismo central del `BacktestEngine`:

- la estrategia observa únicamente el historial hasta el cierre actual;
- la señal debe tener timestamp exactamente igual al cierre de la barra actual;
- la ejecución ocurre como mínimo una barra completa después;
- se mantienen fees, half-spread, slippage, leverage y participación de volumen explícitos.

Los nuevos tests verifican que la ampliación de OSS-1 sigue atravesando ese mismo camino future-bar-only.

## Validación fail-closed de parámetros

Cada `kind` tiene una allowlist exacta de parámetros.

La DSL bloquea, entre otros:

- campos desconocidos;
- parámetros faltantes;
- ventanas inválidas o invertidas;
- cantidades <= 0;
- umbrales NaN/infinito;
- `position_mode` distinto de `long_flat` o `long_short`;
- cualquier intento de introducir `callable`, import u otra superficie dinámica.

## Universo OSS-1 DEVELOPMENT

`oss_campaign.py` congela un primer universo de 18 candidatos, tres variantes por familia:

- 3 Moving Average Cross baseline;
- 3 Trend EMA/ATR;
- 3 Time-Series Momentum;
- 3 Mean Reversion Z-score;
- 3 Donchian Breakout;
- 3 Volatility Regime.

El diseño inicial usa `long_flat` para reducir complejidad de exposición y evitar que el primer torneo dependa de shorting.

Cada trial queda ligado a:

- dataset hash;
- code version;
- StrategySpec hash;
- estrategia/version exactas;
- parámetros exactos;
- DEVELOPMENT split exacto.

No puede construirse el plan OSS-1 con un split cuyo nombre contenga HOLDOUT.

## Ranking

El `TournamentSpec` de OSS-1 usa:

- métrica primaria: `sharpe`;
- dirección: `MAXIMIZE`;
- universo candidato: exactamente los 18 DEVELOPMENT trials congelados.

Esto NO significa que Sharpe por sí solo autorice promoción. El ganador del Tournament debe continuar atravesando los gates existentes de multiple-testing, HOLDOUT, execution-cost continuity, fee accounting, Shadow/Forward y admisión PAPER.

## Por qué no usar hyperopt masivo en esta etapa

La prioridad inicial es comprobar si existen familias con edge robusto, no encontrar un parámetro milagroso.

Un grid demasiado grande aumentaría:

- data snooping;
- multiple-testing burden;
- probabilidad de seleccionar ruido;
- fragilidad fuera de muestra;
- coste de validación.

Por ello OSS-1 empieza con un universo pequeño, explícito y preregistrable. Cualquier ampliación posterior deberá ocurrir como una campaña nueva, nunca editando retroactivamente un torneo ya observado.

## Fuentes externas y licencias

Las ideas tienen procedencia explícita en `strategy_catalog.py`.

- Apache/MIT pueden aportar componentes/patrones cuando corresponda.
- GPL/LGPL permanecen marcadas como `reference-only` cuando la integración directa no es conveniente.
- AUTO-TRADE mantiene contratos, hashes y ejecución propios.

## Tests incorporados

OSS-1 agrega cobertura específica para:

- creación de cada nueva StrategySpec;
- canonical hashing;
- validaciones adversariales;
- señales long/short/flat;
- uso del canal Donchian previo en lugar de contaminarlo con la barra actual;
- activación/inactividad del régimen de volatilidad;
- ejecución future-bar-only a través del BacktestEngine existente;
- invariantes long-flat;
- rechazo de inyección dinámica;
- construcción determinista de los 18 trials;
- identidad campaña/tournament;
- bloqueo explícito de HOLDOUT;
- ausencia de campos de autoridad de broker/capital.

## Autoridad

- Strategy output -> `ResearchSignal` únicamente.
- PAPER execution authority: FALSE.
- External execution authority: FALSE.
- Broker POST authority: NONE.
- OMS authority adquirida por OSS-1: NONE.
- Capital authority: NONE.
- LIVE: BLOCKED.

## Siguiente ola técnica

OSS-2 debe introducir infraestructura multi-activo en research, separada de ejecución:

1. universo congelado de instrumentos;
2. filtros de liquidez y cobertura de datos;
3. cross-sectional momentum/ranking;
4. portfolio allocator research-only;
5. límites de concentración/correlación como evidencia;
6. tournament multi-activo;
7. comparación contra los mejores candidatos single-symbol de OSS-1.
