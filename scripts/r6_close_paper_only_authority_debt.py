from __future__ import annotations

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
DEBT_JSON = ROOT / "knowledge/00_CANON/debt_register.json"
DEBT_MD = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
EVIDENCE = ROOT / "knowledge/60_EVIDENCE/R6_PAPER_ONLY_AUTHORITY_EVIDENCE.json"

IMPLEMENTATION_SHA = "5a4f4730ef6ac242c52f925bb94ccdd0bd7eb5fc"
CORE_RUN = 31513050038
KNOWLEDGE_RUN = 31513049813
R6_AUTHORITY_RUN = 31513049786
TESTS_PASSED = 966
COVERAGE_PERCENT = 85.89

EVIDENCE_FILES = [
    "scripts/check_r6_live_deny_boundary.py",
    "scripts/check_r6_authority.py",
    "tests/test_r6_live_deny_boundary.py",
    "tests/test_r6_authority_checker.py",
    ".github/workflows/core-tests.yml",
    ".github/workflows/r6-authority.yml",
    "knowledge/60_EVIDENCE/R6_PAPER_ONLY_AUTHORITY_EVIDENCE.json",
]

RESOLUTION = (
    "Permanent fail-closed R6 PAPER-only authority boundary scans every alpaca_paper_* production module. "
    "It permits only explicit LIVE deny constants, prevents LIVE URL construction, fixes HTTP Request methods "
    "and low-level I/O budgets, allows exactly one audited writer transport write, bounds trade_updates to one "
    "WSS connect plus auth/listen control frames, forbids OpenAI/Anthropic/autotrade.research authority imports, "
    "and forbids new LIVE promotion identifiers. Safety/OMS final-write and exact PAPER host/path anchors remain "
    f"mandatory. Official implementation exact-head CI on {IMPLEMENTATION_SHA}: Core {CORE_RUN}, "
    f"Knowledge {KNOWLEDGE_RUN}, R6 Authority {R6_AUTHORITY_RUN} PASS; {TESTS_PASSED} tests / "
    f"{COVERAGE_PERCENT:.2f}% coverage."
)


def main() -> int:
    debt = json.loads(DEBT_JSON.read_text(encoding="utf-8"))
    matches = [item for item in debt.get("items", []) if item.get("id") == "TD-R6-008"]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one TD-R6-008, found {len(matches)}")
    item = matches[0]
    if item.get("track") != "R6" or item.get("severity") != "P1":
        raise SystemExit("TD-R6-008 identity/severity mismatch")
    if item.get("status") != "OPEN":
        raise SystemExit(f"TD-R6-008 must be OPEN before closure, found {item.get('status')}")

    item["evidence"] = EVIDENCE_FILES
    item["resolution"] = RESOLUTION
    item["status"] = "CLOSED"
    DEBT_JSON.write_text(json.dumps(debt, indent=2) + "\n", encoding="utf-8")

    md = DEBT_MD.read_text(encoding="utf-8")
    old_row = (
        "| `TD-R6-008` | P1 | permanent PAPER-only authority boundary | "
        "CI prevents LIVE host, Safety/OMS bypass, AI authorization and PAPER→LIVE authority creep |"
    )
    new_row = (
        "| `TD-R6-008` | P1 | permanent PAPER-only authority boundary | "
        "**CLOSED** — dual permanent CI gates enforce exact PAPER-only network/write authority, "
        "LIVE-deny, Safety/OMS and AI/research separation |"
    )
    if md.count(old_row) != 1:
        raise SystemExit("TD-R6-008 human row anchor missing or duplicated")
    md = md.replace(old_row, new_row, 1)
    old_count = "R6 P0/P1/P2 OPEN: **7**."
    if md.count(old_count) != 1:
        raise SystemExit("R6 open-count anchor missing or duplicated")
    md = md.replace(old_count, "R6 P0/P1/P2 OPEN: **6**.", 1)
    DEBT_MD.write_text(md, encoding="utf-8")

    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    if evidence.get("debt_id") != "TD-R6-008" or evidence.get("track") != "R6":
        raise SystemExit("TD-R6-008 evidence identity mismatch")
    if evidence.get("external_paper_order_sent") is not False:
        raise SystemExit("TD-R6-008 evidence must retain external_paper_order_sent=false")
    if evidence.get("live_trading") != "BLOCKED" or evidence.get("capital_authority") != "NONE":
        raise SystemExit("TD-R6-008 evidence authority/nonclaim invariant mismatch")

    evidence["status"] = "CLOSED_AFTER_OFFICIAL_EXACT_HEAD_CI"
    evidence["implementation_certification"] = {
        "head_sha": IMPLEMENTATION_SHA,
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
        "r6_product_boundary": "PASS",
        "debt_register": "PASS",
        "knowledge_contract": "PASS",
    }
    evidence["next_gate"] = (
        "Post-closure exact-head CI must remain green; TD-R6-001..006 remain OPEN."
    )
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    print("TD-R6-008 canonical closure patch applied; certification still required before commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
