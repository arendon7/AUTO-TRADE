from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/paper_operations_read_model.py"


class BoundaryFailure(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BoundaryFailure(message)


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET))

    required = (
        "AlpacaPaperPortfolioGateway",
        "mode=ro&immutable=1",
        "PRAGMA query_only=ON",
        "account_reference",
        "prepared_account_fingerprint",
        "FirstCanaryCloseSourceReader",
        "FIRST_CLOSE_REQUIRES_ZERO_OPEN_ORDERS",
        '"broker_write_authorized": False',
        '"retry_post": False',
        '"credentials_persisted": False',
        '"live_trading": "BLOCKED"',
    )
    for token in required:
        _require(token in source, f"R7 operations read model missing required invariant: {token}")

    forbidden = (
        "SQLiteRuntime",
        "SQLiteR2SafetyStateStore",
        "SQLiteSafetyStateStore",
        "SQLitePortfolioStore",
        "paper_close_writer",
        "paper_close_execution_bridge",
        "paper_close_lifecycle",
        "UrllibAlpacaPaperWriteTransport",
        "HttpsAlpacaPaperCryptoWriteTransport",
        "https://api.alpaca.markets",
    )
    for token in forbidden:
        _require(token not in source, f"R7 operations read model contains forbidden authority: {token}")

    forbidden_methods = {"submit", "cancel", "replace", "write", "write_once", "update", "delete"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            _require(
                node.func.attr not in forbidden_methods,
                f"R7 operations read model calls forbidden mutation method: {node.func.attr}",
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            _require(
                node.value.strip().upper() != "POST",
                "R7 operations read model contains broker POST method authority",
            )

    execute_sql: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            continue
        execute_sql.append(node.args[0].value.strip().upper())
    _require(execute_sql, "R7 operations Safety reader has no explicit SQL inspection")
    for statement in execute_sql:
        _require(
            statement.startswith("PRAGMA") or statement.startswith("SELECT"),
            f"R7 operations read model contains mutating SQL: {statement[:40]}",
        )

    print(
        "R7 PAPER operations read-model boundary: PASS — fresh broker Portfolio GET-only; "
        "account-bound first-canary provenance; Safety SQLite mode=ro/immutable/query_only; "
        "no store initialization, broker POST/cancel/replace/retry/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
