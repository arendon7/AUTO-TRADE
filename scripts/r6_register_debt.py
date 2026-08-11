from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT_JSON = ROOT / "knowledge/00_CANON/debt_register.json"
DEBT_MD = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
STATE = ROOT / "knowledge/00_CANON/ESTADO_ACTUAL.md"
TASK = ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md"
CONTEXT = ROOT / "knowledge/00_CANON/CONTEXTO_RAPIDO.md"
HANDOFF = ROOT / "knowledge/40_HANDOFF/HANDOFF_ACTUAL.md"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"

GREEN_MAIN = "75dcbef65b061f742745ba7be0665521967e0587"
CORE_RUN = 31466198629
KNOWLEDGE_RUN = 31466198624

R6_DEBTS = [
    {
        "area": "External Alpaca PAPER gateway and environment attestation",
        "evidence": [],
        "id": "TD-R6-001",
        "next_action": "Implement a disabled-by-default Alpaca Trading API gateway restricted to exact https://paper-api.alpaca.markets, paper credentials and explicit paper-account attestation before any write. Reject LIVE/arbitrary hosts, redirects, unsafe proxying, malformed account state, blocked trading, secret leakage and any submit before deterministic Safety/OMS approval.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R6"
    },
    {
        "area": "External PAPER submit ambiguity, client-order idempotency and reconciliation",
        "evidence": [],
        "id": "TD-R6-002",
        "next_action": "Bind every external PAPER submit to a deterministic durable client_order_id/idempotency record. On timeout, transport ambiguity or non-terminal acknowledgement enter UNKNOWN and reconcile by client/order ID; never blind-retry a potentially accepted order. Prove restart-safe exact-once local projection and conflict rejection.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R6"
    },
    {
        "area": "Bounded external PAPER canary prerequisites and capital cap",
        "evidence": [],
        "id": "TD-R6-003",
        "next_action": "Implement an explicit opt-in PAPER canary gate requiring certified R0-R5 state, authoritative Instrument Master, synchronized portfolio/broker state, healthy Safety/Health controls, no unresolved UNKNOWN/reconciliation debt and a canary notional cap strictly tighter than normal portfolio limits. Never auto-upsize to venue minimums.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R6"
    },
    {
        "area": "PAPER evidence qualification: terminality, fills, slippage and reconciliation",
        "evidence": [],
        "id": "TD-R6-004",
        "next_action": "Persist tamper-evident PAPER qualification evidence for submit/ack/terminal state, fills, prices, timestamps, slippage and broker/local reconciliation. Missing, stale, partial or contradictory evidence must be unqualified and cannot promote authority or profitability claims.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R6"
    },
    {
        "area": "Broker-side equity bracket protection",
        "evidence": [],
        "id": "TD-R6-005",
        "next_action": "Implement equity-only bracket order construction/validation with one parent plus exactly one take-profit and one stop-loss leg, coherent side/quantity/prices, day/gtc only, extended_hours false, authoritative venue increments and nested broker-response verification. Missing/extra/crossed/mismatched legs fail closed.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R6"
    },
    {
        "area": "PAPER trade_updates protection evidence",
        "evidence": [],
        "id": "TD-R6-006",
        "next_action": "Implement bounded PAPER-only trade_updates ingestion and durable correlation to submitted client/order IDs. Authentication/listen protocol, binary-frame handling, event ordering, terminality and disconnect ambiguity must fail closed when protection policy requires streaming evidence.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R6"
    },
    {
        "area": "Unsupported external products and protection modes fail closed",
        "evidence": [],
        "id": "TD-R6-007",
        "next_action": "Reject unsupported order classes/products before I/O. In particular, broker-side bracket/OTO/OCO protection is equity-only for this track; crypto remains simple-order only and cannot be treated as bracket-protected without separate certification. Unknown asset classes, unsupported TIF/order types or missing Instrument Master rules fail closed.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R6"
    },
    {
        "area": "Permanent R6 PAPER-only authority boundary",
        "evidence": [],
        "id": "TD-R6-008",
        "next_action": "Add permanent CI/static authority checks proving R6 external execution code contains no LIVE Trading API host, cannot bypass Safety/OMS, cannot accept AI/research output as authorization, cannot silently broaden host/method scope and cannot promote PAPER evidence into LIVE authority. Keep LIVE fail-closed globally.",
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R6"
    }
]


