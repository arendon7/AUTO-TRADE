from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BROKER_DIR = ROOT / "src/autotrade/brokers"
R6_PREFIX = "alpaca_paper_"

PAPER_HOST = "paper-api.alpaca.markets"
LIVE_HOST = "api.alpaca.markets"
PAPER_TRADE_UPDATES_URL = "wss://paper-api.alpaca.markets/stream"
LIVE_TRADE_UPDATES_URL = "wss://api.alpaca.markets/stream"

ATTESTATION_FILE = "alpaca_paper_gateway.py"
MARKET_DATA_FILE = "alpaca_paper_market_data.py"
RECONCILIATION_FILE = "alpaca_paper_reconciliation_gateway.py"
WRITER_FILE = "alpaca_paper_writer.py"
TRADE_UPDATES_FILE = "alpaca_paper_trade_updates_transport.py"

HTTP_METHOD_BY_FILE = {
    ATTESTATION_FILE: "GET",
    MARKET_DATA_FILE: "GET",
    RECONCILIATION_FILE: "GET",
    WRITER_FILE: "POST",
}
ALLOWED_LIVE_DENY_LITERALS = {
    ATTESTATION_FILE: frozenset({LIVE_HOST}),
    TRADE_UPDATES_FILE: frozenset({LIVE_TRADE_UPDATES_URL}),
}
ALLOWED_LIVE_IDENTIFIERS = frozenset(
    {"ALPACA_LIVE_TRADING_HOST", "ALPACA_LIVE_TRADE_UPDATES_URL"}
)
FORBIDDEN_AUTH_IMPORT_PREFIXES = (
    "openai",
    "anthropic",
    "autotrade.research",
)

REQUIRED_ROLE_ANCHORS = {
    ATTESTATION_FILE: (
        'ALPACA_PAPER_TRADING_HOST = "paper-api.alpaca.markets"',
        'ALPACA_LIVE_TRADING_HOST = "api.alpaca.markets"',
        'ALPACA_PAPER_ACCOUNT_PATH = "/v2/account"',
        "parsed.hostname != self.allowed_host",
        "parsed.hostname == ALPACA_LIVE_TRADING_HOST",
        "ProxyHandler({})",
        "_RejectRedirectHandler()",
    ),
    MARKET_DATA_FILE: (
        'ALPACA_MARKET_DATA_HOST = "data.alpaca.markets"',
        'ALPACA_BASIC_EQUITY_FEED = "iex"',
        'ALPACA_MARKET_DATA_CURRENCY = "USD"',
        'method="GET"',
        "ProxyHandler({})",
        "_RejectMarketDataRedirectHandler()",
        'parsed.query != f"feed={ALPACA_BASIC_EQUITY_FEED}&currency={ALPACA_MARKET_DATA_CURRENCY}"',
        "MarketSnapshot observed_at must be oldest component timestamp",
    ),
    RECONCILIATION_FILE: (
        "parsed.hostname != ALPACA_PAPER_TRADING_HOST",
        "parsed.hostname == ALPACA_LIVE_TRADING_HOST",
        'method="GET"',
        "ProxyHandler({})",
        "_RejectRedirectHandler()",
        "/v2/orders:by_client_order_id",
        "nested=true",
    ),
    WRITER_FILE: (
        "_validate_writer_base_url(self._config.base_url)",
        "parsed.hostname != ALPACA_PAPER_TRADING_HOST",
        "parsed.hostname == ALPACA_LIVE_TRADING_HOST",
        "parsed.path != PAPER_ORDER_PATH",
        'method="POST"',
        "final_guard: PaperFinalWriteGuard",
        "PaperFinalWritePhase.PRE_CONSUME",
        "PaperFinalWritePhase.PRE_IO",
        "previous_attestation=pre_consume_guard",
        "ProxyHandler({})",
        "_RejectRedirectHandler()",
    ),
    TRADE_UPDATES_FILE: (
        'ALPACA_PAPER_TRADE_UPDATES_URL = "wss://paper-api.alpaca.markets/stream"',
        'ALPACA_LIVE_TRADE_UPDATES_URL = "wss://api.alpaca.markets/stream"',
        '_TRADE_UPDATES_STREAM = "trade_updates"',
        "endpoint != ALPACA_PAPER_TRADE_UPDATES_URL",
        "endpoint == ALPACA_LIVE_TRADE_UPDATES_URL",
        "proxy=None",
        "compression=None",
    ),
}

