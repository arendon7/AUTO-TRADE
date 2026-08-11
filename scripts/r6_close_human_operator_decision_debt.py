from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT_JSON = ROOT / "knowledge/00_CANON/debt_register.json"
DEBT_MD = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
TASK = ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md"
EVIDENCE = ROOT / "knowledge/60_EVIDENCE/R6_HUMAN_OPERATOR_EXECUTION_EVIDENCE.json"

IMPLEMENTATION_HEAD = "5e947ad51477cd3f6a4a544714caaf474624a970"
CORE_RUN = 31524002513
KNOWLEDGE_RUN = 31524002588
R6_AUTHORITY_RUN = 31524002550
TESTS_PASSED = 1050
COVERAGE_PERCENT = 85.04

EVIDENCE_FILES = [
    "src/autotrade/brokers/alpaca_paper_canary_coordinator.py",
    "src/autotrade/brokers/alpaca_paper_operator_decision.py",
    "src/autotrade/brokers/alpaca_paper_execution_bridge.py",
    "src/autotrade/brokers/alpaca_paper_writer.py",
    "src/autotrade/brokers/alpaca_paper_canary_permit.py",
    "scripts/r6_issue_operator_decision.py",
    "scripts/check_r6_canary_coordinator_boundary.py",
    "scripts/check_r6_operator_decision_boundary.py",
    "scripts/check_r6_execution_bridge_boundary.py",
    "scripts/check_r6_writer_human_gate.py",
    "scripts/check_r6_oms_handoff_boundary.py",
    "scripts/check_r6_live_deny_boundary.py",
    "scripts/check_r6_authority.py",
    "scripts/check_r6_product_boundary.py",
    "tests/test_r6_paper_canary_coordinator.py",
    "tests/test_r6_operator_decision.py",
    "tests/test_r6_paper_execution_bridge.py",
    "tests/test_r6_writer_human_gate.py",
    "tests/test_r6_writer_human_gate_boundary.py",
    "tests/test_r6_paper_writer.py",
    "tests/test_r6_writer_same_attempt_resume.py",
    "tests/test_r6_paper_canary_permit.py",
    ".github/workflows/core-tests.yml",
    ".github/workflows/r6-authority.yml",
    "knowledge/60_EVIDENCE/R6_HUMAN_OPERATOR_EXECUTION_EVIDENCE.json",
]

RESOLUTION = (
    "Implemented and certified an explicit durable human-only final PAPER execution gate. "
    "The offline canary coordinator produces a tamper-evident PreparedPaperCanaryPackage while OMS remains VALIDATED and hard-codes network_write_authorized=false plus OPERATOR_DECISION_REQUIRED. "
    "PaperOperatorDecisionContext.from_prepared_package binds the exact package hash, PAPER environment, account attestation, order/client_order_id, submission binding, bracket payload, canary approval, notional and deterministic attempt identity. "
    "Only the interactive human issuer may mint APPROVE_SINGLE_PAPER_CANARY; AI/research/application defaults cannot mint operator authority. "
    "The tamper-evident operator registry enforces short TTL, one-shot consume, exact same-attempt idempotency and cross-attempt conflict. "
    "The no-network PaperCanaryExecutionBridge verifies the human-reviewed RiskDecision Safety version and market fingerprint, consumes the durable human decision before OMS staging, and alone may transition OMS VALIDATED->SUBMITTING. "
    "The single POST writer requires the exact prepared package, a durable CONSUMED human decision for the same attempt, and the exact execution-stage/handoff evidence before permit consumption, UNKNOWN persistence, PRE_IO and the sole transport write. "
    "Missing, stale, mismatched, merely ISSUED, replayed or cross-attempt operator evidence fails closed with zero network I/O. "
    "The canary package is bound to the immutable verified permit ISSUED-event hash so same-attempt crash-safe resume remains valid after the permit state advances to CONSUMED. "
    f"Official exact-head CI on {IMPLEMENTATION_HEAD}: Core {CORE_RUN}, Knowledge {KNOWLEDGE_RUN}, R6 Authority {R6_AUTHORITY_RUN} PASS; "
    f"{TESTS_PASSED} tests / {COVERAGE_PERCENT:.2f}% coverage."
)