def register_debt() -> None:
    data = json.loads(DEBT_JSON.read_text())
    expected = ["R0", "R1", "R2", "R3", "R4", "R5"]
    if data.get("certified_tracks") != expected:
        raise SystemExit(f"unexpected certified tracks: {data.get('certified_tracks')}")
    existing = {item["id"] for item in data["items"]}
    collisions = sorted(existing & {item["id"] for item in R6_DEBTS})
    if collisions:
        raise SystemExit(f"R6 debt IDs already exist: {collisions}")
    data["items"].extend(R6_DEBTS)
    DEBT_JSON.write_text(json.dumps(data, indent=2) + "\n")


def update_matrix() -> None:
    text = MATRIX.read_text()
    old = "R6 is gated until PR #13 is merged and the exact resulting `main` SHA is recertified green. R6 will cover external Alpaca PAPER only, bounded canary qualification and broker-side protection evidence; LIVE remains blocked."
    new = (
        f"R6 starts only from post-R5-green `main` `{GREEN_MAIN}`. "
        "Blocking debt registered before implementation: `TD-R6-001..008`. "
        "R6 is PAPER-only; LIVE remains blocked and outside v0.28R authority."
    )
    if old not in text:
        raise SystemExit("R6 matrix transition marker missing")
    MATRIX.write_text(text.replace(old, new, 1))


def write_debt_md() -> None:
    DEBT_MD.write_text(f"""# DEBT REGISTER — v0.28R

Fecha: 2026-08-11
Estado: **R0–R5 CERTIFIED; R6 ACTIVE**

## Authority
La autoridad machine-readable es `knowledge/00_CANON/debt_register.json`. Si esta vista humana discrepa, manda el JSON + CI.

## Regla
Ningún track puede certificarse con deuda conocida P0/P1/P2 asignada a ese track. Deuda nueva se registra antes de implementar y no se rebaja para satisfacer una release.

## Certified tracks
- **R0 — PASS**
- **R1 — PASS**
- **R2 — PASS**
- **R3 — PASS**
- **R4 — PASS**
- **R5 — PASS e integrado/post-merge recertificado en `main` `{GREEN_MAIN}`**

Post-merge R5: Core Safety `{CORE_RUN}` PASS, Knowledge Contract `{KNOWLEDGE_RUN}` PASS, **606 tests / 86.49% coverage**.

## R6 — deuda registrada antes de implementación
| ID | Sev | Área | Condición de cierre |
|---|---|---|---|
| `TD-R6-001` | P1 | External Alpaca PAPER gateway | disabled default + exact PAPER host/credentials/account attestation + Safety/OMS mandatory; LIVE/arbitrary host reject before I/O |
| `TD-R6-002` | P1 | submit ambiguity/idempotency/reconciliation | durable client_order_id binding; timeout/ambiguity => UNKNOWN + reconcile; never blind retry |
| `TD-R6-003` | P1 | bounded PAPER canary | explicit opt-in + certified prerequisites + synchronized state + tighter notional cap; never auto-upsize |
| `TD-R6-004` | P1 | PAPER qualification evidence | terminality/fills/slippage/reconciliation evidence durable and tamper-evident; incomplete evidence unqualified |
| `TD-R6-005` | P1 | equity bracket protection | parent + exactly TP/SL legs, coherent prices/qty/TIF, authoritative increments and nested response verification |
| `TD-R6-006` | P1 | PAPER trade_updates evidence | PAPER-only authenticated bounded stream + order correlation + ordering/terminality/disconnect fail-closed |
| `TD-R6-007` | P1 | unsupported products | unknown/unsupported products/classes/TIF fail closed; crypto bracket protection explicitly unsupported |
| `TD-R6-008` | P1 | permanent PAPER-only authority boundary | CI prevents LIVE host, Safety/OMS bypass, AI authorization and PAPER→LIVE authority creep |

R6 P0/P1/P2 OPEN: **8**. R6 cannot certify until all close with evidence.

## Deuda no bloqueante fuera de R6
| ID | Sev | Track | Área |
|---|---|---|---|
| `TD-OPS-001` | P3 | OPS | Graphify semantic/deep real pending supported runtime |

## Capital
**LIVE TRADING: BLOQUEADO.**
R6 may qualify bounded external PAPER only; no R6 artifact may authorize LIVE.
""")


