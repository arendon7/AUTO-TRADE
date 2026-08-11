from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DEBT_JSON = ROOT / "knowledge/00_CANON/debt_register.json"
DEBT_MD = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
TASK = ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md"
EVIDENCE = ROOT / "knowledge/60_EVIDENCE/R6_OMS_EXTERNAL_HANDOFF_EVIDENCE.json"

IMPLEMENTATION_HEAD = "dfc2424f7bc42552ad7fc0a4ffb1f03284f76dad"
CORE_RUN = 31516722995
KNOWLEDGE_RUN = 31516722976
R6_AUTHORITY_RUN = 31516722971
TESTS_PASSED = 979
COVERAGE_PERCENT = 85.56

EVIDENCE_FILES = [
    "src/autotrade/oms.py",
    "src/autotrade/brokers/alpaca_paper_writer.py",
    "scripts/check_r6_oms_handoff_boundary.py",
    "tests/test_r6_oms_external_handoff.py",
    "tests/test_r6_oms_handoff_boundary.py",
    "tests/test_r6_paper_writer.py",
    ".github/workflows/core-tests.yml",
    ".github/workflows/r6-authority.yml",
    "knowledge/60_EVIDENCE/R6_OMS_EXTERNAL_HANDOFF_EVIDENCE.json",
]

RESOLUTION = (
    "Implemented an OMS-owned, brokerless external PAPER staging path. "
    "validate_for_external_submission reuses the normal deterministic control plane and creates/replays only VALIDATED state; "
    "stage_external_submission revalidates the original RiskDecision/MarketSnapshot, exact Safety version and NORMAL Health, "
    "durably appends EXTERNAL_ORDER_HANDOFF_AUTHORIZED before transitioning VALIDATED->SUBMITTING, and is restart-safe across "
    "a crash between event append and status update. SUBMITTING without the durable handoff fails closed. The writer requires the "
    "durable handoff, binds it to the one-shot canary approval and frozen submission binding, verifies it through OMS, and requires "
    "PRE_CONSUME Safety version equality before the existing PRE_IO guard. Permanent CI forbids direct R6 OrderStore mutation, "
    "synthetic SUBMITTING state or handoff construction outside OMS. Official exact-head CI on "
    f"{IMPLEMENTATION_HEAD}: Core {CORE_RUN}, Knowledge {KNOWLEDGE_RUN}, R6 Authority {R6_AUTHORITY_RUN} PASS; "
    f"{TESTS_PASSED} tests / {COVERAGE_PERCENT:.2f}% coverage."
)


