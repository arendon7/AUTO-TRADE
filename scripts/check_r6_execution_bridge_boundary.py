from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BROKER_DIR = ROOT / "src/autotrade/brokers"
BRIDGE = BROKER_DIR / "alpaca_paper_execution_bridge.py"
CRYPTO_BRIDGE = BROKER_DIR / "alpaca_paper_crypto_execution_bridge.py"
CRYPTO_PROTECTION_BRIDGE = BROKER_DIR / "alpaca_paper_crypto_protection_execution_bridge.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_execution_bridge_boundary.py"
SELF_TEST = "tests/test_r6_execution_bridge_boundary.py"

FORBIDDEN_BRIDGE_IMPORTS = (
    "alpaca_paper_writer",
    "alpaca_paper_crypto_writer",
    "alpaca_paper_crypto_pre_io",
    "urllib",
    "http.client",
    "requests",
    "socket",
    "websockets",
    "openai",
    "anthropic",
    "autotrade.research",
)
FORBIDDEN_BRIDGE_CALLS = {
    "record_operator_approval",
    "submit_once",
    "write",
    "send",
    "post",
    "urlopen",
    "Request",
}
REQUIRED_BRIDGE_ANCHORS = (
    "class PaperCanaryExecutionBridge:",
    "package: PreparedPaperCanaryPackage",
    "operator_decision: PaperOperatorDecision",
    "operator_registry: SQLitePaperOperatorDecisionRegistry",
    "PaperOperatorDecisionContext.from_prepared_package(package)",
    "operator_registry.get(expected_context.preparation_hash)",
    "operator_registry.consume(",
    "self._oms.stage_external_submission(",
    "risk_decision_fingerprint(risk_decision) != package.risk_decision_fingerprint",
    "package.risk_decision_safety_state_version",
    "package.market_fingerprint",
    "consumed.status is not PaperOperatorDecisionStatus.CONSUMED",
)
REQUIRED_CRYPTO_BRIDGE_ANCHORS = (
    "class CryptoPaperExecutionBridge:",
    "package: PreparedCryptoPaperCanaryPackage",
    "operator_decision: CryptoOperatorDecision",
    "operator_registry: SQLiteCryptoOperatorDecisionRegistry",
    "checkpoint: CryptoExecutionAttemptCheckpoint",
    "CryptoOperatorDecisionContext.from_prepared_package(",
    "checkpoint.package_hash != package.package_hash",
    "checkpoint.preparation_hash != operator_decision.context.preparation_hash",
    "checkpoint.operator_decision_hash != operator_decision.decision_hash",
    "operator_registry.get(expected_context.preparation_hash)",
    "operator_registry.consume(",
    "self._oms.stage_external_submission(",
    "risk_decision_fingerprint(risk_decision) != package.risk_decision_fingerprint",
    "consumed.status is not CryptoOperatorDecisionStatus.CONSUMED",
    "consume_instant > stage_instant",
    "crypto_execution_handoff_id(",
)
REQUIRED_CRYPTO_PROTECTION_BRIDGE_ANCHORS = (
    "class CryptoProtectionExecutionBridge:",
    "package: PreparedCryptoProtectionPackage",
    "operator_decision: CryptoProtectionOperatorDecision",
    "operator_registry: SQLiteCryptoProtectionOperatorDecisionRegistry",
    "checkpoint: CryptoProtectionExecutionAttemptCheckpoint",
    "CryptoProtectionOperatorDecisionContext.from_prepared_package(",
    "checkpoint.package_hash != package.package_hash",
    "checkpoint.operator_decision_hash != operator_decision.decision_hash",
    "checkpoint.lifecycle_id != package.lifecycle_id",
    "checkpoint.order_id != package.order_id",
    "checkpoint.client_order_id != package.client_order_id",
    "operator_registry.get(expected_context.preparation_hash)",
    "operator_registry.consume(",
    "self._oms.stage_external_submission(",
    "risk_decision_fingerprint(risk_decision) != package.risk_decision_fingerprint",
    "risk_decision.risk_reducing is not True",
    "consumed.status is not CryptoProtectionOperatorDecisionStatus.CONSUMED",
    "consume_instant > stage_instant",
    "crypto_protection_execution_handoff_id(",
)


