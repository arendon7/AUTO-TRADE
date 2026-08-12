from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/connectivity_oms_stage.py"

FORBIDDEN_IMPORTS = (
    "autotrade.health",
    "autotrade.health_bridge",
    "autotrade.brokers",
    "requests",
    "httpx",
    "urllib",
    "websockets",
    "openai",
    "anthropic",
)
FORBIDDEN_CALLS = {
    "submit_once",
    "mark_submit_attempt_unknown",
    "post",
    "send",
    "consume",
}


def main() -> int:
    errors: list[str] = []
    if not MODULE.is_file():
        errors.append("missing typed connectivity OMS staging module")
    else:
        source = MODULE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            for module in modules:
                if any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORTS):
                    errors.append(f"{MODULE.relative_to(ROOT)}:{node.lineno}: forbidden import {module}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in FORBIDDEN_CALLS:
                    errors.append(
                        f"{MODULE.relative_to(ROOT)}:{node.lineno}: forbidden writer/submission call {node.func.attr}"
                    )

        for anchor in (
            "class ConnectivityOmsStager",
            'current.intent.strategy_id != CONNECTIVITY_CANARY_STRATEGY_ID',
            'current.intent.quantity != Decimal("1")',
            'current.intent.side.value != "BUY"',
            'current.intent.order_type.value != "LIMIT"',
            "decision.status is not RiskDecisionStatus.APPROVED",
            "decision.intent_fingerprint != fingerprint",
            "market.symbol != current.intent.symbol",
            "decision.market_fingerprint != market_fp",
            "safety.version != decision.safety_state_version",
            "safety.kill_switch_active",
            "safety.circuit_active",
            '_EVENT_TYPE = "CONNECTIVITY_EXTERNAL_HANDOFF_AUTHORIZED"',
            "status=OrderStatus.SUBMITTING",
            "risk_decision_id=decision.decision_id",
            '"SUBMITTING without durable connectivity handoff event is forbidden"',
        ):
            if anchor not in source:
                errors.append(f"connectivity OMS staging anchor missing: {anchor}")
        for forbidden in (
            "health_bypass",
            "PaperCanaryPermitRegistry",
            "AlpacaPaperSingleShotWriter",
            "R6_EXTERNAL_PAPER_WRITE",
        ):
            if forbidden in source:
                errors.append(f"connectivity OMS staging contains forbidden authority surface: {forbidden}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 typed connectivity OMS staging boundary: PASS "
        "(reserved CONNECTIVITY_CANARY only; fresh Safety+market binding; durable handoff before SUBMITTING; "
        "no Health bypass, submission registry, broker, writer, network or POST)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