def replace_table_row(text: str, debt_id: str, replacement: str) -> str:
    lines = text.splitlines()
    matches = [i for i, line in enumerate(lines) if line.startswith(f"| `{debt_id}` |")]
    if len(matches) != 1:
        raise SystemExit(f"{debt_id} human row missing or duplicated")
    lines[matches[0]] = replacement
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def replace_task_line(text: str, debt_id: str, replacement: str) -> str:
    lines = text.splitlines()
    matches = [i for i, line in enumerate(lines) if line.startswith(f"- `{debt_id}`")]
    if len(matches) != 1:
        raise SystemExit(f"{debt_id} task line missing or duplicated")
    lines[matches[0]] = replacement
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def main() -> int:
    debt = json.loads(DEBT_JSON.read_text(encoding="utf-8"))
    matches = [item for item in debt.get("items", []) if item.get("id") == "TD-R6-010"]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one TD-R6-010, found {len(matches)}")
    item = matches[0]
    if item.get("track") != "R6" or item.get("severity") != "P1":
        raise SystemExit("TD-R6-010 identity/severity mismatch")
    if item.get("status") != "OPEN":
        raise SystemExit(f"TD-R6-010 must be OPEN before closure, found {item.get('status')}")
    item["evidence"] = EVIDENCE_FILES
    item["resolution"] = RESOLUTION
    item["status"] = "CLOSED"
    DEBT_JSON.write_text(json.dumps(debt, indent=2) + "\n", encoding="utf-8")

    md = DEBT_MD.read_text(encoding="utf-8")
    md = replace_table_row(
        md,
        "TD-R6-010",
        "| `TD-R6-010` | P1 | OMS-owned external PAPER handoff | **CLOSED** — OMS owns durable VALIDATED→SUBMITTING; handoff event precedes state change; direct R6 OrderStore mutation prohibited |",
    )
    if md.count("R6 P0/P1/P2 OPEN: **7**.") != 1:
        raise SystemExit("R6 blocking-open count anchor missing")
    md = md.replace("R6 P0/P1/P2 OPEN: **7**.", "R6 P0/P1/P2 OPEN: **6**.", 1)
    DEBT_MD.write_text(md, encoding="utf-8")

    task = TASK.read_text(encoding="utf-8")
    task = replace_task_line(
        task,
        "TD-R6-010",
        "- `TD-R6-010` — **CLOSED** — OMS-owned external PAPER handoff certified; no direct OrderStore status mutation.",
    )
    task = task.replace(
        "8. bounded external PAPER evidence only after all prior gates, including `TD-R6-010`, are green;",
        "8. bounded external PAPER evidence only after all prior gates are green; `TD-R6-010` is certified CLOSED;",
        1,
    )
    TASK.write_text(task, encoding="utf-8")

    evidence = {
        "schema_version": 1,
        "track": "R6",
        "debt_id": "TD-R6-010",
        "status": "CLOSED_AFTER_OFFICIAL_EXACT_HEAD_CI",
        "implementation_head_sha": IMPLEMENTATION_HEAD,
        "controls": [
            "OMS validates the external path through the normal deterministic RiskDecision/MarketSnapshot control plane without broker I/O.",
            "OMS alone owns VALIDATED->SUBMITTING for external PAPER; R6 broker modules cannot mutate OrderStore or synthesize SUBMITTING.",
            "EXTERNAL_ORDER_HANDOFF_AUTHORIZED is durable and hash-bound before the SUBMITTING update, making crash replay fail-safe.",
            "The handoff binds order, intent fingerprint, risk decision, Safety-state version, market fingerprint, decision expiry and authorization time.",
            "Staging re-reads Safety twice and requires the exact RiskDecision Safety version, blocking activate/reset races.",
            "Staging requires authoritative Health NORMAL with exact 1.0 order/strategy/portfolio multipliers.",
            "The writer requires the durable OMS handoff, binds handoff_id to the one-shot canary approval hash and re-verifies the handoff through OMS.",
            "PRE_CONSUME Safety version must still equal the OMS handoff Safety version; PRE_IO remains a second fail-closed race barrier.",
            "Permanent Core Safety and R6 Authority CI execute check_r6_oms_handoff_boundary.py plus adversarial tests."
        ],
        "evidence_files": EVIDENCE_FILES[:-1],
        "official_exact_head_ci": {
            "head_sha": IMPLEMENTATION_HEAD,
            "tests_passed": TESTS_PASSED,
            "coverage_percent": COVERAGE_PERCENT,
            "core_safety_run_id": CORE_RUN,
            "knowledge_contract_run_id": KNOWLEDGE_RUN,
            "r6_authority_run_id": R6_AUTHORITY_RUN,
            "contract_registry": "PASS",
            "research_authority": "PASS",
            "r5_authority": "PASS",
            "r6_authority": "PASS",
            "r6_live_deny_boundary": "PASS",
            "r6_oms_handoff_boundary": "PASS",
            "r6_product_boundary": "PASS",
            "debt_register": "PASS",
            "knowledge_contract": "PASS"
        },
        "external_paper_order_sent": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "remaining_r6_blocking_open": 6,
        "next_gate": "Build and certify the integrated manual single-shot canary coordinator; do not send an external PAPER order without an explicit final execution decision.",
        "non_claims": [
            "This evidence does not qualify an external PAPER execution.",
            "This evidence does not demonstrate profitability.",
            "This evidence does not authorize LIVE trading."
        ]
    }
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print("TD-R6-010 closure patch applied; certification required before commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
