# TAREA ACTIVA — AUTO-TRADE

## Objetivo inmediato
**Reparar la integración post-merge de R4, recertificar el SHA exacto de `main` y solo entonces abrir R5.**

PR #11 fue fusionado por squash en `main` como `aa6d80dc1682967edef367f726a620e41c0af118`. Knowledge Contract quedó verde, pero Core Safety falló por dos artefactos de cierre: un test permanente exigía incorrectamente `480` en vez de `479`, y quedó el workflow temporal `r4-final-readiness-one-shot.yml` dentro del árbol fusionado.

Esto NO invalida las capacidades funcionales R4 ya certificadas, pero sí invalida la recertificación post-merge hasta reparar ambos defectos.

## Secuencia obligatoria
1. corregir el contrato permanente R4 a la evidencia real `479 tests / 86.45%`;
2. eliminar el workflow temporal R4 del árbol;
3. mantener explícita la evidencia del fallo de integración;
4. ejecutar Core Safety + Knowledge Contract en la rama hotfix;
5. fusionar el hotfix únicamente contra su head verde esperado;
6. verificar ambos gates sobre el SHA exacto resultante en `main`;
7. solo si `main` queda verde, crear rama R5 desde ese SHA;
8. antes de programar R5, registrar explícitamente sus deudas/capacidades.

## R5 — alcance siguiente, todavía bloqueado
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
- No crear rama R5 desde `aa6d80d...` mientras Core Safety permanezca rojo.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER queda fuera de R5 y pertenece al track R6.