def write_state() -> None:
    STATE.write_text(f"""# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado canónico: **v0.28R reconstruction — R0–R5 CERTIFIED; R6 ACTIVE**

## Base certificada
R5 quedó integrado y post-merge recertificado en exact `main` `{GREEN_MAIN}`.
- Core Safety `{CORE_RUN}`: PASS — **606 tests / 86.49% coverage**.
- Knowledge Contract `{KNOWLEDGE_RUN}`: PASS.
- Contract Registry: 10 PASS.
- Research/Advisory Authority Boundary: PASS.
- R5 Stream/Shadow/Forward Authority Boundary: PASS.
- Debt Register Contract: PASS.

## R6 activo
Branch: `reconstruction/r6-external-paper-protection`.
Base exacta: `{GREEN_MAIN}`.
Antes de implementar se registraron `TD-R6-001..008`, todas P1 OPEN.

Alcance:
- exact Alpaca PAPER gateway + paper environment attestation;
- durable client_order_id/idempotency + UNKNOWN/reconciliation semantics;
- tightly bounded external PAPER canary;
- PAPER terminality/fill/slippage/reconciliation qualification evidence;
- broker-side equity bracket protection;
- PAPER trade_updates protection evidence;
- unsupported products fail closed;
- permanent PAPER-only/LIVE-deny authority boundary.

## Deuda
- R6 P1 OPEN: **8** (`TD-R6-001..008`).
- `TD-OPS-001` Graphify P3/OPS: OPEN, non-blocking.

## Capital
**LIVE TRADING: BLOQUEADO.**
R6 is PAPER-only. Paper simulation results are not profitability proof and cannot promote LIVE authority.
""")


def write_task() -> None:
    TASK.write_text(f"""# TAREA ACTIVA — AUTO-TRADE

## Objetivo inmediato
**R6 — external Alpaca PAPER gateway + bounded canary + protection/qualification evidence.**

Base obligatoria: post-R5-green `main` `{GREEN_MAIN}`.
Branch activa: `reconstruction/r6-external-paper-protection`.

## Deuda registrada antes de programar
- `TD-R6-001` — exact PAPER gateway/environment attestation.
- `TD-R6-002` — submit ambiguity + durable client_order_id idempotency/reconciliation.
- `TD-R6-003` — bounded PAPER canary prerequisites/cap.
- `TD-R6-004` — PAPER terminality/fills/slippage/reconciliation qualification evidence.
- `TD-R6-005` — broker-side equity bracket protection.
- `TD-R6-006` — PAPER trade_updates protection evidence.
- `TD-R6-007` — unsupported products/protection modes fail closed.
- `TD-R6-008` — permanent PAPER-only/LIVE-deny authority boundary.

## Orden de implementación
1. PAPER gateway policy + account/environment attestation, **without submit path enabled**;
2. durable submit intent/client_order_id state machine + ambiguous transport reconciliation;
3. deterministic canary preflight and tighter notional cap;
4. equity bracket request/response protection validation;
5. PAPER trade_updates ingestion/correlation;
6. qualification evidence store and reconciliation proofs;
7. bounded external PAPER evidence only after all prior gates are green;
8. adversarial certification + debt closure.

## Negative tests obligatorios para R6
- gateway disabled by default => zero network and zero order submission;
- exact LIVE host, arbitrary host, redirect, credentials in URL, unsafe proxy or path/method not allowlisted => reject before I/O;
- missing/wrong paper credentials or account/environment attestation => no write;
- `trading_blocked`, stale/unknown account or malformed account state => no write;
- no deterministic Safety/OMS approval => gateway cannot submit;
- same local order + same client_order_id => idempotent; same client_order_id + changed payload => conflict;
- timeout/connection reset/ambiguous submit => UNKNOWN + GET by client_order_id/reconciliation; no blind POST retry;
- restart during UNKNOWN preserves ambiguity and blocks new exposure until resolved;
- canary prerequisites missing/stale/DEGRADED/UNKNOWN => blocked;
- canary notional at exact boundary allowed only when all other gates pass; one quantum above rejected; no auto-upsize to venue minimum;
- missing/partial/contradictory terminal/fill/slippage/reconciliation evidence => PAPER qualification FAIL;
- bracket equity parent must have exactly one take-profit and one stop-loss; missing/extra/crossed/side/qty/price/TIF/extended-hours mismatch => reject;
- broker nested response must prove exactly two coherent protection legs before bracket is considered protected;
- crypto/unknown product bracket, OCO or OTO => fail closed; R6 crypto remains simple-order only;
- PAPER `trade_updates` wrong host/auth, malformed/binary decode failure, event gap/order mismatch/disconnect ambiguity => protection evidence FAIL;
- no R6 source may contain/use LIVE trading host or convert PAPER qualification into LIVE authority;
- AI/research output is never an execution authorization; Safety + OMS remain mandatory deterministic authority.

## Restricciones
- Coverage gate >=85% intacto.
- No reduce/relax negative tests to close R6.
- No external PAPER submit until gateway + ambiguity + canary + authority gates are implemented and green.
- Any real PAPER test must be explicitly enabled, bounded and evidenced; no unbounded loops or broad market activity.
- `TD-OPS-001` remains visible; never fabricate Graphify.
- No profitability claim from paper simulation.

## Capital
**LIVE TRADING: BLOQUEADO.**
R6 may authorize bounded PAPER only after separate certification; LIVE remains outside v0.28R.
""")


