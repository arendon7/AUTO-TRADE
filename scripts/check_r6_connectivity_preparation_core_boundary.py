from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "src/autotrade/brokers/alpaca_paper_connectivity_gate.py"
BINDING = ROOT / "src/autotrade/connectivity_preparation_binding.py"
PREPARE = ROOT / "src/autotrade/brokers/alpaca_paper_connectivity_prepare.py"
CLI = ROOT / "scripts/r6_prepare_connectivity_candidate.py"

FORBIDDEN_IMPORTS = (
    "autotrade.research", "autotrade.health_bridge", "openai", "anthropic",
    "requests", "urllib", "websockets",
)
FORBIDDEN_TEXT = (
    "AlpacaPaperCredentials", "AlpacaPaperSingleShotWriter", "PaperCanaryExecutionBridge",
    "stage_external_submission", "submit_once", "record_operator_approval",
)


def main() -> int:
    errors: list[str] = []
    for path in (GATE, BINDING, PREPARE, CLI):
        if not path.is_file():
            errors.append(f"missing connectivity preparation surface: {path.relative_to(ROOT)}")
            continue
        errors.extend(_scan_imports(path))

    if GATE.is_file():
        source = GATE.read_text(encoding="utf-8")
        for anchor in (
            "health_allows_new_exposure is not False",
            "must not be represented as Strategy Health approval",
            "CONNECTIVITY_CANARY_STRATEGY_ID",
            'order.intent.quantity != Decimal("1")',
            'order.intent.side.value != "BUY"',
            'order.intent.order_type.value != "LIMIT"',
            "PaperSubmissionStatus.PREPARED",
            "context.prior_canary_submissions != 0",
        ):
            if anchor not in source:
                errors.append(f"connectivity gate anchor missing: {anchor}")
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in source:
                errors.append(f"connectivity gate contains forbidden authority: {forbidden}")

    if BINDING.is_file():
        source = BINDING.read_text(encoding="utf-8")
        for anchor in (
            '"purpose": "CONNECTIVITY_CANARY"',
            '"strategy_health_required": False',
            '"strategy_health_created": False',
            '"strategy_trading_authorized": False',
            '"operator_authority_created": False',
            '"external_post_authorized": False',
            '"live_trading": "BLOCKED"',
            "CONNECTIVITY_CANARY_PREPARED",
        ):
            if anchor not in source:
                errors.append(f"connectivity binding anchor missing: {anchor}")

    if PREPARE.is_file():
        source = PREPARE.read_text(encoding="utf-8")
        for anchor in (
            "ConnectivityCanaryGate(authority)",
            "health_allows_new_exposure=False",
            "SQLitePaperSubmissionRegistry",
            "SQLitePaperCanaryPermitRegistry",
            "SQLiteConnectivityPreparationBindingStore(runtime).record(binding)",
            "self._require_normal_operator_artifacts_absent()",
            'CONNECTIVITY_PREP_ARTIFACT = "connectivity_preparation.json"',
            '"operator_context_created": False',
            '"strategy_health_required": False',
            '"strategy_trading_authorized": False',
            '"operator_authority_created": False',
            '"external_post_authorized": False',
            '"next_action": "CONNECTIVITY_OPERATOR_BRIDGE_REQUIRED"',
        ):
            if anchor not in source:
                errors.append(f"connectivity preparation anchor missing: {anchor}")
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in source:
                errors.append(f"connectivity preparation contains forbidden authority: {forbidden}")
        tree = ast.parse(source, filename=str(PREPARE))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"post", "send", "write", "submit_once", "stage_external_submission"}:
                    errors.append(
                        f"connectivity preparation:{node.lineno}: forbidden network/execution call {node.func.attr}"
                    )

    if CLI.is_file():
        source = CLI.read_text(encoding="utf-8")
        for anchor in (
            'os.environ.get(_WRITE_ENV) == "ENABLED"',
            "os.environ.get(_KEY_ENV) or os.environ.get(_SECRET_ENV)",
            "PaperConnectivityPreparationBridge",
            '"network_used": False',
            '"credentials_used": False',
            '"operator_authority_created": False',
            '"external_post_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
        ):
            if anchor not in source:
                errors.append(f"connectivity preparation CLI anchor missing: {anchor}")
        for forbidden in ("--key", "--secret", "--execute", "submit_once"):
            if forbidden in source:
                errors.append(f"connectivity preparation CLI contains forbidden surface: {forbidden}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 connectivity preparation core boundary: PASS "
        "(health=false purpose gate; OMS remains VALIDATED; PREPARED+permit reuse; "
        "connectivity ledger binding; no credentials/network/operator/POST authority)"
    )
    return 0


def _scan_imports(path: Path) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        for module in modules:
            if any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORTS):
                errors.append(f"{path.relative_to(ROOT)}:{node.lineno}: forbidden import {module}")
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