def replace_prefixed_line(text: str, prefix: str, replacement: str) -> str:
    lines = text.splitlines()
    matches = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if len(matches) != 1:
        raise SystemExit(f"expected one line with prefix {prefix!r}, found {len(matches)}")
    lines[matches[0]] = replacement
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def main() -> int:
    debt = json.loads(DEBT_JSON.read_text(encoding="utf-8"))
    matches = [item for item in debt.get("items", []) if item.get("id") == "TD-R6-011"]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one TD-R6-011, found {len(matches)}")
    item = matches[0]
    if item.get("track") != "R6" or item.get("severity") != "P1" or item.get("status") != "OPEN":
        raise SystemExit("TD-R6-011 identity/severity/status mismatch")
    item["evidence"] = EVIDENCE_FILES
    item["resolution"] = RESOLUTION
    item["status"] = "CLOSED"
    DEBT_JSON.write_text(json.dumps(debt, indent=2) + "\n", encoding="utf-8")

    md = DEBT_MD.read_text(encoding="utf-8")
    md = replace_prefixed_line(
        md,
        "| `TD-R6-011` |",
        "| `TD-R6-011` | P1 | human final PAPER execution decision | **CLOSED** — exact prepared package → durable human-only one-shot decision → no-network execution bridge → OMS SUBMITTING → human-gated single POST writer |",
    )
    if md.count("R6 P0/P1/P2 OPEN: **7**.") != 1:
        raise SystemExit("R6 blocking-open count anchor missing")
    md = md.replace("R6 P0/P1/P2 OPEN: **7**.", "R6 P0/P1/P2 OPEN: **6**.", 1)
    DEBT_MD.write_text(md, encoding="utf-8")

    task = TASK.read_text(encoding="utf-8")
    task = replace_prefixed_line(
        task,
        "- `TD-R6-011`",
        "- `TD-R6-011` — **CLOSED** — exact prepared package + durable explicit human one-shot authority + execution bridge + human-gated writer certified.",
    )
    task = task.replace(
        "9. durable explicit human execution decision (`TD-R6-011`); crash-safe same-attempt resume (`TD-R6-012`) is certified CLOSED;",
        "9. durable explicit human execution decision (`TD-R6-011`) and crash-safe same-attempt resume (`TD-R6-012`) are certified CLOSED;",
        1,
    )
    task = task.replace(
        "- No external PAPER submit until gateway + ambiguity + canary + authority gates are green, `TD-R6-010` proves the OMS-owned handoff, and `TD-R6-011/012` prove explicit operator authority plus crash-safe one-shot semantics.",
        "- No external PAPER submit until gateway + ambiguity + canary + authority gates are green; `TD-R6-010/011/012` now prove OMS-owned handoff, explicit human-only operator authority and crash-safe same-attempt semantics, but they do not themselves authorize a real external PAPER order.",
        1,
    )
    TASK.write_text(task, encoding="utf-8")

    evidence = {
        "schema_version": 1,
        "track": "R6",
        "debt_id": "TD-R6-011",
        "status": "CLOSED_AFTER_OFFICIAL_EXACT_HEAD_CI",
        "implementation_head_sha": IMPLEMENTATION_HEAD,
        "controls": [
            "Offline preparation ends at OMS VALIDATED and cannot authorize or perform network writes.",
            "Prepared package hash binds RiskDecision Safety-state version and market fingerprint in addition to order, account, bracket, approval, permit issuance evidence and attempt identity.",
            "Human authority source is exactly HUMAN_OPERATOR and action exactly APPROVE_SINGLE_PAPER_CANARY with bounded TTL.",
            "Only the interactive issuer script may mint the durable operator approval; production coordinator, execution bridge and writer cannot mint it.",
            "Operator registry is append-only/tamper-evident and enforces exact-attempt one-shot consume; cross-attempt consume fails before bridge/writer.",
            "Execution bridge has no network API, verifies current RiskDecision/MarketSnapshot against the reviewed package, consumes operator authority before OMS staging, and alone may call stage_external_submission.",
            "Writer requires durable operator status CONSUMED by the exact attempt plus exact execution-stage/package/decision/handoff hashes before permit, UNKNOWN and POST.",
            "Writer cannot stage OMS or mint operator authority; it contains exactly one audited transport write.",
            "Prepared package verifies immutable permit ISSUED-event evidence while the current permit may legitimately advance to CONSUMED for same-attempt crash-safe resume.",
            "Missing, expired, mismatched, merely ISSUED, wrong-registry, wrong-market, wrong-Safety-version, replayed or cross-attempt human evidence produces zero broker I/O.",
            "Permanent Core Safety and R6 Authority CI enforce coordinator, operator-decision, execution-bridge, writer-human, OMS, LIVE-deny and product boundaries.",
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
            "contract_count": 10,
            "research_authority": "PASS",
            "r5_authority": "PASS",
            "r6_authority": "PASS",
            "r6_live_deny_boundary": "PASS",
            "r6_oms_handoff_boundary": "PASS",
            "r6_canary_coordinator_boundary": "PASS",
            "r6_operator_decision_boundary": "PASS",
            "r6_execution_bridge_boundary": "PASS",
            "r6_writer_human_gate": "PASS",
            "r6_product_boundary": "PASS",
            "debt_register": "PASS",
            "knowledge_contract": "PASS",
        },
        "external_paper_order_sent": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "remaining_r6_blocking_open": 6,
        "next_gate": "Collect bounded real external PAPER evidence for TD-R6-001..006 only after a separate explicit final operator execution decision and all real-environment prerequisites are satisfied.",
        "non_claims": [
            "This structural certification does not itself authorize sending a real external PAPER order.",
            "This evidence does not close TD-R6-001..006, which require bounded external broker/PAPER evidence.",
            "This evidence does not demonstrate profitability.",
            "This evidence does not authorize LIVE trading.",
        ],
    }
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print("TD-R6-011 closure patch applied; full certification required before publish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
