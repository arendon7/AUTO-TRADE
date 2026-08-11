# TAREA ACTIVA — AUTO-TRADE

## Objetivo inmediato
**Integrar R5 certificado en `main`, recertificar el SHA exacto resultante y sólo entonces abrir R6.**

R5 ya tiene branch certification sobre `0d4f75d083a055b83646bb861f08731aecace560`. No añadir nuevas features R5 ni iniciar external PAPER desde la rama pre-merge.

## Secuencia obligatoria
1. exigir Core Safety + Knowledge Contract verdes en el head final de PR #13;
2. sacar PR #13 de DRAFT sólo con head exacto certificado;
3. merge por squash usando `expected_head_sha`;
4. verificar Core Safety + Knowledge Contract sobre el SHA exacto resultante en `main`;
5. sólo si `main` queda verde, crear rama R6 desde ese SHA;
6. registrar explícitamente deuda R6 antes de programar cualquier gateway PAPER.

## R6 — alcance siguiente, todavía no iniciado
- external Alpaca PAPER gateway, disabled by default;
- exact PAPER host allowlist; LIVE host forbidden;
- bounded external PAPER canary con prerequisites y notional cap más estricto;
- qualification evidence de terminality/fills/slippage/reconciliation;
- broker-side equity bracket protection con parent + exactamente 2 legs validadas;
- PAPER `trade_updates` protection evidence cuando la policy lo requiera;
- unsupported products fail closed; crypto bracket no soportado salvo certificación separada.

## Negative tests obligatorios para R6
- gateway disabled by default => cero red y cero order submission;
- LIVE host, arbitrary host, credentials/proxy no autorizado o path no permitido => reject antes de I/O;
- falta de preregistration, Instrument Master, Health/Safety approval, reconciliation o PAPER qualification => canary bloqueado;
- canary notional exactamente en frontera permitido y un quantum por encima rechazado; nunca auto-upsize a venue minimum;
- stale/missing/conflicting market/portfolio/broker state => fail closed, no nueva exposición;
- ambiguous submit/timeout => UNKNOWN + reconciliation; nunca retry ciego que duplique orden;
- partial fill/cancel/replace/restart preservan idempotencia y reservas;
- bracket equity debe tener parent + exactamente stop-loss y take-profit coherentes; leg faltante/extra/crossed/invalid => reject;
- asset/producto sin bracket certificado => fail closed; crypto bracket permanece unsupported;
- `trade_updates` faltante/stale/conflictivo cuando policy lo exige => protección no certificada;
- ninguna ruta R6 acepta host LIVE ni puede promover LIVE authority;
- AI/research output jamás es autorización de orden; Safety + OMS siguen siendo gates deterministas.

## Restricciones
- Coverage gate >=85% intacto.
- No relajar negative tests para cerrar R6.
- `TD-OPS-001` permanece visible; no fabricar Graphify.
- No declarar rentabilidad por PAPER qualification.

## Capital
**LIVE TRADING: BLOQUEADO.**
R6, si se certifica, será PAPER-only; cualquier futuro LIVE requerirá promoción separada y explícita fuera de v0.28R.
