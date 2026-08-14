from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BROKERS = ROOT / "src/autotrade/brokers"
BRIDGE = BROKERS / "alpaca_paper_crypto_protection_execution_bridge.py"
WORKFLOW = ROOT / ".github/workflows/r6-crypto-protection.yml"
MAC_SURFACES = (
    ROOT / "scripts/mac_dashboard.py",
    ROOT / "scripts/mac_crypto_dashboard.py",
    ROOT / "scripts/mac_crypto_paper_rehearsal.py",
    ROOT / "web/mac_multi_asset.html",
    ROOT / "web/mac_crypto_dashboard.html",
    ROOT / "ABRIR_AUTO_TRADE.command",
    ROOT / "ABRIR_CRYPTO_PAPER.command",
)
SELF_COMMAND = "python scripts/check_r6_crypto_protection_execution_bridge_boundary.py"
SELF_TEST = "tests/test_r6_paper_crypto_protection_execution_bridge.py"
NETWORK_ROOTS = {"http", "urllib", "socket", "ssl", "requests", "httpx", "aiohttp", "websockets"}
FORBIDDEN_IMPORT_FRAGMENTS = (
    "alpaca_paper_crypto_writer",
    "alpaca_paper_crypto_pre_io",
    "alpaca_paper_writer",
    "alpaca_paper_bracket",
    "alpaca_paper_final_guard",
    "connectivity_workspace_post",
    "connectivity_final_freshness",
)
FORBIDDEN_CALLS = {
    "record_operator_approval",
    "submit_once",
    "post",
    "write",
    "send",
    "urlopen",
    "Request",
}
REQUIRED_ANCHORS = (
    "class CryptoProtectionExecutionBridge:",
    "package: PreparedCryptoProtectionPackage",
    "operator_decision: CryptoProtectionOperatorDecision",
    "operator_registry: SQLiteCryptoProtectionOperatorDecisionRegistry",
    "checkpoint: CryptoProtectionExecutionAttemptCheckpoint",
    "risk_decision: RiskDecision",
    "market: MarketSnapshot",
    "network_write_authorized is not False",
    'next_action != "OPERATOR_DECISION_REQUIRED"',
    "package.risk_reducing is not True",
    "checkpoint.package_hash != package.package_hash",
    "checkpoint.operator_decision_hash != operator_decision.decision_hash",
    "checkpoint.lifecycle_id != package.lifecycle_id",
    "checkpoint.order_id != package.order_id",
    "checkpoint.client_order_id != package.client_order_id",
    "risk_decision_fingerprint(risk_decision) != package.risk_decision_fingerprint",
    "risk_decision.risk_reducing is not True",
    "market_fingerprint(market) != package.market_fingerprint",
    "CryptoProtectionOperatorDecisionContext.from_prepared_package(",
    "operator_registry.get(expected_context.preparation_hash)",
    "operator_registry.consume(",
    "consumed.status is not CryptoProtectionOperatorDecisionStatus.CONSUMED",
    "handoff_id = crypto_protection_execution_handoff_id(",
    "self._oms.stage_external_submission(",
    "consume_instant > stage_instant",
    '"R6_CRYPTO_PROTECTION_EXECUTION_HANDOFF"',
)


def fail(message: str) -> None:
    print(f"crypto protection execution bridge boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if not BRIDGE.is_file():
        fail(f"missing bridge: {BRIDGE.relative_to(ROOT)}")
    source = BRIDGE.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(BRIDGE))
    except SyntaxError as exc:
        fail(f"bridge syntax error: {exc}")

    for anchor in REQUIRED_ANCHORS:
        if anchor not in source:
            fail(f"missing contract anchor: {anchor}")

    imports = _imports(tree)
    roots = {module.split(".", 1)[0] for module in imports if module}
    forbidden_network = roots & NETWORK_ROOTS
    if forbidden_network:
        fail(f"bridge imports direct network stack: {sorted(forbidden_network)}")
    for module in imports:
        if any(fragment in module for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
            fail(f"bridge imports forbidden write/equity authority: {module}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                fail(f"bridge contains forbidden call {name} at line {node.lineno}")

    for token in (
        "AlpacaPaperCredentials",
        "APCA-API-KEY-ID",
        "APCA-API-SECRET-KEY",
        "HttpsAlpacaPaperCryptoWriteTransport",
        "AlpacaPaperCryptoWriter",
        "FinalGuardedCryptoEntryTransport",
        "api.alpaca.markets",
        "paper-api.alpaca.markets",
        "/v2/orders",
    ):
        if token in source:
            fail(f"bridge contains forbidden credential/network token: {token}")

    checkpoint_pos = source.find("checkpoint.package_hash != package.package_hash")
    durable_pos = source.find("durable = operator_registry.get(")
    consume_pos = source.find("consumed = operator_registry.consume(")
    handoff_pos = source.find("handoff_id = crypto_protection_execution_handoff_id(")
    stage_pos = source.find("staged, handoff = self._oms.stage_external_submission(")
    positions = (checkpoint_pos, durable_pos, consume_pos, handoff_pos, stage_pos)
    if any(pos < 0 for pos in positions) or tuple(sorted(positions)) != positions:
        fail("authority order must be checkpoint -> durable decision -> consume -> handoff -> OMS stage")

    stage_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node.func) == "stage_external_submission"
    ]
    if len(stage_calls) != 1:
        fail(f"bridge must contain exactly one OMS stage call, found {len(stage_calls)}")

    for path in sorted(BROKERS.glob("alpaca_paper_crypto_protection_*.py")):
        if path == BRIDGE:
            continue
        other = path.read_text(encoding="utf-8")
        if "stage_external_submission(" in other:
            fail(f"protection OMS staging leaked outside bridge: {path.name}")

    for path in MAC_SURFACES:
        if path.is_file() and "alpaca_paper_crypto_protection_execution_bridge" in path.read_text(encoding="utf-8"):
            fail(f"Mac/user-facing surface imports protection execution bridge: {path.name}")

    if WORKFLOW.is_file():
        workflow = WORKFLOW.read_text(encoding="utf-8")
        if SELF_COMMAND not in workflow:
            fail("protection workflow does not run bridge checker")
        if SELF_TEST not in workflow:
            fail("protection workflow does not run bridge tests")

    print(
        "crypto protection execution bridge boundary: PASS — durable PRE_CONSUME checkpoint -> "
        "same-attempt human consumption -> deterministic OMS SUBMITTING handoff; no credentials/network/writer; "
        "Mac remains disconnected"
    )
    return 0


def _imports(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
