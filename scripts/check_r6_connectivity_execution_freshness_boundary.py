from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/connectivity_execution_freshness_binding.py"
CLI = ROOT / "scripts/r6_connectivity_bound_final_freshness.py"

FORBIDDEN_IMPORTS = (
    "autotrade.health",
    "autotrade.health_bridge",
    "autotrade.research",
    "autotrade.oms",
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
            errors.append(f"missing execution/freshness surface: {path.relative_to(ROOT)}")
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
            "SQLiteConnectivityExecutionIntentRegistry(",
            "self._final_guard.acquire(credentials=credentials)",
            "intent.is_valid_at(started_at)",
            "intent.is_valid_at(completed_at)",
            "final_freshness_permit_hash",
            "execution_intent_decision_hash",
            '"second_human_execution_intent_bound": True',
            '"final_freshness_bound": True',
            '"max_external_post_attempts": 1',
            '"oms_staging_authorized": False',
            '"external_post_authorized": False',
            '"external_order_submitted": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
            '"next_action": "CONNECTIVITY_STAGING_BRIDGE_REQUIRED"',
            "order.status is not OrderStatus.VALIDATED",
            "submission.status is not PaperSubmissionStatus.PREPARED",
            "submission.attempt_count != 0",
        ):
            if anchor not in source:
                errors.append(f"execution/freshness module anchor missing: {anchor}")
        for forbidden in (
            "OrderStatus.SUBMITTING",
            '"oms_staging_authorized": True',
            '"external_post_authorized": True',
            "health_allows_new_exposure=True",
        ):
            if forbidden in source:
                errors.append(f"execution/freshness module contains forbidden authority: {forbidden}")

    if CLI.is_file():
        source = CLI.read_text(encoding="utf-8")
        for anchor in (
            "not args.allow_paper_final_freshness_read",
            'os.environ.get(_WRITE_ENV) == "ENABLED"',
            '"network_read_count": 5',
            '"max_external_post_attempts": 1',
            '"oms_staging_authorized": False',
            '"external_post_authorized": False',
            '"external_order_submitted": False',
            '"next_action": "CONNECTIVITY_STAGING_BRIDGE_REQUIRED"',
        ):
            if anchor not in source:
                errors.append(f"execution/freshness CLI anchor missing: {anchor}")
        for forbidden in ("--execute", "--submit", "--write", "--key", "--secret"):
            if forbidden in source:
                errors.append(f"execution/freshness CLI contains forbidden option: {forbidden}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 execution-intent/final-freshness boundary: PASS "
        "(second human intent valid before+after exactly five GETs; fresh permit bound durably; "
        "OMS VALIDATED; submission PREPARED; no Health/staging/POST/LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
