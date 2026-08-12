from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/connectivity_operator_decision.py"
CLI = ROOT / "scripts/r6_issue_connectivity_operator_decision.py"

FORBIDDEN_IMPORTS = (
    "autotrade.health",
    "autotrade.health_bridge",
    "autotrade.research",
    "autotrade.brokers.alpaca_paper_execution_bridge",
    "autotrade.brokers.alpaca_paper_operational_execute",
    "autotrade.brokers.alpaca_paper_writer",
    "autotrade.brokers.alpaca_paper_operator_decision",
    "requests",
    "urllib",
    "websockets",
    "httpx",
    "openai",
    "anthropic",
)
FORBIDDEN_CALLS = {
    "stage_external_submission",
    "submit_once",
    "post",
    "send",
    "execute",
    "mark_submit_attempt_unknown",
}


def main() -> int:
    errors: list[str] = []
    for path in (MODULE, CLI):
        if not path.is_file():
            errors.append(f"missing connectivity operator surface: {path.relative_to(ROOT)}")
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
                        f"{path.relative_to(ROOT)}:{node.lineno}: forbidden execution/network call {node.func.attr}"
                    )

    if MODULE.is_file():
        source = MODULE.read_text(encoding="utf-8")
        for anchor in (
            'self.purpose != "CONNECTIVITY_CANARY"',
            '_ACTION = "APPROVE_CONNECTIVITY_CANARY"',
            '_SOURCE = "HUMAN_OPERATOR"',
            '_OPERATOR_DB = "connectivity_operator.sqlite3"',
            'order.status is not OrderStatus.VALIDATED',
            'submission.status is not PaperSubmissionStatus.PREPARED',
            'submission.attempt_count != 0',
            'permit.status is not PaperCanaryPermitStatus.ISSUED',
            '"oms_staging_authorized": False',
            '"external_post_authorized": False',
            '"strategy_trading_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
            '"next_action": "CONNECTIVITY_FINAL_FRESHNESS_REQUIRED"',
            'self._workspace.operator_context_path',
            'self._workspace.operator_db_path',
        ):
            if anchor not in source:
                errors.append(f"connectivity operator module anchor missing: {anchor}")
        for forbidden in (
            "health_allows_new_exposure=True",
            "APPROVE_SINGLE_PAPER_CANARY",
            "R6_EXTERNAL_PAPER_WRITE=ENABLED\"",
        ):
            if forbidden in source:
                errors.append(f"connectivity operator module contains forbidden strategy/write surface: {forbidden}")

    if CLI.is_file():
        source = CLI.read_text(encoding="utf-8")
        for anchor in (
            'os.environ.get(_WRITE_ENV) == "ENABLED"',
            'os.environ.get(_KEY_ENV) or os.environ.get(_SECRET_ENV)',
            "sys.stdin.isatty()",
            "sys.stdout.isatty()",
            "connectivity_operator_confirmation_challenge(context)",
            '"oms_staging_authorized": False',
            '"external_post_authorized": False',
            '"strategy_trading_authorized": False',
            '"next_action": "CONNECTIVITY_FINAL_FRESHNESS_REQUIRED"',
        ):
            if anchor not in source:
                errors.append(f"connectivity operator CLI anchor missing: {anchor}")
        for forbidden in ("--key", "--secret", "--execute", "--submit"):
            if forbidden in source:
                errors.append(f"connectivity operator CLI contains forbidden option: {forbidden}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 connectivity operator boundary: PASS "
        "(purpose-bound HUMAN_OPERATOR; OMS remains VALIDATED; submission remains PREPARED; "
        "separate registry; no Strategy Health, staging, network or POST authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
