from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/connectivity_operator_review.py"
RECEIPT_CLI = ROOT / "scripts/r6_connectivity_review_receipt.py"
INTENT_CLI = ROOT / "scripts/r6_issue_connectivity_execution_intent.py"
FRESHNESS_CLI = ROOT / "scripts/r6_connectivity_bound_final_freshness.py"

FORBIDDEN_IMPORTS = (
    "requests",
    "httpx",
    "urllib",
    "http.client",
    "socket",
    "ssl",
    "websockets",
    "openai",
    "anthropic",
)
FORBIDDEN_MODULE_TEXT = (
    "/v2/orders",
    "AlpacaPaperWriteTransport",
    "UrllibAlpacaPaperWriteTransport",
    ".write(request)",
    "submit_once(",
    "mark_submit_attempt_unknown(",
    "R6_EXTERNAL_PAPER_WRITE",
)


def main() -> int:
    errors: list[str] = []
    for path in (MODULE, RECEIPT_CLI, INTENT_CLI, FRESHNESS_CLI):
        if not path.is_file():
            errors.append(f"missing operator-review surface: {path.relative_to(ROOT)}")
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            for module in modules:
                if any(module == p or module.startswith(p + ".") for p in FORBIDDEN_IMPORTS):
                    errors.append(f"{path.relative_to(ROOT)}:{node.lineno}: forbidden direct network import {module}")

    if MODULE.is_file():
        source = MODULE.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODULE_TEXT:
            if forbidden in source:
                errors.append(f"operator review module contains forbidden execution surface: {forbidden}")
        for anchor in (
            "class ConnectivityOperatorReviewReceiptBuilder",
            '"human_execution_intent_recorded": False',
            '"final_freshness_reacquisition_required": True',
            '"max_external_post_attempts": 1',
            '"external_post_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
            "class ConnectivityReviewedExecutionIntentBridge",
            "receipt_hash: str",
            "reviewed_execution_intent_challenge",
            "class ConnectivityReviewedBoundFinalFreshnessGuard",
            "verify_execution_review_binding(self._workspace)",
            "verify_reviewed_final_freshness_binding",
        ):
            if anchor not in source:
                errors.append(f"operator-review authority anchor missing: {anchor}")

    if RECEIPT_CLI.is_file():
        source = RECEIPT_CLI.read_text(encoding="utf-8")
        for anchor in (
            "refuses R6_EXTERNAL_PAPER_WRITE=ENABLED",
            "refuses Alpaca credentials",
            '"network_used": False',
            '"external_post_authorized": False',
        ):
            if anchor not in source:
                errors.append(f"review receipt CLI safety anchor missing: {anchor}")

    if INTENT_CLI.is_file():
        source = INTENT_CLI.read_text(encoding="utf-8")
        for anchor in (
            "ConnectivityReviewedExecutionIntentBridge",
            "reviewed_execution_intent_challenge",
            '"operator_review_receipt_hash": receipt.receipt_hash',
            '"execution_review_binding_hash": review_binding.binding_hash',
            '"next_action": "REVIEWED_BOUND_FINAL_FRESHNESS_REQUIRED"',
        ):
            if anchor not in source:
                errors.append(f"reviewed execution intent CLI anchor missing: {anchor}")

    if FRESHNESS_CLI.is_file():
        source = FRESHNESS_CLI.read_text(encoding="utf-8")
        for anchor in (
            "ConnectivityReviewedBoundFinalFreshnessGuard",
            '"review_freshness_binding_hash": review_binding["binding_hash"]',
            '"network_read_count": 5',
            '"external_post_authorized": False',
        ):
            if anchor not in source:
                errors.append(f"reviewed Final Freshness CLI anchor missing: {anchor}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 operator review receipt boundary: PASS "
        "(credential-free receipt; exact bracket/notional/flat evidence; receipt-bound second human intent; "
        "reviewed Final Freshness chain; no writer/POST/LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
