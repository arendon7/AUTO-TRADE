from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/connectivity_final_freshness.py"
CLI = ROOT / "scripts/r6_connectivity_final_freshness.py"

FORBIDDEN_IMPORTS = (
    "autotrade.health",
    "autotrade.health_bridge",
    "autotrade.research",
    "autotrade.brokers.alpaca_paper_execution_bridge",
    "autotrade.brokers.alpaca_paper_operational_execute",
    "autotrade.brokers.alpaca_paper_writer",
    "autotrade.brokers.alpaca_paper_operator_decision",
    "requests",
    "httpx",
    "websockets",
    "openai",
    "anthropic",
)
FORBIDDEN_CALLS = {
    "stage_external_submission",
    "submit_once",
    "mark_submit_attempt_unknown",
    "consume_execution_authority",
    "record_operator_approval",
    "post",
    "send",
}


def main() -> int:
    errors: list[str] = []
    for path in (MODULE, CLI):
        if not path.is_file():
            errors.append(f"missing final freshness surface: {path.relative_to(ROOT)}")
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
                        f"{path.relative_to(ROOT)}:{node.lineno}: forbidden execution/network-write call {node.func.attr}"
                    )

    if MODULE.is_file():
        source = MODULE.read_text(encoding="utf-8")
        for anchor in (
            'self._account_gateway.attest_account(',
            'self._asset_gateway.attest_asset(',
            'self._flat_gateway.attest_flatness(',
            'self._market_gateway.attest_snapshot(',
            'network_methods": ["GET", "GET", "GET", "GET", "GET"]',
            '"network_read_count": 5',
            'CapitalSafetyKernel(limits, ledger, state_store=safety_store).evaluate(',
            'order.status is not OrderStatus.VALIDATED',
            'submission.status is not PaperSubmissionStatus.PREPARED',
            'submission.attempt_count != 0',
            '_FINAL_TTL_SECONDS = 5',
            '"initial_preflight_artifacts_modified": False',
            '"oms_staging_authorized": False',
            '"external_post_authorized": False',
            '"external_order_submitted": False',
            '"strategy_health_required": False',
            '"strategy_health_created": False',
            '"strategy_trading_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
            '"next_action": "EXPLICIT_CONNECTIVITY_EXECUTION_DECISION_REQUIRED"',
        ):
            if anchor not in source:
                errors.append(f"final freshness module anchor missing: {anchor}")
        for forbidden in (
            "health_allows_new_exposure=True",
            "OrderStatus.SUBMITTING",
            '"external_post_authorized": True',
            '"oms_staging_authorized": True',
            '"capital_authority": "GRANTED"',
        ):
            if forbidden in source:
                errors.append(f"final freshness module contains forbidden authority: {forbidden}")

    if CLI.is_file():
        source = CLI.read_text(encoding="utf-8")
        for anchor in (
            'not args.allow_paper_final_freshness_read',
            'os.environ.get(_WRITE_ENV) == "ENABLED"',
            'network_read_count": 5',
            '"oms_staging_authorized": False',
            '"external_post_authorized": False',
            '"external_order_submitted": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
        ):
            if anchor not in source:
                errors.append(f"final freshness CLI anchor missing: {anchor}")
        for forbidden in ("--key", "--secret", "--execute", "--submit", "--write"):
            if forbidden in source:
                errors.append(f"final freshness CLI contains forbidden option: {forbidden}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 connectivity final freshness boundary: PASS "
        "(exact 5 GETs; fresh Capital Safety; <=5s eligibility; OMS VALIDATED; "
        "submission PREPARED; no Strategy Health/staging/POST/LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
