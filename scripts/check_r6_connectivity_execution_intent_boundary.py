from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/connectivity_execution_intent.py"
CLI = ROOT / "scripts/r6_issue_connectivity_execution_intent.py"

FORBIDDEN_IMPORTS = (
    "autotrade.health",
    "autotrade.health_bridge",
    "autotrade.research",
    "autotrade.connectivity_final_freshness",
    "autotrade.brokers.alpaca_paper_execution_bridge",
    "autotrade.brokers.alpaca_paper_operational_execute",
    "autotrade.brokers.alpaca_paper_writer",
    "requests",
    "httpx",
    "urllib",
    "websockets",
    "openai",
    "anthropic",
)
FORBIDDEN_CALLS = {
    "stage_external_submission",
    "submit_once",
    "mark_submit_attempt_unknown",
    "consume_execution_authority",
    "post",
    "send",
}


def main() -> int:
    errors: list[str] = []
    for path in (MODULE, CLI):
        if not path.is_file():
            errors.append(f"missing execution-intent surface: {path.relative_to(ROOT)}")
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
                if any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORTS):
                    errors.append(f"{path.relative_to(ROOT)}:{node.lineno}: forbidden import {module}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in FORBIDDEN_CALLS:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: forbidden staging/network-write call {node.func.attr}"
                    )

    if MODULE.is_file():
        source = MODULE.read_text(encoding="utf-8")
        for anchor in (
            '_ACTION = "CONFIRM_CONNECTIVITY_EXECUTION_INTENT"',
            '_SOURCE = "HUMAN_OPERATOR"',
            'self.purpose != "CONNECTIVITY_CANARY"',
            'self.max_external_post_attempts != 1',
            '_FINAL_FRESHNESS_ARTIFACT = "connectivity_final_freshness.json"',
            'order.status is not OrderStatus.VALIDATED',
            'submission.status is not PaperSubmissionStatus.PREPARED',
            'submission.attempt_count != 0',
            'permit.status is not PaperCanaryPermitStatus.ISSUED',
            '"human_execution_intent_recorded": True',
            '"max_external_post_attempts": 1',
            '"final_freshness_required": True',
            '"oms_staging_authorized": False',
            '"external_post_authorized": False',
            '"external_order_submitted": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
            '"next_action": "INLINE_FINAL_FRESHNESS_REQUIRED"',
        ):
            if anchor not in source:
                errors.append(f"execution-intent module anchor missing: {anchor}")
        for forbidden in (
            "OrderStatus.SUBMITTING",
            '"external_post_authorized": True',
            '"oms_staging_authorized": True',
            "health_allows_new_exposure=True",
        ):
            if forbidden in source:
                errors.append(f"execution-intent module contains forbidden authority: {forbidden}")

    if CLI.is_file():
        source = CLI.read_text(encoding="utf-8")
        for anchor in (
            'os.environ.get(_WRITE_ENV) == "ENABLED"',
            'os.environ.get(_KEY_ENV) or os.environ.get(_SECRET_ENV)',
            "sys.stdin.isatty()",
            "sys.stdout.isatty()",
            "connectivity_execution_intent_challenge(context)",
            '"max_external_post_attempts": 1',
            '"final_freshness_required": True',
            '"oms_staging_authorized": False',
            '"external_post_authorized": False',
            '"external_order_submitted": False',
            '"next_action": "INLINE_FINAL_FRESHNESS_REQUIRED"',
        ):
            if anchor not in source:
                errors.append(f"execution-intent CLI anchor missing: {anchor}")
        for forbidden in ("--key", "--secret", "--execute", "--submit", "--write"):
            if forbidden in source:
                errors.append(f"execution-intent CLI contains forbidden option: {forbidden}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 connectivity execution-intent boundary: PASS "
        "(second HUMAN_OPERATOR confirmation; one-attempt budget; Final Freshness still required; "
        "OMS VALIDATED; submission PREPARED; no network/staging/POST/LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
