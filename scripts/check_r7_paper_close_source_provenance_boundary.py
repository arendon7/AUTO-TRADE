from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/paper_close_source_provenance.py"


class BoundaryFailure(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise BoundaryFailure(message)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(TARGET))

    required = {
        "mode=ro&immutable=1": "immutable read-only SQLite URI",
        "PRAGMA query_only=ON": "SQLite query-only enforcement",
        "attempt_db_sha256": "physical database hash binding",
        "execution_started_hash": "irreversible execution latch binding",
        "execution_result_hash": "one-shot execution result binding",
        "recovery_resolution_hash": "GET-only terminal recovery binding",
        "ENTRY_FILLED_UNPROTECTED": "terminal source lifecycle requirement",
        "entry_attempt_count": "single burned entry attempt proof",
        "retry_post": "no-retry terminal resolution proof",
        '"live_trading", "BLOCKED"': "permanent LIVE deny proof",
        "_validate_fee_adjusted_net_position": "gross/net received-asset fee validation",
    }
    for token, label in required.items():
        if token not in text:
            _fail(f"missing {label}: {token}")

    forbidden_text = {
        "SQLiteRuntime(": "historical attempt DB must never initialize writable runtime",
        "FirstCanaryAttemptWorkspace.open(": "reader must not create attempt directories",
        "submit_once(": "source provenance may not invoke a broker writer",
        "PaperCloseWriter": "source provenance may not import close writer authority",
        "http.client": "source provenance may not own HTTP transport",
        "urllib": "source provenance may not own network transport",
    }
    for token, label in forbidden_text.items():
        if token in text:
            _fail(f"{label}: found {token}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                names = [node.module or ""]
            for name in names:
                if name.startswith(("http", "urllib", "requests", "websockets")):
                    _fail(f"network import forbidden in provenance reader: {name}")
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr.lower() in {
            "post",
            "put",
            "patch",
            "delete",
            "submit_once",
            "cancel",
            "replace_order",
        }:
            _fail(f"broker/network mutation call forbidden: {func.attr}")
        if isinstance(func, ast.Attribute) and func.attr == "execute" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                statement = first.value.strip().upper()
                if not statement.startswith(("SELECT", "PRAGMA")):
                    _fail(f"non-read-only SQL forbidden in provenance reader: {statement[:40]}")

    if "sqlite3.connect(uri, uri=True, isolation_level=None)" not in text:
        _fail("SQLite connection must use explicit URI read-only mode")
    if text.count("sqlite3.connect(") != 1:
        _fail("provenance reader must expose exactly one SQLite connection site")
    if "-wal" not in text or "-shm" not in text:
        _fail("provenance reader must fail closed on active WAL/SHM sidecars")
    if "database bytes changed during read-only verification" not in text:
        _fail("provenance reader must compare database bytes before/after read")

    print(
        "R7 first-canary close source provenance boundary: PASS — exact hashed attempt artifacts + "
        "canonical OMS/lifecycle chain read via mode=ro/immutable/query_only; physical DB unchanged; "
        "one burned POST + terminal fee-aware broker truth required; no credentials/network/writer/retry/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
