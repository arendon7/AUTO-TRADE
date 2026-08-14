from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BROKER_DIR = ROOT / "src/autotrade/brokers"
R6_PREFIX = "alpaca_paper_"
CURRENT_PHASE = "PAPER_MULTI_ASSET_SINGLE_SHOT_RECONCILIATION_AND_PROTECTION_STRUCTURAL_CERTIFICATION"

PAPER_HOST = "paper-api.alpaca.markets"
LIVE_HOST = "api.alpaca.markets"
MARKET_DATA_HOST = "data.alpaca.markets"
PAPER_TRADE_UPDATES_URL = "wss://paper-api.alpaca.markets/stream"
LIVE_TRADE_UPDATES_URL = "wss://api.alpaca.markets/stream"
ATTESTATION_FILE = "alpaca_paper_gateway.py"
FLAT_ACCOUNT_FILE = "alpaca_paper_flat_account.py"
MARKET_DATA_FILE = "alpaca_paper_market_data.py"
RECONCILIATION_FILE = "alpaca_paper_reconciliation_gateway.py"
WRITER_FILE = "alpaca_paper_writer.py"
TRADE_UPDATES_FILE = "alpaca_paper_trade_updates_transport.py"
CRYPTO_WRITER_FILE = "alpaca_paper_crypto_writer.py"
CRYPTO_RECONCILIATION_FILE = "alpaca_paper_crypto_reconciliation.py"
CRYPTO_PRE_IO_FILE = "alpaca_paper_crypto_pre_io.py"
APPROVED_NETWORK_FILES = frozenset(
    {
        ATTESTATION_FILE,
        FLAT_ACCOUNT_FILE,
        MARKET_DATA_FILE,
        RECONCILIATION_FILE,
        WRITER_FILE,
        TRADE_UPDATES_FILE,
        CRYPTO_WRITER_FILE,
    }
)

FORBIDDEN_IMPORT_PREFIXES = (
    "openai",
    "anthropic",
    "autotrade.research",
)
NETWORK_ROOTS = {
    "http",
    "httpx",
    "requests",
    "socket",
    "ssl",
    "urllib",
    "websocket",
    "websockets",
}
FORBIDDEN_EXTERNAL_CALLS = {
    "post",
    "send",
    "submit",
    "submit_order",
    "place_order",
    "create_order",
    "replace_order",
    "cancel_order",
}
FORBIDDEN_EXECUTION_SYMBOLS = {
    "OpenAI",
    "Anthropic",
    "ChatCompletion",
}


def main() -> int:
    files = sorted(
        path for path in BROKER_DIR.glob(f"{R6_PREFIX}*.py") if path.is_file()
    )
    errors: list[str] = []
    if not files:
        errors.append("R6 authority checker found no alpaca_paper_* modules")
    for path in files:
        errors.extend(_scan(path))

    errors.extend(_validate_network_roles())

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"AUTO-TRADE R6 PAPER authority boundary: PASS ({CURRENT_PHASE})")
    return 0