def write_context() -> None:
    CONTEXT.write_text(f"""# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **v0.28R R0–R5 certified; R6 active**.

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md`
5. `knowledge/00_CANON/debt_register.json`
6. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`
7. `knowledge/60_EVIDENCE/R5_CERTIFICATION.json`

## Base R6
Exact post-R5-green `main`: `{GREEN_MAIN}`.
Core Safety `{CORE_RUN}` PASS; Knowledge Contract `{KNOWLEDGE_RUN}` PASS; **606 tests / 86.49%**.

## R6
Branch `reconstruction/r6-external-paper-protection`.
`TD-R6-001..008` registered P1 OPEN before implementation.

R6 = exact PAPER gateway -> ambiguity/idempotency/reconciliation -> bounded canary -> bracket/trade_updates protection -> qualification evidence -> adversarial certification.

## Authority
External PAPER is not yet enabled. Safety + OMS remain mandatory. AI/research/PAPER evidence cannot authorize LIVE.

**LIVE TRADING: BLOQUEADO.**
""")


def write_handoff() -> None:
    HANDOFF.write_text(f"""# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado: **R5 post-merge certified; R6 active**

## Base certificada
`main` `{GREEN_MAIN}` is exact post-R5 green base.
- Core Safety `{CORE_RUN}` PASS — 606 tests / 86.49%.
- Knowledge Contract `{KNOWLEDGE_RUN}` PASS.
- Contract Registry / Research Authority / R5 Authority / Debt Register PASS.

## R6
Branch: `reconstruction/r6-external-paper-protection`.
Blocking debt registered before implementation: `TD-R6-001..008`, all P1 OPEN.

Order:
1. exact PAPER-only gateway/environment attestation without submit enabled;
2. durable client_order_id + ambiguity/reconciliation state machine;
3. bounded canary preflight/cap;
4. equity bracket protection validation;
5. PAPER trade_updates evidence;
6. terminality/fill/slippage/reconciliation qualification;
7. bounded external PAPER evidence only after prior gates pass;
8. adversarial certification.

## R6 external facts locked from official Alpaca docs
- PAPER Trading API uses `paper-api.alpaca.markets` and separate paper credentials from LIVE.
- client_order_id can identify/retrieve an order and is required for safe retry reconciliation.
- bracket class is supported for equities; crypto order class is simple only.
- PAPER `trade_updates` is available on the PAPER trading WebSocket and uses binary frames.

## Inherited invariants
- Safety + OMS mandatory and deterministic.
- Kill switch/reconciliation/UNKNOWN semantics remain fail-closed.
- No stale/missing/ambiguous evidence increases exposure.
- PAPER simulation is not profitability proof.
- LIVE host/authority remains prohibited.

## Deuda no bloqueante
`TD-OPS-001` Graphify P3/OPS remains OPEN.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER is still disabled until R6 gates and certification explicitly permit a bounded canary.
""")


def main() -> None:
    register_debt()
    update_matrix()
    write_debt_md()
    write_state()
    write_task()
    write_context()
    write_handoff()


if __name__ == "__main__":
    main()