CORE_WORKFLOW = ROOT / ".github/workflows/core-tests.yml"
R6_WORKFLOW = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_live_deny_boundary.py"
SELF_TEST = "tests/test_r6_live_deny_boundary.py"


def main() -> int:
    files = sorted(
        path for path in BROKER_DIR.glob(f"{R6_PREFIX}*.py") if path.is_file()
    )
    errors: list[str] = []
    if not files:
        errors.append("R6 LIVE-deny checker found no alpaca_paper_* modules")
    for path in files:
        errors.extend(_scan(path))
    errors.extend(_validate_ci_wiring())

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)

    print(
        "AUTO-TRADE R6 permanent LIVE-deny boundary: PASS "
        "(exact PAPER methods/endpoints/I-O budgets; AI/research authority denied)"
    )
    return 0


def _scan(path: Path) -> list[str]:
    return [*_scan_ast(path), *_validate_role_contract(path)]


def _scan_ast(path: Path) -> list[str]:
    errors: list[str] = []
    rel = _relative(path)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(rel))
    parents = _parent_map(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_auth_import(alias.name):
                    errors.append(
                        f"{rel}:{node.lineno}: AI/research authority import is forbidden: {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_auth_import(module):
                errors.append(
                    f"{rel}:{node.lineno}: AI/research authority import is forbidden: {module}"
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _is_live_authority_literal(node.value) and not _allowed_live_deny_literal(
                path.name, node.value
            ):
                errors.append(
                    f"{rel}:{node.lineno}: LIVE endpoint/host literal is forbidden outside explicit deny constants"
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if _forbidden_live_identifier(node.name):
                errors.append(
                    f"{rel}:{node.lineno}: LIVE promotion/authority identifier is forbidden: {node.name}"
                )
        elif isinstance(node, ast.arg):
            if _forbidden_live_identifier(node.arg):
                errors.append(
                    f"{rel}:{node.lineno}: LIVE authority argument is forbidden: {node.arg}"
                )
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store) and _forbidden_live_identifier(node.id):
                errors.append(
                    f"{rel}:{node.lineno}: LIVE promotion/authority binding is forbidden: {node.id}"
                )
            if (
                isinstance(node.ctx, ast.Load)
                and node.id in ALLOWED_LIVE_IDENTIFIERS
                and not _live_deny_symbol_used_only_for_comparison(node, parents)
            ):
                errors.append(
                    f"{rel}:{node.lineno}: LIVE deny symbol may only be read in a comparison, never to build authority"
                )
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name == "Request":
                expected = HTTP_METHOD_BY_FILE.get(path.name)
                if expected is None:
                    errors.append(
                        f"{rel}:{node.lineno}: urllib Request authority is forbidden in this R6 module"
                    )
                else:
                    method = _literal_keyword(node, "method")
                    if method != expected:
                        errors.append(
                            f"{rel}:{node.lineno}: Request method must be literal {expected}, got {method!r}"
                        )
            if name == "write":
                if not (path.name == WRITER_FILE and _is_exact_writer_transport_write(node)):
                    errors.append(
                        f"{rel}:{node.lineno}: direct transport write is forbidden outside the one audited writer call"
                    )
            if name == "websocket_connect" and path.name != TRADE_UPDATES_FILE:
                errors.append(
                    f"{rel}:{node.lineno}: websocket_connect authority is forbidden outside trade_updates transport"
                )
            if _is_named_socket_send(node) and path.name != TRADE_UPDATES_FILE:
                errors.append(
                    f"{rel}:{node.lineno}: socket.send authority is forbidden outside trade_updates control handshake"
                )

    return errors


def _validate_role_contract(path: Path) -> list[str]:
    errors: list[str] = []
    rel = _relative(path)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(rel))

    request_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node.func) == "Request"
    ]
    opener_calls = [
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and _is_opener_open(node)
    ]
    writer_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_exact_writer_transport_write(node)
    ]
    websocket_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node.func) == "websocket_connect"
    ]
    connector_open_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_connector_open(node)
    ]
    control_sends = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_named_socket_send(node)
    ]

    expected_method = HTTP_METHOD_BY_FILE.get(path.name)
    if expected_method is not None:
        if len(request_calls) != 1:
            errors.append(
                f"{rel}: audited HTTP role must contain exactly one urllib Request constructor, found {len(request_calls)}"
            )
        if len(opener_calls) != 1:
            errors.append(
                f"{rel}: audited HTTP role must contain exactly one low-level _opener.open call, found {len(opener_calls)}"
            )
    elif opener_calls:
        errors.append(f"{rel}: low-level _opener.open is forbidden in this R6 module")

    if path.name == WRITER_FILE:
        if len(writer_calls) != 1:
            errors.append(
                f"{rel}: writer must contain exactly one self._transport.write(request) call"
            )
    elif writer_calls:
        errors.append(f"{rel}: writer transport call escaped the certified writer module")

    if path.name == TRADE_UPDATES_FILE:
        if len(websocket_calls) != 1:
            errors.append(
                f"{rel}: trade_updates must contain exactly one websocket_connect call, found {len(websocket_calls)}"
            )
        if len(connector_open_calls) != 1:
            errors.append(
                f"{rel}: trade_updates must contain exactly one injected connector.open call, found {len(connector_open_calls)}"
            )
        if len(control_sends) != 2:
            errors.append(
                f"{rel}: trade_updates must contain exactly two socket.send control frames, found {len(control_sends)}"
            )
    else:
        if websocket_calls:
            errors.append(f"{rel}: websocket_connect call is forbidden outside trade_updates")
        if control_sends:
            errors.append(f"{rel}: socket.send call is forbidden outside trade_updates")

    for anchor in REQUIRED_ROLE_ANCHORS.get(path.name, ()):
        if anchor not in source:
            errors.append(f"{rel}: required PAPER/LIVE-deny anchor missing: {anchor}")

    return errors


