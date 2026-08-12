# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **v0.28R R0–R5 certified; R6 active / pre-first-canary**.

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md`
5. `knowledge/00_CANON/debt_register.json`
6. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`
7. `knowledge/60_EVIDENCE/R5_CERTIFICATION.json`

## Base R6
Exact post-R5-green `main`: `75dcbef65b061f742745ba7be0665521967e0587`.
Branch: `reconstruction/r6-external-paper-protection`.
PR #14: DRAFT, no merge.

Último checkpoint de código triple-certificado: `b0419c682a1af2907cbb559610fe021c93467859`.
- Core Safety `31556266622`: PASS — **1292 tests / 85.18180897396941%**.
- R6 Authority `31556266743`: PASS.
- Knowledge `31556266619`: PASS.

## R6 estructural
`TD-R6-007..013` están CLOSED.
Sólo `TD-R6-001..006` permanecen bloqueantes y exigen evidencia PAPER externa real.
`TD-OPS-001` Graphify permanece OPEN/nonblocking.

R6 ya incluye:
- exact PAPER account attestation;
- flat-account first-canary two-GET gate;
- IEX market-data GET evidence;
- bounded canary + US-equity bracket;
- durable idempotency/UNKNOWN/reconciliation;
- PAPER trade_updates + qualification;
- human one-shot decision;
- OMS handoff + writer final guards;
- same-core provenance + restart semantics;
- Mac Safe Start / Doctor / rehearsal / private workspace / readiness.

## Mac — lo que ya puede ensayarse
Sin credenciales ni red:
```bash
bash scripts/mac_start.sh
bash scripts/mac_start.sh rehearsal
bash scripts/mac_start.sh init-workspace "$HOME/AUTO-TRADE-R6/workspace-001"
bash scripts/mac_start.sh readiness "$HOME/AUTO-TRADE-R6/workspace-001"
```

Con credenciales sólo PAPER y decisión explícita:
- account GET;
- flat-account positions + open-orders GETs;
- IEX market-data GET.

Ninguno de esos pasos puede enviar una orden.

## Siguiente bloque
Crear **Mac candidate → Capital Safety rehearsal**:
- candidato explícito y acotado;
- `RiskDecision` producido sólo por `CapitalSafetyKernel.evaluate(...)`;
- offline/local;
- no writer / broker / operator authority;
- no claim de estrategia rentable.

No crear Strategy Health sintético. Una connectivity canary y una estrategia con edge son conceptos distintos.

## Authority
External PAPER order enviado: **0**.
Capital authority: **NONE**.
PAPER evidence no autoriza LIVE ni demuestra rentabilidad.

**LIVE TRADING: BLOQUEADO.**
