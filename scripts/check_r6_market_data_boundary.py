from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "src/autotrade/brokers/alpaca_paper_market_data.py"
EVIDENCE = ROOT / "src/autotrade/brokers/alpaca_paper_market_evidence.py"
CLI = ROOT / "scripts/r6_external_paper_market_preflight.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_market_data_boundary.py"
SELF_TEST = "tests/test_r6_market_data_boundary.py"
FUNCTIONAL_TESTS = (
    "tests/test_r6_paper_market_data.py",
    "tests/test_r6_paper_market_evidence.py",
    "tests/test_r6_market_preflight_cli.py",
)

FORBIDDEN_AUTHORITY_IMPORTS = (
    "autotrade.research",
    "autotrade.oms",
    "autotrade.safety",
    "alpaca_paper_writer",
    "alpaca_paper_execution_bridge",
    "alpaca_paper_reconciliation_gateway",
    "alpaca_paper_trade_updates_transport",
    "openai",
    "anthropic",
)


def main() -> int:
    errors: list[str] = []
    for path, label in ((GATEWAY, "market gateway"), (EVIDENCE, "market evidence"), (CLI, "market CLI")):
        if not path.is_file():
            errors.append(f"required {label} is missing: {_relative(path)}")

    if GATEWAY.is_file():
        source = GATEWAY.read_text(encoding="utf-8")
        errors.extend(_forbidden_imports(source, GATEWAY))
        required = (
            'ALPACA_MARKET_DATA_HOST = "data.alpaca.markets"',
            'ALPACA_BASIC_EQUITY_FEED = "iex"',
            'ALPACA_MARKET_DATA_CURRENCY = "USD"',
            'enabled: bool = False',
            'method="GET"',
            'ProxyHandler({})',
            '_RejectMarketDataRedirectHandler()',
            'parsed.query != f"feed={ALPACA_BASIC_EQUITY_FEED}&currency={ALPACA_MARKET_DATA_CURRENCY}"',
            'self._transport.read(request)',
            'observed_at=min(quote_time, trade_time)',
        )
        for anchor in required:
            if anchor not in source:
                errors.append(f"market gateway safety anchor missing: {anchor}")
        if source.count("Request(") != 1:
            errors.append("market gateway must contain exactly one urllib Request constructor")
        if source.count("self._opener.open(") != 1:
            errors.append("market gateway must contain exactly one low-level HTTP open")
        for forbidden in (
            'method="POST"',
            "method='POST'",
            "paper-api.alpaca.markets",
            "/v2/orders",
            "socket.send(",
            "websocket_connect",
        ):
            if forbidden in source:
                errors.append(f"market gateway contains forbidden trading/write surface: {forbidden}")

    if EVIDENCE.is_file():
        source = EVIDENCE.read_text(encoding="utf-8")
        errors.extend(_forbidden_imports(source, EVIDENCE))
        required = (
            '"network_method": "GET"',
            '"credentials_persisted": False',
            '"broker_write_authorized": False',
            '"external_order_submitted": False',
            '"capital_authority": "NONE"',
            '"profitability_claim": False',
            '"live_trading": "BLOCKED"',
            'account.get("credential_reference") != credentials.credential_reference',
            '_write_json_idempotent(self.path, payload)',
        )
        for anchor in required:
            if anchor not in source:
                errors.append(f"market evidence safety anchor missing: {anchor}")
        for forbidden in ("Request(", "_opener.open(", "connect_and_listen", "submit_once"):
            if forbidden in source:
                errors.append(f"market evidence must remain network/execution inert: {forbidden}")

    if CLI.is_file():
        source = CLI.read_text(encoding="utf-8")
        errors.extend(_forbidden_imports(source, CLI))
        required = (
            '"--allow-paper-market-read"',
            'os.environ.get(_KEY_ENV)',
            'os.environ.get(_SECRET_ENV)',
            'os.environ.get(_WRITE_ENV) == "ENABLED"',
            'AlpacaPaperMarketDataConfig(enabled=True)',
            'gateway.attest_snapshot(',
            'PaperMarketEvidenceStore(workspace).write(',
            '"broker_write_authorized": False',
            '"external_order_submitted": False',
            '"live_trading": "BLOCKED"',
        )
        for anchor in required:
            if anchor not in source:
                errors.append(f"market CLI safety anchor missing: {anchor}")
        if source.count("gateway.attest_snapshot(") != 1:
            errors.append("market CLI must contain exactly one market snapshot network call")
        for forbidden in (
            "--key-id",
            "--secret",
            "r6_execute_paper_canary",
            "AlpacaPaperSingleShotWriter",
            "/v2/orders",
            "stage_external_submission",
            "record_operator_approval",
        ):
            if forbidden in source:
                errors.append(f"market CLI contains forbidden authority surface: {forbidden}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: R6 market-data checker is not wired into CI")
    if R6.is_file():
        text = R6.read_text(encoding="utf-8")
        if SELF_TEST not in text:
            errors.append("R6 Authority: market-data adversarial checker test is not wired")
        for test in FUNCTIONAL_TESTS:
            if test not in text:
                errors.append(f"R6 Authority: market-data functional test is not wired: {test}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 equity market-data boundary: PASS "
        "(one explicit IEX GET; sanitized immutable evidence; no trading/write authority)"
    )
    return 0


def _forbidden_imports(source: str, path: Path) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{_relative(path)}: syntax error: {exc}"]
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules.append(base)
            modules.extend(f"{base}.{alias.name}" for alias in node.names)
        for module in modules:
            if any(fragment in module for fragment in FORBIDDEN_AUTHORITY_IMPORTS):
                errors.append(f"{_relative(path)}:{node.lineno}: forbidden authority import {module}")
    return errors


def _relative(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


if __name__ == "__main__":
    raise SystemExit(main())