def _scan(path: Path) -> list[str]:
    errors: list[str] = []
    rel = path.relative_to(ROOT)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(rel))
    network_allowed = path.name in APPROVED_NETWORK_FILES
    writer_calls: list[ast.Call] = []
    crypto_writer_calls: list[ast.Call] = []
    crypto_pre_io_delegate_calls: list[ast.Call] = []
    control_sends: list[ast.Call] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_import(alias.name):
                    errors.append(
                        f"{rel}:{node.lineno}: forbidden authority import {alias.name}"
                    )
                if not network_allowed and _network_import(alias.name):
                    errors.append(
                        f"{rel}:{node.lineno}: networking is forbidden outside approved R6 network modules: {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_import(module):
                errors.append(
                    f"{rel}:{node.lineno}: forbidden authority import {module}"
                )
            if not network_allowed and _network_import(module):
                errors.append(
                    f"{rel}:{node.lineno}: networking is forbidden outside approved R6 network modules: {module}"
                )
            for alias in node.names:
                if alias.name in FORBIDDEN_EXECUTION_SYMBOLS:
                    errors.append(
                        f"{rel}:{node.lineno}: forbidden AI/execution symbol {alias.name}"
                    )
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            is_exact_control_send = (
                path.name == TRADE_UPDATES_FILE
                and name == "send"
                and _is_named_socket_send(node)
            )
            is_exact_crypto_post = (
                path.name == CRYPTO_WRITER_FILE
                and name == "post"
                and _is_crypto_transport_post(node)
            )
            is_exact_crypto_pre_io_delegate = (
                path.name == CRYPTO_PRE_IO_FILE
                and name == "post"
                and _is_crypto_pre_io_delegate_post(node)
            )
            if (
                name in FORBIDDEN_EXTERNAL_CALLS
                and not is_exact_control_send
                and not is_exact_crypto_post
                and not is_exact_crypto_pre_io_delegate
            ):
                errors.append(
                    f"{rel}:{node.lineno}: unaudited external write call {name} is forbidden"
                )
            if is_exact_crypto_post:
                crypto_writer_calls.append(node)
                if _inside_loop(tree, node):
                    errors.append(
                        f"{rel}:{node.lineno}: crypto PAPER transport POST cannot execute inside a loop"
                    )
            if is_exact_crypto_pre_io_delegate:
                crypto_pre_io_delegate_calls.append(node)
                if _inside_loop(tree, node):
                    errors.append(
                        f"{rel}:{node.lineno}: crypto PRE_IO delegated POST cannot execute inside a loop"
                    )
            if is_exact_control_send:
                control_sends.append(node)
                if _inside_loop(tree, node):
                    errors.append(
                        f"{rel}:{node.lineno}: trade_updates control send cannot execute inside a loop"
                    )
            if name == "write" and _is_transport_write(node):
                if path.name != WRITER_FILE:
                    errors.append(
                        f"{rel}:{node.lineno}: transport write is allowed only in {WRITER_FILE}"
                    )
                else:
                    writer_calls.append(node)
                    if _inside_loop(tree, node):
                        errors.append(
                            f"{rel}:{node.lineno}: PAPER transport write cannot execute inside a loop"
                        )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == LIVE_HOST and path.name != ATTESTATION_FILE:
                errors.append(
                    f"{rel}:{node.lineno}: LIVE Trading API host literal is forbidden"
                )
            if node.value.startswith("wss://"):
                allowed_wss_literal = (
                    path.name == TRADE_UPDATES_FILE
                    and node.value in {PAPER_TRADE_UPDATES_URL, LIVE_TRADE_UPDATES_URL}
                )
                if not allowed_wss_literal:
                    errors.append(
                        f"{rel}:{node.lineno}: websocket endpoint authority is forbidden outside exact PAPER trade_updates module"
                    )
            if node.value.startswith("https://") and not network_allowed:
                errors.append(
                    f"{rel}:{node.lineno}: endpoint literal forbidden outside approved R6 network modules"
                )

    if path.name == WRITER_FILE and len(writer_calls) > 1:
        errors.append(
            f"{rel}: audited PAPER writer may contain exactly one transport write call"
        )
    if path.name == CRYPTO_WRITER_FILE and len(crypto_writer_calls) != 1:
        errors.append(
            f"{rel}: audited crypto PAPER writer must contain exactly one transport POST call"
        )
    if path.name == CRYPTO_PRE_IO_FILE and len(crypto_pre_io_delegate_calls) != 2:
        errors.append(
            f"{rel}: crypto PRE_IO interlock must contain exactly two role-bound self._delegate.post call sites"
        )
    if path.name == TRADE_UPDATES_FILE and len(control_sends) != 2:
        errors.append(
            f"{rel}: PAPER trade_updates transport must contain exactly two socket control sends"
        )
    return errors


def _validate_network_roles() -> list[str]:
    errors: list[str] = []
    for filename in APPROVED_NETWORK_FILES:
        if not (BROKER_DIR / filename).is_file():
            errors.append(f"{filename}: approved R6 network module is missing")

    gateway = BROKER_DIR / ATTESTATION_FILE
    if gateway.is_file():
        text = gateway.read_text(encoding="utf-8")
        if f'ALPACA_PAPER_TRADING_HOST = "{PAPER_HOST}"' not in text:
            errors.append("gateway: exact PAPER host constant is missing")
        if f'ALPACA_LIVE_TRADING_HOST = "{LIVE_HOST}"' not in text:
            errors.append("gateway: explicit LIVE deny constant is missing")
        if text.count(f'"{LIVE_HOST}"') != 1:
            errors.append(
                "gateway: exact LIVE host literal must appear exactly once as the deny constant"
            )
        _require_disabled_get_surface(text, ATTESTATION_FILE, errors)
        if "ALPACA_PAPER_ACCOUNT_PATH = \"/v2/account\"" not in text:
            errors.append("gateway: exact /v2/account path constant is missing")

    flat_account = BROKER_DIR / FLAT_ACCOUNT_FILE
    if flat_account.is_file():
        text = flat_account.read_text(encoding="utf-8")
        for anchor in (
            'POSITIONS_PATH = "/v2/positions"',
            'ORDERS_PATH = "/v2/orders"',
            'ORDERS_QUERY = "status=open&limit=500&direction=asc&nested=true"',
            'if not self._config.enabled:',
            'request.method != "GET"',
            'self._transport.read(request)',
            'path=POSITIONS_PATH',
            'path=ORDERS_PATH',
            'query=ORDERS_QUERY',
            'position_count=len(position_payload)',
            'open_order_count=len(order_payload)',
        ):
            if anchor not in text:
                errors.append(f"flat account: required exact two-GET anchor missing: {anchor}")
        if text.count("self._read(") != 2:
            errors.append("flat account: attest_flatness must invoke exactly two controlled reads")
        if text.count("self._transport.read(request)") != 1:
            errors.append("flat account: exactly one transport read call site is required")
        if 'method="POST"' in text or "method='POST'" in text:
            errors.append("flat account: POST authority is forbidden")
        for forbidden in (
            "submit_once",
            "stage_external_submission",
            "AlpacaPaperSingleShotWriter",
            "alpaca_paper_writer",
            "alpaca_paper_execution_bridge",
        ):
            if forbidden in text:
                errors.append(f"flat account: execution authority is forbidden: {forbidden}")

    market_data = BROKER_DIR / MARKET_DATA_FILE
    if market_data.is_file():
        text = market_data.read_text(encoding="utf-8")
        if f'ALPACA_MARKET_DATA_HOST = "{MARKET_DATA_HOST}"' not in text:
            errors.append("market data: exact data.alpaca.markets host constant is missing")
        for anchor in (
            'ALPACA_BASIC_EQUITY_FEED = "iex"',
            'ALPACA_MARKET_DATA_CURRENCY = "USD"',
            'enabled: bool = False',
            'method="GET"',
            'ProxyHandler({})',
            '_RejectMarketDataRedirectHandler()',
            '?feed={self._config.feed}&currency={self._config.currency}',
            'parsed.query != f"feed={ALPACA_BASIC_EQUITY_FEED}&currency={ALPACA_MARKET_DATA_CURRENCY}"',
        ):
            if anchor not in text:
                errors.append(f"market data: required read-only IEX anchor missing: {anchor}")
        if 'method="POST"' in text or "method='POST'" in text:
            errors.append("market data: POST authority is forbidden")
        if "paper-api.alpaca.markets" in text or '"/v2/orders"' in text:
            errors.append("market data: trading-host/order surface is forbidden")

    reconciliation = BROKER_DIR / RECONCILIATION_FILE
    if reconciliation.is_file():
        text = reconciliation.read_text(encoding="utf-8")
        _require_disabled_get_surface(text, RECONCILIATION_FILE, errors)
        if "/v2/orders:by_client_order_id" not in text:
            errors.append("reconciliation gateway: client_order_id lookup path is missing")
        if "nested=true" not in text:
            errors.append("reconciliation gateway: nested bracket lookup is missing")
        if 'method="POST"' in text or "method='POST'" in text:
            errors.append("reconciliation gateway: POST authority is forbidden")

    writer = BROKER_DIR / WRITER_FILE
    if writer.is_file():
        text = writer.read_text(encoding="utf-8")
        if "enabled: bool = False" not in text:
            errors.append("writer: disabled-by-default config contract is missing")
        if 'method="POST"' not in text and "method='POST'" not in text:
            errors.append("writer: exact POST construction is missing")
        if text.count("self._transport.write(request)") != 1:
            errors.append("writer: exactly one low-level transport write call is required")
        if "ProxyHandler({})" not in text:
            errors.append("writer: environment proxy bypass is missing")
        if "_RejectRedirectHandler()" not in text:
            errors.append("writer: redirect rejection is missing")
        if "PaperSubmissionStatus.UNKNOWN" not in text:
            errors.append("writer: durable UNKNOWN-before-write guard is missing")
        if "PAPER_ORDER_PATH = \"/v2/orders\"" not in text:
            errors.append("writer: exact /v2/orders path constant is missing")
        pre_consume = text.find("PaperFinalWritePhase.PRE_CONSUME")
        durable_unknown = text.find("mark_submit_attempt_unknown")
        pre_io = text.find("PaperFinalWritePhase.PRE_IO")
        transport_write = text.find("self._transport.write(request)")
        if not 0 <= pre_consume < durable_unknown < pre_io < transport_write:
            errors.append(
                "writer: final guard ordering must be PRE_CONSUME -> durable UNKNOWN -> PRE_IO -> one transport write"
            )
        if "final_guard: PaperFinalWriteGuard" not in text:
            errors.append("writer: authoritative PaperFinalWriteGuard parameter is required")
        if "previous_attestation=pre_consume_guard" not in text:
            errors.append("writer: PRE_IO must be chained to actual PRE_CONSUME attestation")
        request_validation = text.find("_validate_write_request(request)")
        if not 0 <= request_validation < pre_io < transport_write:
            errors.append(
                "writer: PRE_IO must occur after request validation and immediately before transport write"
            )

    crypto_writer = BROKER_DIR / CRYPTO_WRITER_FILE
    if crypto_writer.is_file():
        text = crypto_writer.read_text(encoding="utf-8")
        required = (
            'CRYPTO_ORDERS_PATH = "/v2/orders"',
            "enabled: bool = False",
            "host: str = ALPACA_PAPER_TRADING_HOST",
            "if self.host != ALPACA_PAPER_TRADING_HOST:",
            "http.client.HTTPSConnection(host",
            'connection.request("POST", path',
            "class GuardedAlpacaPaperCryptoWriteTransport:",
            "if not isinstance(self._transport, GuardedAlpacaPaperCryptoWriteTransport):",
            'getattr(self._transport, "role", None) is not order.role',
            "lifecycle.mark_entry_submission_unknown",
            "lifecycle.mark_protection_submission_unknown",
            "self._transport.post(",
            "crypto POST outcome is unknown",
            "reconcile by durable client_order_id",
        )
        for anchor in required:
            if anchor not in text:
                errors.append(f"crypto writer: required authority anchor missing: {anchor}")
        if text.count('connection.request("POST", path') != 1:
            errors.append("crypto writer: exactly one low-level HTTPS POST call site is required")
        if text.count("self._transport.post(") != 1:
            errors.append("crypto writer: exactly one transport POST call site is required")
        if LIVE_HOST in text:
            errors.append("crypto writer: LIVE Trading API host literal is forbidden")
        if "order_class" in text or "AlpacaEquityBracketRequest" in text:
            errors.append("crypto writer: equity bracket/order_class authority is forbidden")
        entry_unknown = text.find("lifecycle.mark_entry_submission_unknown")
        protection_unknown = text.find("lifecycle.mark_protection_submission_unknown")
        transport_post = text.find("self._transport.post(")
        if not 0 <= entry_unknown < transport_post:
            errors.append("crypto writer: entry UNKNOWN must precede transport POST")
        if not 0 <= protection_unknown < transport_post:
            errors.append("crypto writer: protection UNKNOWN must precede transport POST")

    crypto_pre_io = BROKER_DIR / CRYPTO_PRE_IO_FILE
    if not crypto_pre_io.is_file():
        errors.append("crypto PRE_IO interlock: module is missing")
    else:
        text = crypto_pre_io.read_text(encoding="utf-8")
        required = (
            "class FinalGuardedCryptoEntryTransport(GuardedAlpacaPaperCryptoWriteTransport):",
            "role = CryptoOrderRole.ENTRY",
            "class FinalGuardedCryptoProtectionTransport(GuardedAlpacaPaperCryptoWriteTransport):",
            "role = CryptoOrderRole.PROTECTION",
            "CryptoFinalWritePhase.PRE_IO",
            "CryptoProtectionFinalWritePhase.PRE_IO",
            "CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN",
            "CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN",
            "attestation.entry_attempt_count != 1",
            "attestation.protection_attempt_count != 1",
            "attestation.client_order_id != client_order_id",
            "self._last_attestation = attestation",
            "self._delegate.post(",
        )
        for anchor in required:
            if anchor not in text:
                errors.append(f"crypto PRE_IO interlock: required guard anchor missing: {anchor}")
        if text.count("self._delegate.post(") != 2:
            errors.append("crypto PRE_IO interlock: exactly two role-bound delegated POST call sites are required")
        if "HttpsAlpacaPaperCryptoWriteTransport" in text:
            errors.append("crypto PRE_IO interlock: direct HTTPS transport construction is forbidden")

        entry_start = text.find("class FinalGuardedCryptoEntryTransport(")
        protection_start = text.find("class FinalGuardedCryptoProtectionTransport(")
        simulation_start = text.find("class DeterministicCryptoPaperSimulationTransport", protection_start)
        if not 0 <= entry_start < protection_start < simulation_start:
            errors.append("crypto PRE_IO interlock: ENTRY/PROTECTION class boundaries are invalid")
        else:
            entry_text = text[entry_start:protection_start]
            protection_text = text[protection_start:simulation_start]
            _validate_crypto_pre_io_role(
                entry_text,
                label="ENTRY",
                phase="CryptoFinalWritePhase.PRE_IO",
                unknown="CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN",
                attempt="attestation.entry_attempt_count != 1",
                errors=errors,
            )
            _validate_crypto_pre_io_role(
                protection_text,
                label="PROTECTION",
                phase="CryptoProtectionFinalWritePhase.PRE_IO",
                unknown="CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN",
                attempt="attestation.protection_attempt_count != 1",
                errors=errors,
            )

    crypto_reconciliation = BROKER_DIR / CRYPTO_RECONCILIATION_FILE
    if not crypto_reconciliation.is_file():
        errors.append("crypto reconciliation: module is missing")
    else:
        text = crypto_reconciliation.read_text(encoding="utf-8")
        required = (
            'ORDER_BY_CLIENT_PATH = "/v2/orders:by_client_order_id"',
            'POSITION_PATH_PREFIX = "/v2/positions/"',
            "config or AlpacaPaperGatewayConfig()",
            "if not self._config.enabled:",
            'method="GET"',
            "UrllibAlpacaPaperReadTransport",
            "client_order_id=",
            "POST outcome remains unresolved",
            "confirmed_net_long_quantity=reconciliation.position.quantity",
        )
        for anchor in required:
            if anchor not in text:
                errors.append(f"crypto reconciliation: required GET-only anchor missing: {anchor}")
        for forbidden in (
            'method="POST"',
            "method='POST'",
            "http.client",
            ".post(",
            "AlpacaEquityBracketRequest",
        ):
            if forbidden in text:
                errors.append(f"crypto reconciliation: forbidden authority marker: {forbidden}")

    trade_updates = BROKER_DIR / TRADE_UPDATES_FILE
    if trade_updates.is_file():
        text = trade_updates.read_text(encoding="utf-8")
        if f'ALPACA_PAPER_TRADE_UPDATES_URL = "{PAPER_TRADE_UPDATES_URL}"' not in text:
            errors.append("trade_updates: exact PAPER WSS endpoint constant is missing")
        if f'ALPACA_LIVE_TRADE_UPDATES_URL = "{LIVE_TRADE_UPDATES_URL}"' not in text:
            errors.append("trade_updates: explicit LIVE WSS deny constant is missing")
        if text.count(f'"{LIVE_TRADE_UPDATES_URL}"') != 1:
            errors.append("trade_updates: LIVE WSS literal must appear exactly once as deny constant")
        if "enabled: bool = False" not in text:
            errors.append("trade_updates: disabled-by-default config contract is missing")
        if text.count("socket.send(") != 2:
            errors.append("trade_updates: exactly two internal socket control sends are required")
        if '"action": "auth"' not in text or '"action": "listen"' not in text:
            errors.append("trade_updates: exact auth/listen control actions are missing")
        if '"streams": [_TRADE_UPDATES_STREAM]' not in text:
            errors.append("trade_updates: listen surface must be trade_updates-only")
        if "proxy=None" not in text:
            errors.append("trade_updates: environment proxy bypass is missing")
        if "compression=None" not in text:
            errors.append("trade_updates: websocket compression must be disabled")
        if "def reconnect" in text:
            errors.append("trade_updates: reconnect surface is forbidden in R6")
        session_start = text.find("class PaperTradeUpdatesSession:")
        transport_start = text.find("class AlpacaPaperTradeUpdatesTransport:")
        if not 0 <= session_start < transport_start:
            errors.append("trade_updates: receive-only session class is missing")
        else:
            session_text = text[session_start:transport_start]
            if "def send(" in session_text or "def subscribe(" in session_text:
                errors.append("trade_updates: post-handshake session exposes forbidden send/subscribe")

    return errors


def _validate_crypto_pre_io_role(
    text: str,
    *,
    label: str,
    phase: str,
    unknown: str,
    attempt: str,
    errors: list[str],
) -> None:
    authorizer = text.find("self._authorizer()")
    phase_pos = text.find(phase, authorizer)
    unknown_pos = text.find(unknown, phase_pos)
    attempt_pos = text.find(attempt, unknown_pos)
    client = text.find("attestation.client_order_id != client_order_id", attempt_pos)
    delegate = text.find("self._delegate.post(", client)
    if text.count("self._delegate.post(") != 1:
        errors.append(f"crypto PRE_IO interlock: {label} must contain exactly one delegated POST call site")
    if not 0 <= authorizer < phase_pos < unknown_pos < attempt_pos < client < delegate:
        errors.append(
            f"crypto PRE_IO interlock: {label} ordering must be authorizer -> PRE_IO -> UNKNOWN -> one-attempt -> client binding -> delegate"
        )


def _require_disabled_get_surface(
    text: str,
    label: str,
    errors: list[str],
) -> None:
    if "enabled: bool = False" not in text:
        errors.append(f"{label}: disabled-by-default config contract is missing")
    if 'method="GET"' not in text and "method='GET'" not in text:
        errors.append(f"{label}: GET-only request construction is missing")
    if "ProxyHandler({})" not in text:
        errors.append(f"{label}: environment proxy bypass is missing")
    if "_RejectRedirectHandler()" not in text:
        errors.append(f"{label}: redirect rejection is missing")


def _forbidden_import(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )


def _network_import(module: str) -> bool:
    return module.split(".")[0] in NETWORK_ROOTS


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_transport_write(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "write"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "_transport"
    )


def _is_crypto_transport_post(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "post"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "_transport"
    )


def _is_crypto_pre_io_delegate_post(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "post"
        and isinstance(func.value, ast.Attribute)
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "self"
        and func.value.attr == "_delegate"
    )


def _is_named_socket_send(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "send"
        and isinstance(func.value, ast.Name)
        and func.value.id == "socket"
    )


def _inside_loop(tree: ast.AST, target: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            if any(child is target for child in ast.walk(node)):
                return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
