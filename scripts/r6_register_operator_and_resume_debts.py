from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DEBT_JSON = ROOT / "knowledge/00_CANON/debt_register.json"
DEBT_MD = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
TASK = ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md"

NEW_ITEMS = [
    {
        "area": "Durable explicit human final PAPER execution decision",
        "evidence": [],
        "id": "TD-R6-011",
        "next_action": (
            "Add a tamper-evident, short-lived, one-shot operator decision bound to the exact prepared PAPER canary: "
            "operator identity/action, PAPER account attestation, order/client_order_id, submission binding, bracket payload hash, "
            "canary approval, notional, expiry and attempt identity. The external writer must require and consume this decision "
            "before any POST. AI/research/application defaults cannot mint or substitute operator authority; missing, stale, "
            "mismatched, replayed or non-PAPER decisions must produce zero network I/O."
        ),
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R6",
    },
    {
        "area": "Crash-safe same-attempt resume after one-shot authorization consumption",
        "evidence": [],
        "id": "TD-R6-012",
        "next_action": (
            "Align writer restart semantics with the durable one-shot permit/decision ledgers. If a crash occurs after the same "
            "attempt consumes authorization but before UNKNOWN is persisted, PREPARED + same-attempt CONSUMED may resume only "
            "through the exact frozen identities and fresh guards. Any UNKNOWN state, different attempt, conflicting evidence, "
            "or uncertainty about whether POST could have happened remains reconciliation-only and must never blind retry."
        ),
        "resolution": "",
        "severity": "P1",
        "status": "OPEN",
        "track": "R6",
    },
]


def main() -> int:
    debt = json.loads(DEBT_JSON.read_text(encoding="utf-8"))
    items = debt.get("items")
    if not isinstance(items, list):
        raise SystemExit("debt register items missing")
    existing = {item.get("id") for item in items}
    for new in NEW_ITEMS:
        if new["id"] in existing:
            raise SystemExit(f"{new['id']} already exists")
    anchors = [i for i, item in enumerate(items) if item.get("id") == "TD-R6-010"]
    if len(anchors) != 1:
        raise SystemExit("TD-R6-010 anchor missing or duplicated")
    insert_at = anchors[0] + 1
    for offset, new in enumerate(NEW_ITEMS):
        items.insert(insert_at + offset, new)
    DEBT_JSON.write_text(json.dumps(debt, indent=2) + "\n", encoding="utf-8")

    md = DEBT_MD.read_text(encoding="utf-8")
    row010 = (
        "| `TD-R6-010` | P1 | OMS-owned external PAPER handoff | **CLOSED** — OMS owns durable VALIDATED→SUBMITTING; "
        "handoff event precedes state change; direct R6 OrderStore mutation prohibited |"
    )
    rows = (
        row010
        + "\n| `TD-R6-011` | P1 | human final PAPER execution decision | OPEN — durable one-shot operator authority must bind exact prepared canary and be consumed before POST |"
        + "\n| `TD-R6-012` | P1 | crash-safe same-attempt resume | OPEN — PREPARED + same-attempt consumed authorization may resume only when POST provably could not have occurred; UNKNOWN remains reconciliation-only |"
    )
    if md.count(row010) != 1:
        raise SystemExit("TD-R6-010 human anchor missing or duplicated")
    md = md.replace(row010, rows, 1)
    if md.count("R6 P0/P1/P2 OPEN: **6**.") != 1:
        raise SystemExit("R6 open count anchor missing or duplicated")
    md = md.replace("R6 P0/P1/P2 OPEN: **6**.", "R6 P0/P1/P2 OPEN: **8**.", 1)
    DEBT_MD.write_text(md, encoding="utf-8")

    task = TASK.read_text(encoding="utf-8")
    debt_anchor = "- `TD-R6-010` — **CLOSED** — OMS-owned external PAPER handoff certified; no direct OrderStore status mutation."
    debt_lines = (
        debt_anchor
        + "\n- `TD-R6-011` — durable explicit human final PAPER execution decision; no AI/research/application-default authority."
        + "\n- `TD-R6-012` — crash-safe same-attempt resume after one-shot authorization consumption."
    )
    if task.count(debt_anchor) != 1:
        raise SystemExit("TAREA TD-R6-010 anchor missing")
    task = task.replace(debt_anchor, debt_lines, 1)

    old_steps = (
        "8. bounded external PAPER evidence only after all prior gates are green; `TD-R6-010` is certified CLOSED;\n"
        "9. adversarial certification + debt closure."
    )
    new_steps = (
        "8. integrated manual single-shot canary coordinator that stops before network I/O;\n"
        "9. durable explicit human execution decision + crash-safe same-attempt resume (`TD-R6-011/012`);\n"
        "10. bounded external PAPER evidence only after all prior gates are green and an explicit final operator decision exists;\n"
        "11. adversarial certification + debt closure."
    )
    if task.count(old_steps) != 1:
        raise SystemExit("TAREA implementation-order anchor missing")
    task = task.replace(old_steps, new_steps, 1)

    negative_anchor = "- AI/research output is never an execution authorization; Safety + OMS remain mandatory deterministic authority."
    negative_lines = (
        "- missing/stale/mismatched/replayed operator decision, or decision not bound to exact bracket/submission/account/attempt => zero POST;\n"
        "- crash after same-attempt authorization consumption but before durable UNKNOWN => exact same-attempt resume only; different attempt or any UNKNOWN => reconciliation-only;\n"
        + negative_anchor
    )
    if task.count(negative_anchor) != 1:
        raise SystemExit("TAREA negative-test anchor missing")
    task = task.replace(negative_anchor, negative_lines, 1)

    old_restriction = (
        "- No external PAPER submit until gateway + ambiguity + canary + authority gates are implemented and green, and `TD-R6-010` proves the OMS-owned external handoff."
    )
    new_restriction = (
        "- No external PAPER submit until gateway + ambiguity + canary + authority gates are green, `TD-R6-010` proves the OMS-owned handoff, "
        "and `TD-R6-011/012` prove explicit operator authority plus crash-safe one-shot semantics."
    )
    if task.count(old_restriction) != 1:
        raise SystemExit("TAREA external-submit restriction anchor missing")
    task = task.replace(old_restriction, new_restriction, 1)
    TASK.write_text(task, encoding="utf-8")

    print("TD-R6-011 and TD-R6-012 registered before implementation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
