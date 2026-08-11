from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DEBT_JSON = ROOT / "knowledge/00_CANON/debt_register.json"
DEBT_MD = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
TASK = ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md"
EVIDENCE = ROOT / "knowledge/60_EVIDENCE/R6_SAME_ATTEMPT_RESUME_EVIDENCE.json"

HEAD = "e163dc74dfe2bc36e2be004d3d77d6d6e1df2ef4"
CORE = 31518438417
KNOWLEDGE = 31518438421
R6_AUTHORITY = 31518438415
TESTS = 983
COVERAGE = 85.54


def replace_row(text: str, debt_id: str, replacement: str) -> str:
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines) if line.startswith(f"| `{debt_id}` |")]
    if len(hits) != 1:
        raise SystemExit(f"{debt_id} row missing/duplicated")
    lines[hits[0]] = replacement
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def replace_task_line(text: str, debt_id: str, replacement: str) -> str:
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines) if line.startswith(f"- `{debt_id}`")]
    if len(hits) != 1:
        raise SystemExit(f"{debt_id} task line missing/duplicated")
    lines[hits[0]] = replacement
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def main() -> int:
    debt = json.loads(DEBT_JSON.read_text(encoding="utf-8"))
    matches = [item for item in debt["items"] if item.get("id") == "TD-R6-012"]
    if len(matches) != 1 or matches[0].get("status") != "OPEN":
        raise SystemExit("TD-R6-012 must exist once and be OPEN")
    item = matches[0]
    item["status"] = "CLOSED"
    item["evidence"] = [
        "src/autotrade/brokers/alpaca_paper_writer.py",
        "src/autotrade/brokers/alpaca_paper_canary_permit.py",
        "src/autotrade/brokers/alpaca_paper_submission.py",
        "tests/test_r6_writer_same_attempt_resume.py",
        "tests/test_r6_paper_writer.py",
        "tests/test_r6_paper_canary_permit.py",
        "knowledge/60_EVIDENCE/R6_SAME_ATTEMPT_RESUME_EVIDENCE.json",
    ]
    item["resolution"] = (
        "Writer restart semantics now permit exactly one narrow recovery case: durable submission PREPARED with zero submit attempts and "
        "a canary permit already CONSUMED by the exact same attempt_id. This state proves by code ordering that UNKNOWN was not persisted "
        "and therefore POST could not have been reached. The retry re-runs all freshness/OMS/Safety/final guards and idempotently reuses "
        "the same permit consumption before persisting UNKNOWN. A different attempt, expired approval, any UNKNOWN/ACKNOWLEDGED state, "
        "or conflicting/tampered permit remains fail-closed/reconciliation-only. Official clean-head CI on "
        f"{HEAD}: Core {CORE}, Knowledge {KNOWLEDGE}, R6 Authority {R6_AUTHORITY} PASS; {TESTS} tests / {COVERAGE:.2f}% coverage."
    )
    DEBT_JSON.write_text(json.dumps(debt, indent=2) + "\n", encoding="utf-8")

    md = DEBT_MD.read_text(encoding="utf-8")
    md = replace_row(
        md,
        "TD-R6-012",
        "| `TD-R6-012` | P1 | crash-safe same-attempt resume | **CLOSED** — only PREPARED + zero attempts + same-attempt CONSUMED may resume; UNKNOWN/different/stale remain fail-closed |",
    )
    if md.count("R6 P0/P1/P2 OPEN: **8**.") != 1:
        raise SystemExit("R6 open count 8 anchor missing")
    md = md.replace("R6 P0/P1/P2 OPEN: **8**.", "R6 P0/P1/P2 OPEN: **7**.", 1)
    DEBT_MD.write_text(md, encoding="utf-8")

    task = TASK.read_text(encoding="utf-8")
    task = replace_task_line(
        task,
        "TD-R6-012",
        "- `TD-R6-012` — **CLOSED** — crash-safe same-attempt resume certified; UNKNOWN/different attempt remains reconciliation-only.",
    )
    task = task.replace(
        "9. durable explicit human execution decision + crash-safe same-attempt resume (`TD-R6-011/012`);",
        "9. durable explicit human execution decision (`TD-R6-011`); crash-safe same-attempt resume (`TD-R6-012`) is certified CLOSED;",
        1,
    )
    TASK.write_text(task, encoding="utf-8")

    evidence = {
        "schema_version": 1,
        "track": "R6",
        "debt_id": "TD-R6-012",
        "status": "CLOSED_AFTER_OFFICIAL_EXACT_HEAD_CI",
        "implementation_head_sha": HEAD,
        "official_exact_head_ci": {
            "core_safety_run_id": CORE,
            "knowledge_contract_run_id": KNOWLEDGE,
            "r6_authority_run_id": R6_AUTHORITY,
            "tests_passed": TESTS,
            "coverage_percent": COVERAGE,
            "result": "PASS"
        },
        "invariants": [
            "PREPARED plus attempt_count=0 proves the writer has not crossed the durable UNKNOWN-before-POST boundary.",
            "A CONSUMED permit is resumable only when its attempt_id equals the current writer attempt_id.",
            "Approval freshness, frozen binding, OMS handoff, Safety/Health guards and PRE_IO are re-evaluated on resume.",
            "UNKNOWN is never converted back into write authority and always requires reconciliation.",
            "A different attempt cannot reuse a consumed permit.",
            "An expired approval cannot resume even when the permit belongs to the same attempt."
        ],
        "external_paper_order_sent": False,
        "live_trading": "BLOCKED",
        "capital_authority": "NONE",
        "remaining_r6_blocking_open": 7,
        "next_gate": "Implement TD-R6-011 durable explicit human final PAPER execution decision before any external PAPER POST."
    }
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print("TD-R6-012 closure patch applied; certify before publish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
