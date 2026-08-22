from __future__ import annotations

import ast
from html import unescape
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


def _is_local_http_response_write(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "write":
        return False
    receiver = node.func.value
    return (
        isinstance(receiver, ast.Attribute)
        and receiver.attr == "wfile"
        and isinstance(receiver.value, ast.Name)
        and receiver.value.id == "self"
    )


def main() -> int:
    for path in (OVERLAY, HTML):
        require(path.is_file(), f"missing R7 Mac surface: {path.relative_to(ROOT)}")

    overlay = OVERLAY.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    html_visible = unescape(html)
    tree = ast.parse(overlay, filename=str(OVERLAY))

    required_overlay = (
        "class PaperOperationsSession(r6.AutoSettlementSession):",
        "result = super().connect(payload)",
        "PaperOperationsReadModel(workspace_path=self.workspace).snapshot(",
        "AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)",
        "PaperCloseOperator(workspace_path=self.workspace)",
        "FIRST_CANARY_PAPER_MIN_NOTIONAL",
        "FIRST_CANARY_PAPER_TARGET_NOTIONAL",
        "FIRST_CANARY_PAPER_MAX_NOTIONAL",
        '"/api/operations"',
        '"/api/close/prepare"',
        '"/api/close/approve"',
        '"/api/close/execute"',
        '"/api/close/recover"',
        "secrets.compare_digest(supplied, self.close_review_token)",
        "secrets.compare_digest(supplied, self.close_execute_token)",
        "self.close_execute_token = None",
        "operator.prepare_full_close(credentials=self._paper_credentials())",
        "self._close_operator().approve(prepared=prepared)",
        "self._close_operator().execute_once(",
        "self._close_operator().recover(credentials=self._paper_credentials())",
        '"broker_write_authorized": False',
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
        "paper_close_reconciliation",
        "PaperCloseWriter",
        "PaperCloseExecutionBridge",
        "AlpacaPaperCloseReconciliationGateway",
        "HttpsAlpacaPaperCryptoWriteTransport",
        "UrllibAlpacaPaperWriteTransport",
        "R6_EXTERNAL_PAPER_WRITE=ENABLED",
        "ALPACA_LIVE_TRADING_HOST",
        "https://api.alpaca.markets",
        "stage_risk_reducing_external_submission",
        "submit_once(",
        "mark_submission_unknown(",
        "cancel_order(",
        "replace_order(",
    )
    for token in forbidden_overlay:
        require(token not in overlay, f"overlay contains forbidden low-level authority: {token}")

    for route in (
        '"/api/close/prepare"',
        '"/api/close/approve"',
        '"/api/close/execute"',
        '"/api/close/recover"',
    ):
        require(overlay.count(route) == 1, f"close route must occur exactly once: {route}")

    roots = {name.split(".", 1)[0] for name in imports(OVERLAY) if name}
    forbidden_external_network = roots & {"requests", "httpx", "aiohttp", "urllib", "websocket", "websockets"}
    require(
        not forbidden_external_network,
        f"overlay imports external network stack: {sorted(forbidden_external_network)}",
    )

    session_methods = class_methods(tree, "PaperOperationsSession")
    for method in {"prepare", "approve", "execute", "recover", "reset"}:
        require(
            method not in session_methods,
            f"R7 overlay must inherit certified R6 entry method unchanged: {method}",
        )
    require("connect" in session_methods, "R7 session must wrap connect to discover close recovery")
    for method in {"close_prepare", "close_approve", "close_execute", "close_recover"}:
        require(method in session_methods, f"R7 session missing exact close facade method: {method}")

    handler_methods = class_methods(tree, "PaperOperationsHandler")
    require("do_GET" in handler_methods, "R7 handler must own operations GET surface")
    require("do_POST" in handler_methods, "R7 handler must dispatch exact close facade POST routes")
    require("super().do_POST()" in overlay, "non-close POST routes must remain inherited R6 authority")

    dangerous_attrs = {
        "submit",
        "submit_once",
        "cancel",
        "cancel_order",
        "replace_order",
        "write_once",
        "stage_risk_reducing_external_submission",
        "mark_submission_unknown",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "write":
                require(
                    _is_local_http_response_write(node),
                    "overlay calls non-local write method",
                )
                continue
            require(
                node.func.attr not in dangerous_attrs,
                f"overlay calls forbidden external mutation method: {node.func.attr}",
            )

    required_visible_html = (
        "AUTO-TRADE · R7 OPERATIONS",
        "PORTFOLIO GET-ONLY",
        "ENTRY USD 10–12",
        "LIVE BLOCKED",
        "Actualizar broker truth",
        "Safety kill switch",
        "Safety circuit",
        "P&L no realizado",
        "Cierre total de la posición PAPER",
        "Preparar cierre total",
        "Aprobar cierre",
        "CERRAR UNA VEZ EN PAPER",
        "Reconciliar cierre por GET",
        "NO vuelvas a pulsar cerrar",
        "Retry POST: FALSE",
    )
    for token in required_visible_html:
        require(token in html_visible, f"R7 UI missing operator/close anchor: {token}")

    for token in (
        "ready_for_close_preparation",
        "close_preparation_allowed",
        "close_recovery_pending",
        "get('/api/operations')",
        "post('/api/close/prepare'",
        "post('/api/close/approve'",
        "post('/api/close/execute'",
        "post('/api/close/recover'",
    ):
        require(token in html, f"R7 UI missing exact close source anchor: {token}")

    forbidden_html = (
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "USD 1–5",
        "USD 1-5",
        "target=2",
        "máximo USD 5",
        "CERRAR PAPER",
        "Attempt ID",
        "challenge",
        "retry close",
        "reintentar cierre",
    )
    for token in forbidden_html:
        require(token not in html, f"R7 UI contains stale/forbidden authority text: {token}")

    print(
        "R7 PAPER operations Mac boundary: PASS — R6 entry authority remains inherited; connect only wraps "
        "recovery discovery; Portfolio/Safety remains GET broker truth; exactly four close routes call only "
        "PaperCloseOperator; two explicit one-shot human tokens guard FULL BTC/USD SELL LIMIT IOC; dashboard "
        "has no low-level writer/bridge/reconciler/network authority; burned close attempts recover GET-only; "
        "credentials memory-only; no retry POST; LIVE blocked"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryFailure as exc:
        fail(str(exc))
