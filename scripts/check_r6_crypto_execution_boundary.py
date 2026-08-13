from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BROKERS = ROOT / "src/autotrade/brokers"
WRITER = BROKERS / "alpaca_paper_crypto_writer.py"
RECONCILIATION = BROKERS / "alpaca_paper_crypto_reconciliation.py"
ORDER = BROKERS / "alpaca_paper_crypto_order.py"
LIFECYCLE = BROKERS / "alpaca_paper_crypto_lifecycle.py"
CRYPTO_MODULES = tuple(sorted(BROKERS.glob("alpaca_paper_crypto_*.py")))

FORBIDDEN_CROSS_PRODUCT = (
    "alpaca_paper_bracket",
    "alpaca_paper_writer",
    "alpaca_paper_final_guard",
    "connectivity_final_freshness",
    "connectivity_workspace_post",
)
NETWORK_IMPORT_ROOTS = {"http", "urllib", "socket", "requests", "httpx"}


def main() -> int:
    errors: list[str] = []
    for path in (WRITER, RECONCILIATION, ORDER, LIFECYCLE):
        if not path.is_file():
            errors.append(f"missing crypto execution contract file: {path.relative_to(ROOT)}")

    if WRITER.is_file():
        text = WRITER.read_text(encoding="utf-8")
        required = {
            'CRYPTO_ORDERS_PATH = "/v2/orders"': "exact orders path is missing",
            "enabled: bool = False": "crypto writer must be disabled by default",
            "host: str = ALPACA_PAPER_TRADING_HOST": "writer host is not bound to PAPER constant",
            'if self.host != ALPACA_PAPER_TRADING_HOST': "writer exact PAPER host self-check is missing",
            "http.client.HTTPSConnection(host": "writer TLS transport is missing",
            'connection.request("POST", path': "writer exact POST transport is missing",
            "lifecycle.mark_entry_submission_unknown": "entry UNKNOWN-before-I/O transition is missing",
            "lifecycle.mark_protection_submission_unknown": "protection UNKNOWN-before-I/O transition is missing",
            "self._transport.post(": "one-shot transport call is missing",
            'raise CryptoLifecycleBlocked("entry POST requires durable ENTRY_PREPARED")': "entry state gate is missing",
            'raise CryptoLifecycleBlocked("protection POST requires durable PROTECTION_PREPARED")': "protection state gate is missing",
            "reconcile by durable client_order_id": "ambiguous ACK reconciliation instruction is missing",
        }
        for needle, reason in required.items():
            if needle not in text:
                errors.append(f"writer: {reason}")
        if "api.alpaca.markets" in text:
            errors.append("writer contains LIVE Alpaca host literal")
        if "order_class" in text:
            errors.append("crypto writer may not expose equity order_class semantics")
        if text.find("lifecycle.mark_entry_submission_unknown") > text.find("self._transport.post("):
            errors.append("entry UNKNOWN transition does not precede broker I/O in source authority flow")
        if text.find("lifecycle.mark_protection_submission_unknown") > text.find("self._transport.post("):
            errors.append("protection UNKNOWN transition does not precede broker I/O in source authority flow")

    if RECONCILIATION.is_file():
        text = RECONCILIATION.read_text(encoding="utf-8")
        required = {
            'ORDER_BY_CLIENT_PATH = "/v2/orders:by_client_order_id"': "client-order reconciliation endpoint is missing",
            'POSITION_PATH_PREFIX = "/v2/positions/"': "position reconciliation endpoint is missing",
            "client_order_id=": "durable client_order_id lookup binding is missing",
            "UrllibAlpacaPaperReadTransport": "reconciliation must use certified read transport",
            'method="GET"': "reconciliation GET-only request is missing",
            "POST outcome remains unresolved": "not-found ambiguity handling is missing",
            "confirmed_net_long_quantity=reconciliation.position.quantity": "position truth is not applied to lifecycle",
        }
        for needle, reason in required.items():
            if needle not in text:
                errors.append(f"reconciliation: {reason}")
        for forbidden in ("http.client", ".post(", 'method="POST"'):
            if forbidden in text:
                errors.append(f"reconciliation contains forbidden write marker: {forbidden}")

    for path in CRYPTO_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = _imports(tree)
        for module in imports:
            if any(fragment in module for fragment in FORBIDDEN_CROSS_PRODUCT):
                errors.append(
                    f"{path.name}: imports forbidden equity/write authority {module}"
                )
        network_roots = {module.split(".", 1)[0] for module in imports if module}
        if path != WRITER and network_roots & NETWORK_IMPORT_ROOTS:
            errors.append(
                f"{path.name}: only dedicated crypto writer may import direct network stack; found {sorted(network_roots & NETWORK_IMPORT_ROOTS)}"
            )

    if ORDER.is_file():
        text = ORDER.read_text(encoding="utf-8")
        for forbidden in ("http.client", "urllib", "socket", "requests", "httpx", ".post("):
            if forbidden in text:
                errors.append(f"order contract contains forbidden network marker: {forbidden}")
    if LIFECYCLE.is_file():
        text = LIFECYCLE.read_text(encoding="utf-8")
        for forbidden in ("http.client", "urllib", "socket", "requests", "httpx", ".post("):
            if forbidden in text:
                errors.append(f"lifecycle contains forbidden network marker: {forbidden}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 crypto execution authority boundary: PASS "
        "(dedicated disabled-by-default PAPER writer; UNKNOWN-before-I/O; exact /v2/orders POST; "
        "GET-only client-id + position reconciliation; no equity bracket/LIVE cross-authority)"
    )
    return 0


def _imports(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


if __name__ == "__main__":
    raise SystemExit(main())
