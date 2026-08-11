from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT_JSON = ROOT / "knowledge/00_CANON/debt_register.json"
DEBT_MD = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
TASK = ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md"

DEBT_ID = "TD-R6-013"


def main() -> int:
    debt = json.loads(DEBT_JSON.read_text(encoding="utf-8"))
    items = debt.get("items")
    if not isinstance(items, list):
        raise SystemExit("debt register items missing")
    if any(isinstance(item, dict) and item.get("id") == DEBT_ID for item in items):
        raise SystemExit(f"{DEBT_ID} already exists")
    items.append(
        {
            "area": "Operational external PAPER qualification lifecycle harness",
            "evidence": [],
            "id": DEBT_ID,
            "next_action": (
                "Implement a durable, reproducible operational R6 lifecycle with strict authority separation before the first real PAPER canary. "
                "A preparation command may load PAPER credentials only from environment, perform exact account attestation/read-only preflight, initialize durable state, "
                "prepare the exact bounded canary package and operator-decision context, and emit sanitized artifacts, but it must not import/call the execution bridge, writer or any order POST. "
                "Execution must remain a separate explicit human-only command using the certified operator decision and existing execution bridge/writer; evidence collection must separately reconcile by client_order_id, "
                "capture the exact nested bracket and bounded trade_updates stream, and build qualification artifacts. Permanent CI must forbid preparation/evidence tools from widening network/write authority, leaking credentials, "
                "or converting evidence capture into execution authority. No real PAPER POST is part of closing this structural debt."
            ),
            "resolution": "",
            "severity": "P1",
            "status": "OPEN",
            "track": "R6",
        }
    )
    DEBT_JSON.write_text(json.dumps(debt, indent=2) + "\n", encoding="utf-8")

    md = DEBT_MD.read_text(encoding="utf-8")
    row_anchor = "| `TD-R6-012` | P1 | crash-safe same-attempt resume | **CLOSED** — only PREPARED + zero attempts + same-attempt CONSUMED may resume; UNKNOWN/different/stale remain fail-closed |\n"
    new_row = row_anchor + "| `TD-R6-013` | P1 | operational external PAPER lifecycle harness | OPEN — durable read-only preparation + separate human execution + separate evidence capture; preparation cannot import/call POST authority |\n"
    if md.count(row_anchor) != 1:
        raise SystemExit("TD-R6-012 human register anchor missing")
    md = md.replace(row_anchor, new_row, 1)
    if md.count("R6 P0/P1/P2 OPEN: **6**.") != 1:
        raise SystemExit("R6 open count anchor missing")
    md = md.replace("R6 P0/P1/P2 OPEN: **6**.", "R6 P0/P1/P2 OPEN: **7**.", 1)
    DEBT_MD.write_text(md, encoding="utf-8")

    task = TASK.read_text(encoding="utf-8")
    debt_anchor = "- `TD-R6-012` — **CLOSED** — crash-safe same-attempt resume certified; UNKNOWN/different attempt remains reconciliation-only.\n"
    if task.count(debt_anchor) != 1:
        raise SystemExit("TD-R6-012 task anchor missing")
    task = task.replace(
        debt_anchor,
        debt_anchor + "- `TD-R6-013` — operational external PAPER lifecycle harness; read-only preparation, separate human execution and separate evidence capture.\n",
        1,
    )
    order_anchor = "10. bounded external PAPER evidence only after all prior gates are green and an explicit final operator decision exists;\n11. adversarial certification + debt closure.\n"
    if task.count(order_anchor) != 1:
        raise SystemExit("implementation order anchor missing")
    task = task.replace(
        order_anchor,
        "10. operational external PAPER lifecycle harness (`TD-R6-013`): durable read-only preparation, separate human execution and separate evidence capture;\n"
        "11. bounded external PAPER evidence only after all prior gates are green and an explicit final operator decision exists;\n"
        "12. adversarial certification + debt closure.\n",
        1,
    )
    restriction = "- No external PAPER submit until gateway + ambiguity + canary + authority gates are green; `TD-R6-010/011/012` now prove OMS-owned handoff, explicit human-only operator authority and crash-safe same-attempt semantics, but they do not themselves authorize a real external PAPER order.\n"
    if task.count(restriction) != 1:
        raise SystemExit("task restriction anchor missing")
    task = task.replace(
        restriction,
        restriction + "- `TD-R6-013` must close structurally before any real canary: preparation and evidence commands must remain non-authorizing; real execution stays separate and explicitly human-triggered.\n",
        1,
    )
    TASK.write_text(task, encoding="utf-8")
    print("TD-R6-013 registered OPEN before operational harness implementation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