def main() -> int:
    errors: list[str] = []
    if not BRIDGE.is_file():
        errors.append("R6 execution bridge source missing")
    else:
        source = BRIDGE.read_text(encoding="utf-8")
        for anchor in REQUIRED_BRIDGE_ANCHORS:
            if anchor not in source:
                errors.append(f"execution bridge anchor missing: {anchor}")
        errors.extend(_scan_bridge(source, BRIDGE))
        errors.extend(_validate_bridge_ordering(source))

    if not CRYPTO_BRIDGE.is_file():
        errors.append("R6 crypto execution bridge source missing")
    else:
        source = CRYPTO_BRIDGE.read_text(encoding="utf-8")
        for anchor in REQUIRED_CRYPTO_BRIDGE_ANCHORS:
            if anchor not in source:
                errors.append(f"crypto execution bridge anchor missing: {anchor}")
        errors.extend(_scan_bridge(source, CRYPTO_BRIDGE))
        errors.extend(_validate_crypto_bridge_ordering(source))

    if not CRYPTO_PROTECTION_BRIDGE.is_file():
        errors.append("R6 crypto protection execution bridge source missing")
    else:
        source = CRYPTO_PROTECTION_BRIDGE.read_text(encoding="utf-8")
        for anchor in REQUIRED_CRYPTO_PROTECTION_BRIDGE_ANCHORS:
            if anchor not in source:
                errors.append(f"crypto protection execution bridge anchor missing: {anchor}")
        errors.extend(_scan_bridge(source, CRYPTO_PROTECTION_BRIDGE))
        errors.extend(_validate_crypto_protection_bridge_ordering(source))

    # No other R6 broker production module may transition OMS into SUBMITTING.
    approved_bridges = {
        BRIDGE.resolve(),
        CRYPTO_BRIDGE.resolve(),
        CRYPTO_PROTECTION_BRIDGE.resolve(),
    }
    for path in sorted(BROKER_DIR.glob("alpaca_paper_*.py")):
        if path.resolve() in approved_bridges:
            continue
        for lineno, call in _named_calls(path, "stage_external_submission"):
            errors.append(
                f"{_relative(path)}:{lineno}: OMS external staging is execution-bridge-only ({call})"
            )

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: execution bridge checker is not wired into permanent CI")
    if not R6.is_file() or SELF_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: execution bridge adversarial tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 execution bridge boundary: PASS "
        "(equity + checkpoint-bound crypto decisions consumed before bridge-only OMS staging; no network/AI authority)"
    )
    return 0


def _scan_bridge(source: str, path: Path) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]
    rel = _relative(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules = [base]
            modules.extend(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
            )
        else:
            modules = []
        for module in modules:
            if any(fragment in module for fragment in FORBIDDEN_BRIDGE_IMPORTS):
                errors.append(f"{rel}:{node.lineno}: forbidden bridge import {module}")
        if isinstance(node, ast.Call):
            call = _call_name(node.func)
            if call in FORBIDDEN_BRIDGE_CALLS:
                errors.append(f"{rel}:{node.lineno}: forbidden bridge call {call}")
    return errors


def _validate_bridge_ordering(source: str) -> list[str]:
    errors: list[str] = []
    get_pos = source.find("durable = operator_registry.get(")
    consume_pos = source.find("consumed = operator_registry.consume(")
    stage_pos = source.find("staged, handoff = self._oms.stage_external_submission(")
    if get_pos < 0 or consume_pos < 0 or stage_pos < 0 or not get_pos < consume_pos < stage_pos:
        errors.append("execution bridge must verify durable decision, consume it, then stage OMS")
    if "network_write_authorized is not False" not in source:
        errors.append("execution bridge must reject any package claiming network authority")
    if 'next_action != "OPERATOR_DECISION_REQUIRED"' not in source:
        errors.append("execution bridge must require OPERATOR_DECISION_REQUIRED package")
    return errors


def _validate_crypto_bridge_ordering(source: str) -> list[str]:
    errors: list[str] = []
    checkpoint_pos = source.find("checkpoint.package_hash != package.package_hash")
    get_pos = source.find("durable = operator_registry.get(")
    consume_pos = source.find("consumed = operator_registry.consume(")
    handoff_pos = source.find("handoff_id = crypto_execution_handoff_id(")
    stage_pos = source.find("staged, handoff = self._oms.stage_external_submission(")
    positions = (checkpoint_pos, get_pos, consume_pos, handoff_pos, stage_pos)
    if any(pos < 0 for pos in positions) or tuple(sorted(positions)) != positions:
        errors.append(
            "crypto execution bridge must verify checkpoint, verify durable decision, consume it, bind handoff, then stage OMS"
        )
    if "network_write_authorized is not False" not in source:
        errors.append("crypto execution bridge must reject any package claiming network authority")
    if 'next_action != "OPERATOR_DECISION_REQUIRED"' not in source:
        errors.append("crypto execution bridge must require OPERATOR_DECISION_REQUIRED package")
    if "consume_instant > stage_instant" not in source:
        errors.append("crypto execution bridge must prevent consume-after-stage time travel")
    return errors


def _validate_crypto_protection_bridge_ordering(source: str) -> list[str]:
    errors: list[str] = []
    checkpoint_pos = source.find("checkpoint.package_hash != package.package_hash")
    get_pos = source.find("durable = operator_registry.get(")
    consume_pos = source.find("consumed = operator_registry.consume(")
    handoff_pos = source.find("handoff_id = crypto_protection_execution_handoff_id(")
    stage_pos = source.find("staged, handoff = self._oms.stage_external_submission(")
    positions = (checkpoint_pos, get_pos, consume_pos, handoff_pos, stage_pos)
    if any(pos < 0 for pos in positions) or tuple(sorted(positions)) != positions:
        errors.append(
            "crypto protection execution bridge must verify checkpoint, verify durable decision, consume it, bind handoff, then stage OMS"
        )
    if "network_write_authorized is not False" not in source:
        errors.append("crypto protection execution bridge must reject any package claiming network authority")
    if 'next_action != "OPERATOR_DECISION_REQUIRED"' not in source:
        errors.append("crypto protection execution bridge must require OPERATOR_DECISION_REQUIRED package")
    if "risk_reducing is not True" not in source:
        errors.append("crypto protection execution bridge must require risk-reducing authority")
    if "consume_instant > stage_instant" not in source:
        errors.append("crypto protection execution bridge must prevent consume-after-stage time travel")
    return errors


def _named_calls(path: Path, name: str) -> list[tuple[int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError):
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) == name:
            found.append((node.lineno, name))
    return found


def _relative(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
