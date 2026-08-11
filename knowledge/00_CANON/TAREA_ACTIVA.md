# TAREA ACTIVA — AUTO-TRADE

## Objetivo inmediato
**Integrar R4 certificado en `main`, recertificar el SHA exacto resultante y solo entonces abrir R5.**

R4 ya tiene certificado de rama. La tarea activa NO es seguir añadiendo features R4 ni empezar R5 antes del merge.

## Secuencia obligatoria
1. validar el head final de branch con Core Safety + Knowledge Contract;
2. actualizar PR #11 con evidencia exacta y sacarlo de DRAFT solo si ambos gates están verdes;
3. merge por squash usando `expected_head_sha`;
4. verificar Core Safety + Knowledge Contract sobre el SHA exacto resultante en `main`;
5. solo si `main` queda verde, crear rama R5 desde ese SHA;
6. antes de programar R5, registrar explícitamente sus deudas/capacidades.

## R5 — alcance siguiente, todavía no iniciado
- closed-kline read-only stream, disabled by default y fixed host;
- duplicate idempotency + gap fail-closed; no silent imputation;
- unexpected socket termination -> DEGRADED; no reconnect que esconda gaps;
- synchronized portfolio shadow con pesos/timestamps congelados;
- forward evidence post-activation sin HOLDOUT;
- ninguna autoridad external PAPER/LIVE.

## Negative tests obligatorios para R5
- duplicated closed kline no duplica estado/evidencia;
- gap o out-of-order stream falla cerrado y no imputa;
- stale/malformed/future kline rechazada;
- unexpected socket termination deja estado DEGRADED;
- reconnect no puede ocultar un gap existente;
- shadow con weight/timestamp mismatch falla cerrado;
- forward evidence no toca FINAL_HOLDOUT;
- cualquier path de stream/shadow sigue sin importar OMS/broker execution authority.

## Restricciones
- `TD-OPS-001` Graphify P3 permanece visible; no fabricar `graphify-out`.
- No reducir coverage gate ni borrar negative tests para cerrar R5.
- No declarar rentabilidad por resultados de infraestructura/reproducibilidad.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER queda fuera de R5 y pertenece al track R6.