def _validate_ci_wiring() -> list[str]:
    errors: list[str] = []
    for workflow, label in (
        (CORE_WORKFLOW, "Core Safety"),
        (R6_WORKFLOW, "R6 Authority"),
    ):
        if not workflow.is_file():
            errors.append(f"{label}: required workflow is missing: {workflow.relative_to(ROOT)}")
            continue
        text = workflow.read_text(encoding="utf-8")
        if SELF_COMMAND not in text:
            errors.append(f"{label}: permanent R6 LIVE-deny checker is not wired into CI")
    if R6_WORKFLOW.is_file():
        text = R6_WORKFLOW.read_text(encoding="utf-8")
        if SELF_TEST not in text:
            errors.append("R6 Authority: adversarial LIVE-deny checker tests are not wired into CI")
    return errors


def _relative(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _forbidden_auth_import(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_AUTH_IMPORT_PREFIXES
    )


def _is_live_authority_literal(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        normalized == LIVE_HOST
        or normalized.startswith(f"https://{LIVE_HOST}")
        or normalized.startswith(f"wss://{LIVE_HOST}")
        or f"//{LIVE_HOST}" in normalized
    )


def _allowed_live_deny_literal(filename: str, value: str) -> bool:
    return value in ALLOWED_LIVE_DENY_LITERALS.get(filename, frozenset())


def _forbidden_live_identifier(name: str) -> bool:
    return "live" in name.lower() and name not in ALLOWED_LIVE_IDENTIFIERS


def _live_deny_symbol_used_only_for_comparison(
    node: ast.Name,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    return isinstance(parents.get(node), ast.Compare)


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _literal_keyword(node: ast.Call, name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                return keyword.value.value
            return None
    return None


def _is_opener_open(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "open"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "_opener"
    )


def _is_connector_open(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "open"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "_connector"
    )


def _is_exact_writer_transport_write(node: ast.Call) -> bool:
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "write"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "_transport"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "self"
    ):
        return False
    return (
        len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "request"
        and not node.keywords
    )


def _is_named_socket_send(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "send"
        and isinstance(func.value, ast.Name)
        and func.value.id == "socket"
    )


if __name__ == "__main__":
    raise SystemExit(main())
