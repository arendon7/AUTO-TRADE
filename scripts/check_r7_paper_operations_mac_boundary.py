from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "scripts/mac_r7_paper_operations_dashboard.py"
HTML = ROOT / "web/mac_r7_paper_operations.html"


class BoundaryFailure(RuntimeError):
    pass


def fail(message: str) -> None:
    print(f"R7 PAPER operations Mac boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BoundaryFailure(message)


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def class_methods(tree: ast.AST, class_name: str) -> set[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return set()


def main() -> int:
    for path in (OVERLAY, HTML):
        require(path.is_file(), f"missing R7 Mac surface: {path.relative_to(ROOT)}")

    overlay = OVERLAY.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    tree = ast.parse(overlay, filename=str(OVERLAY))

    required_overlay = (
        "class PaperOperationsSession(r6.AutoSettlementSession):",
        "PaperOperationsReadModel(workspace_path=self.workspace).snapshot(",
        "AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)",
        "FIRST_CANARY_PAPER_MIN_NOTIONAL",
        "FIRST_CANARY_PAPER_TARGET_NOTIONAL",
        "FIRST_CANARY_PAPER_MAX_NOTIONAL",
        '"/api/operations"',
        '"broker_write_authorized": False',
        '"close_execution_authorized": False',
        '"retry_post": False',
        '"credentials_persisted": False',
        '"live_trading": "BLOCKED"',
        "snapshot.portfolio.positions or snapshot.portfolio.open_orders",
        "super()._assert_no_unresolved_recovery()",
    )
    for token in required_overlay:
        require(token in overlay, f"overlay missing invariant: {token}")

    forbidden_overlay = (
        "paper_close_writer",
        "paper_close_execution_bridge",
        "PaperCloseWriter",
        "PaperCloseExecutionBridge",
        "HttpsAlpacaPaperCryptoWriteTransport",
        "UrllibAlpacaPaperWriteTransport",
        "R6_EXTERNAL_PAPER_WRITE=ENABLED",
        "ALPACA_LIVE_TRADING_HOST",
        "https://api.alpaca.markets",
        '"/api/close"',
        "stage_risk_reducing_external_submission",
        "submit_once(",
        "cancel_order(",
        "replace_order(",
    )
    for token in forbidden_overlay:
        require(token not in overlay, f"overlay contains forbidden authority: {token}")

    roots = {name.split(".", 1)[0] for name in imports(OVERLAY) if name}
    forbidden_external_network = roots & {"requests", "httpx", "aiohttp", "urllib", "websocket", "websockets"}
    require(
        not forbidden_external_network,
        f"overlay imports external network stack: {sorted(forbidden_external_network)}",
    )

    session_methods = class_methods(tree, "PaperOperationsSession")
    for method in {"connect", "prepare", "approve", "execute", "recover", "reset"}:
        require(
            method not in session_methods,
            f"R7 overlay must inherit certified R6 authority method unchanged: {method}",
        )
    handler_methods = class_methods(tree, "PaperOperationsHandler")
    require("do_GET" in handler_methods, "R7 handler must own the read-only GET surface")
    require("do_POST" not in handler_methods, "R7 handler may not add a POST surface")

    dangerous_attrs = {
        "submit",
        "submit_once",
        "cancel",
        "cancel_order",
        "replace_order",
        "write",
        "write_once",
        "stage_risk_reducing_external_submission",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            require(
                node.func.attr not in dangerous_attrs,
                f"overlay calls forbidden external mutation method: {node.func.attr}",
            )

    required_html = (
        "AUTO-TRADE · R7 OPERATIONS",
        "PORTFOLIO GET-ONLY",
        "ENTRY USD 10–12",
        "LIVE BLOCKED",
        "Actualizar broker truth",
        "Safety kill switch",
        "Safety circuit",
        "P&L no realizado",
        "ready_for_close_preparation",
        "Cierre de posición",
        "Aún no está habilitado.",
        "Close write: DISABLED",
        "NO vuelvas a pulsar ejecutar",
        "get('/api/operations')",
    )
    for token in required_html:
        require(token in html, f"R7 UI missing operator/read-only anchor: {token}")

    forbidden_html = (
        "/api/close",
        "EJECUTAR CIERRE",
        "CERRAR POSICIÓN AHORA",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "USD 1–5",
        "USD 1-5",
        "target=2",
        "máximo USD 5",
    )
    for token in forbidden_html:
        require(token not in html, f"R7 UI contains stale/forbidden authority text: {token}")

    print(
        "R7 PAPER operations Mac boundary: PASS — R7 subclasses the certified R6 session without "
        "redefining connect/prepare/approve/execute/recover; adds only /api/operations GET; fresh "
        "broker exposure interlocks new BUY preparation; policy metadata comes from canonical "
        "10/10.50/12 constants; no close writer/POST/cancel/replace/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryFailure as exc:
        fail(str(exc))
