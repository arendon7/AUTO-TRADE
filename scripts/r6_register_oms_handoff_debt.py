from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DEBT_JSON = ROOT / "knowledge/00_CANON/debt_register.json"
DEBT_MD = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
TASK = ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md"

NEW_ITEM = {
    "area": "OMS-owned external PAPER handoff and durable SUBMITTING transition",
    "evidence": [],
    "id": "TD-R6-010",
    "next_action": (
        "Implement a deterministic OMS-owned staging boundary for the first external PAPER canary. "
        "It must revalidate the existing durable order/control-plane identity and transition exactly "
        "VALIDATED -> SUBMITTING without invoking the internal broker, while forbidding direct R6 "
        "OrderStore mutation or any Safety/OMS bypass. The staged order must then be consumed only by "
        "the certified canary/final-write path and remain restart/ambiguity safe."
    ),
    "resolution": "",
    "severity": "P1",
    "status": "OPEN",
    "track": "R6",
}


def main() -> int:
    debt = json.loads(DEBT_JSON.read_text(encoding="utf-8"))
    items = debt.get("items")
    if not isinstance(items, list):
        raise SystemExit("debt register items missing")
    if any(item.get("id") == "TD-R6-010" for item in items):
        raise SystemExit("TD-R6-010 already exists")
    anchor_indexes = [i for i, item in enumerate(items) if item.get("id") == "TD-R6-009"]
    if len(anchor_indexes) != 1:
        raise SystemExit("TD-R6-009 anchor missing or duplicated")
    items.insert(anchor_indexes[0] + 1, NEW_ITEM)
    DEBT_JSON.write_text(json.dumps(debt, indent=2) + "\n", encoding="utf-8")

    md = DEBT_MD.read_text(encoding="utf-8")
    row009 = (
        "| `TD-R6-009` | P1 | final write Safety/OMS recheck | **CLOSED** — dual authoritative "
        "PRE_CONSUME/PRE_IO recheck, cryptographic phase chain, version-race rejection, zero-I/O fail-closed |"
    )
    row010 = (
        "| `TD-R6-010` | P1 | OMS-owned external PAPER handoff | OPEN — production must own the "
        "VALIDATED→SUBMITTING transition through OMS; direct R6 OrderStore mutation is forbidden |"
    )
    if md.count(row009) != 1:
        raise SystemExit("TD-R6-009 human anchor missing or duplicated")
    md = md.replace(row009, row009 + "\n" + row010, 1)
    if md.count("R6 P0/P1/P2 OPEN: **6**.") != 1:
        raise SystemExit("R6 open-count anchor missing or duplicated")
    md = md.replace("R6 P0/P1/P2 OPEN: **6**.", "R6 P0/P1/P2 OPEN: **7**.", 1)
    DEBT_MD.write_text(md, encoding="utf-8")

    task = TASK.read_text(encoding="utf-8")
    debt_anchor = "- `TD-R6-008` — permanent PAPER-only/LIVE-deny authority boundary."
    debt_line = (
        "- `TD-R6-010` — OMS-owned external PAPER handoff; no direct OrderStore status mutation."
    )
    if task.count(debt_anchor) != 1:
        raise SystemExit("R6 debt-list anchor missing")
    task = task.replace(debt_anchor, debt_anchor + "\n" + debt_line, 1)

    old_order = (
        "7. bounded external PAPER evidence only after all prior gates are green;\n"
        "8. adversarial certification + debt closure."
    )
    new_order = (
        "7. OMS-owned external PAPER handoff: durable `VALIDATED -> SUBMITTING` without internal-broker I/O or direct store mutation;\n"
        "8. bounded external PAPER evidence only after all prior gates, including `TD-R6-010`, are green;\n"
        "9. adversarial certification + debt closure."
    )
    if task.count(old_order) != 1:
        raise SystemExit("R6 implementation-order anchor missing")
    task = task.replace(old_order, new_order, 1)

    negative_anchor = (
        "- no deterministic Safety/OMS approval => gateway cannot submit;"
    )
    negative_line = (
        "- direct R6 mutation of OMS order status to `SUBMITTING`, or staging without fresh control-plane identity, => fail closed;"
    )
    if task.count(negative_anchor) != 1:
        raise SystemExit("R6 negative-test anchor missing")
    task = task.replace(negative_anchor, negative_anchor + "\n" + negative_line, 1)

    restriction = (
        "- No external PAPER submit until gateway + ambiguity + canary + authority gates are implemented and green."
    )
    tightened = (
        "- No external PAPER submit until gateway + ambiguity + canary + authority gates are implemented and green, "
        "and `TD-R6-010` proves the OMS-owned external handoff."
    )
    if task.count(restriction) != 1:
        raise SystemExit("R6 submit restriction anchor missing")
    task = task.replace(restriction, tightened, 1)
    TASK.write_text(task, encoding="utf-8")

    print("TD-R6-010 registered before implementation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
