from __future__ import annotations

from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one marker, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# Safety uses the same semantic integrity validator as persistence.
# ---------------------------------------------------------------------------
safety = ROOT / "src" / "autotrade" / "safety.py"
text = safety.read_text(encoding="utf-8")
old_import = "from .ledger import EventLedger, LedgerEvent\nfrom .state import InMemorySafetyStateStore, SafetyStateStore\n"
new_import = (
    "from .ledger import EventLedger, LedgerEvent\n"
    "from .portfolio_integrity import portfolio_snapshot_error\n"
    "from .state import InMemorySafetyStateStore, SafetyStateStore\n"
)
if text.count(old_import) != 1:
    raise SystemExit("safety import marker mismatch")
text = text.replace(old_import, new_import, 1)
pattern = re.compile(
    r"def _validate_portfolio\(portfolio: PortfolioSnapshot\) -> str \| None:\n.*?\n    return None\n?$",
    re.S,
)
replacement = (
    "def _validate_portfolio(portfolio: PortfolioSnapshot) -> str | None:\n"
    "    # Backward-compatible private wrapper. Semantic trust is centralized\n"
    "    # in portfolio_integrity so persistence and Safety cannot disagree.\n"
    "    return portfolio_snapshot_error(portfolio)\n"
)
text, count = pattern.subn(replacement, text)
if count != 1:
    raise SystemExit(f"safety validator marker mismatch: {count}")
safety.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Durable fill store: verify hash + independent row identity on every read and
# duplicate replay, normalizing corruption into FillIntegrityConflict.
# ---------------------------------------------------------------------------
execution = ROOT / "src" / "autotrade" / "execution_state.py"
text = execution.read_text(encoding="utf-8")
text = text.replace("from decimal import Decimal\n", "from decimal import Decimal, InvalidOperation\n", 1)
old_duplicate = '''            row = conn.execute(\n                "SELECT fill_hash FROM order_fills WHERE fill_id = ?", (fill.fill_id,)\n            ).fetchone()\n            if row is not None:\n                if row["fill_hash"] != fingerprint:\n                    conn.execute("ROLLBACK")\n                    raise FillIntegrityConflict(fill.fill_id)\n                conn.execute("COMMIT")\n                return False\n'''
new_duplicate = '''            row = conn.execute(\n                """\n                SELECT fill_id, order_id, fill_json, fill_hash, occurred_at\n                FROM order_fills WHERE fill_id = ?\n                """,\n                (fill.fill_id,),\n            ).fetchone()\n            if row is not None:\n                existing = _fill_from_storage(row)\n                if fill_fingerprint(existing) != fingerprint:\n                    conn.execute("ROLLBACK")\n                    raise FillIntegrityConflict(fill.fill_id)\n                conn.execute("COMMIT")\n                return False\n'''
if text.count(old_duplicate) != 1:
    raise SystemExit("fill duplicate marker mismatch")
text = text.replace(old_duplicate, new_duplicate, 1)
old_read = '''            rows = conn.execute(\n                """\n                SELECT fill_json FROM order_fills\n                WHERE order_id = ? ORDER BY occurred_at, fill_id\n                """,\n                (order_id,),\n            ).fetchall()\n            return tuple(_fill_from_json(row["fill_json"]) for row in rows)\n'''
new_read = '''            rows = conn.execute(\n                """\n                SELECT fill_id, order_id, fill_json, fill_hash, occurred_at\n                FROM order_fills\n                WHERE order_id = ? ORDER BY occurred_at, fill_id\n                """,\n                (order_id,),\n            ).fetchall()\n            fills = tuple(_fill_from_storage(row) for row in rows)\n            if any(fill.order_id != order_id for fill in fills):\n                raise FillIntegrityConflict("stored fill order identity mismatch")\n            return fills\n'''
if text.count(old_read) != 1:
    raise SystemExit("fill read marker mismatch")
text = text.replace(old_read, new_read, 1)
append_marker = '''def _fill_from_json(raw: str) -> Fill:\n    data = json.loads(raw)\n    return Fill(\n        fill_id=data["fill_id"],\n        order_id=data["order_id"],\n        symbol=data["symbol"],\n        side=Side(data["side"]),\n        quantity=Decimal(data["quantity"]),\n        price=Decimal(data["price"]),\n        occurred_at=datetime.fromisoformat(data["occurred_at"]),\n    )\n'''
append_replacement = append_marker + '''\n\ndef _fill_from_storage(row) -> Fill:\n    expected_hash = row["fill_hash"]\n    if (\n        not isinstance(expected_hash, str)\n        or len(expected_hash) != 64\n        or any(char not in "0123456789abcdef" for char in expected_hash)\n    ):\n        raise FillIntegrityConflict("stored fill hash is invalid")\n    try:\n        fill = _fill_from_json(row["fill_json"])\n        _validate_fill_shape(fill)\n    except (\n        json.JSONDecodeError,\n        KeyError,\n        TypeError,\n        ValueError,\n        InvalidOperation,\n    ) as exc:\n        raise FillIntegrityConflict("stored fill payload is invalid") from exc\n    if fill.fill_id != row["fill_id"]:\n        raise FillIntegrityConflict("stored fill_id column mismatch")\n    if fill.order_id != row["order_id"]:\n        raise FillIntegrityConflict("stored order_id column mismatch")\n    if fill.occurred_at.isoformat() != row["occurred_at"]:\n        raise FillIntegrityConflict("stored occurred_at column mismatch")\n    if fill_fingerprint(fill) != expected_hash:\n        raise FillIntegrityConflict("stored fill hash mismatch")\n    return fill\n'''
if text.count(append_marker) != 1:
    raise SystemExit("fill helper marker mismatch")
text = text.replace(append_marker, append_replacement, 1)
execution.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Portfolio persistence: independent snapshot hash, strict migration, semantic
# validation on every read/write/CAS/reconciliation/reservation path.
# ---------------------------------------------------------------------------
persistence = ROOT / "src" / "autotrade" / "persistence.py"
text = persistence.read_text(encoding="utf-8")
old_state_import = '''from .state import (\n    PortfolioNotInitialized,\n'''
new_state_import = '''from .portfolio_integrity import PortfolioIntegrityError, validate_portfolio_snapshot\nfrom .state import (\n    PortfolioNotInitialized,\n'''
if text.count(old_state_import) != 1:
    raise SystemExit("persistence state import marker mismatch")
text = text.replace(old_state_import, new_state_import, 1)
text = text.replace(
    '''                    snapshot_json TEXT NOT NULL,\n                    updated_at TEXT NOT NULL\n''',
    '''                    snapshot_json TEXT NOT NULL,\n                    snapshot_hash TEXT,\n                    updated_at TEXT NOT NULL\n''',
    1,
)
migration_marker = '''                INSERT OR IGNORE INTO reservation_meta(singleton_id, generation) VALUES (1, 0);\n                """\n            )\n        finally:\n            conn.close()\n'''
migration_replacement = '''                INSERT OR IGNORE INTO reservation_meta(singleton_id, generation) VALUES (1, 0);\n                """\n            )\n            _ensure_portfolio_state_integrity_schema(conn)\n        finally:\n            conn.close()\n'''
if text.count(migration_marker) != 1:
    raise SystemExit("portfolio schema migration marker mismatch")
text = text.replace(migration_marker, migration_replacement, 1)

start = text.index("class SQLitePortfolioStore:")
end = text.index("\n\nclass SQLiteSafetyStateStore:", start)
new_store = '''class SQLitePortfolioStore:\n    def __init__(self, runtime: SQLiteRuntime) -> None:\n        self._runtime = runtime\n\n    def initialize(self, snapshot: PortfolioSnapshot, *, now: datetime) -> VersionedPortfolioSnapshot:\n        snapshot_json, snapshot_hash = _portfolio_for_storage(snapshot)\n        conn = self._runtime.connect()\n        try:\n            conn.execute("BEGIN IMMEDIATE")\n            row = conn.execute(\n                "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"\n            ).fetchone()\n            if row is None:\n                conn.execute(\n                    """\n                    INSERT INTO portfolio_state(\n                        singleton_id, version, snapshot_json, snapshot_hash, updated_at\n                    ) VALUES (1, 1, ?, ?, ?)\n                    """,\n                    (snapshot_json, snapshot_hash, now.isoformat()),\n                )\n                conn.execute("COMMIT")\n                return VersionedPortfolioSnapshot(version=1, snapshot=snapshot)\n            current = _portfolio_from_storage(\n                snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]\n            )\n            conn.execute("COMMIT")\n            return VersionedPortfolioSnapshot(version=int(row["version"]), snapshot=current)\n        except Exception:\n            if conn.in_transaction:\n                conn.execute("ROLLBACK")\n            raise\n        finally:\n            conn.close()\n\n    def get(self) -> VersionedPortfolioSnapshot:\n        conn = self._runtime.connect()\n        try:\n            row = conn.execute(\n                "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"\n            ).fetchone()\n            if row is None:\n                raise PortfolioNotInitialized("portfolio state is not initialized")\n            return VersionedPortfolioSnapshot(\n                version=int(row["version"]),\n                snapshot=_portfolio_from_storage(\n                    snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]\n                ),\n            )\n        finally:\n            conn.close()\n\n    def compare_and_set(\n        self,\n        *,\n        expected_version: int,\n        snapshot: PortfolioSnapshot,\n        now: datetime,\n    ) -> VersionedPortfolioSnapshot | None:\n        snapshot_json, snapshot_hash = _portfolio_for_storage(snapshot)\n        conn = self._runtime.connect()\n        try:\n            conn.execute("BEGIN IMMEDIATE")\n            row = conn.execute(\n                "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"\n            ).fetchone()\n            if row is None or int(row["version"]) != expected_version:\n                conn.execute("ROLLBACK")\n                return None\n            _portfolio_from_storage(\n                snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]\n            )\n            cursor = conn.execute(\n                """\n                UPDATE portfolio_state\n                SET version = version + 1, snapshot_json = ?, snapshot_hash = ?, updated_at = ?\n                WHERE singleton_id = 1 AND version = ?\n                """,\n                (snapshot_json, snapshot_hash, now.isoformat(), expected_version),\n            )\n            if cursor.rowcount != 1:\n                conn.execute("ROLLBACK")\n                return None\n            conn.execute("COMMIT")\n            return VersionedPortfolioSnapshot(version=expected_version + 1, snapshot=snapshot)\n        except Exception:\n            if conn.in_transaction:\n                conn.execute("ROLLBACK")\n            raise\n        finally:\n            conn.close()\n\n    def set_reconciliation_status(\n        self,\n        *,\n        reconciliation_ok: bool,\n        broker_state_known: bool,\n        now: datetime,\n    ) -> VersionedPortfolioSnapshot:\n        if not isinstance(reconciliation_ok, bool) or not isinstance(broker_state_known, bool):\n            raise PortfolioIntegrityError("reconciliation status flags must be boolean")\n        conn = self._runtime.connect()\n        try:\n            conn.execute("BEGIN IMMEDIATE")\n            row = conn.execute(\n                "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"\n            ).fetchone()\n            if row is None:\n                conn.execute("ROLLBACK")\n                raise PortfolioNotInitialized("portfolio state is not initialized")\n            current = _portfolio_from_storage(\n                snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]\n            )\n            version = int(row["version"])\n            if (\n                current.reconciliation_ok == reconciliation_ok\n                and current.broker_state_known == broker_state_known\n            ):\n                conn.execute("COMMIT")\n                return VersionedPortfolioSnapshot(version=version, snapshot=current)\n            updated = replace(\n                current,\n                reconciliation_ok=reconciliation_ok,\n                broker_state_known=broker_state_known,\n            )\n            snapshot_json, snapshot_hash = _portfolio_for_storage(updated)\n            conn.execute(\n                """\n                UPDATE portfolio_state\n                SET version = ?, snapshot_json = ?, snapshot_hash = ?, updated_at = ?\n                WHERE singleton_id = 1\n                """,\n                (version + 1, snapshot_json, snapshot_hash, now.isoformat()),\n            )\n            conn.execute("COMMIT")\n            return VersionedPortfolioSnapshot(version=version + 1, snapshot=updated)\n        except Exception:\n            if conn.in_transaction:\n                conn.execute("ROLLBACK")\n            raise\n        finally:\n            conn.close()\n\n    def apply_order_result(self, order: OrderRecord, *, now: datetime) -> VersionedPortfolioSnapshot:\n        conn = self._runtime.connect()\n        try:\n            conn.execute("BEGIN IMMEDIATE")\n            row = conn.execute(\n                "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"\n            ).fetchone()\n            if row is None:\n                conn.execute("ROLLBACK")\n                raise PortfolioNotInitialized("portfolio state is not initialized")\n            version = int(row["version"])\n            current = _portfolio_from_storage(\n                snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]\n            )\n            already_applied = conn.execute(\n                "SELECT 1 FROM portfolio_applied_orders WHERE order_id = ?", (order.order_id,)\n            ).fetchone()\n            if already_applied is not None or order.filled_quantity <= 0:\n                conn.execute("COMMIT")\n                return VersionedPortfolioSnapshot(version=version, snapshot=current)\n\n            updated = apply_fill_to_portfolio(current, order)\n            snapshot_json, snapshot_hash = _portfolio_for_storage(updated)\n            conn.execute(\n                """\n                UPDATE portfolio_state\n                SET version = ?, snapshot_json = ?, snapshot_hash = ?, updated_at = ?\n                WHERE singleton_id = 1\n                """,\n                (version + 1, snapshot_json, snapshot_hash, now.isoformat()),\n            )\n            conn.execute(\n                "INSERT INTO portfolio_applied_orders(order_id, applied_at) VALUES (?, ?)",\n                (order.order_id, now.isoformat()),\n            )\n            conn.execute("COMMIT")\n            return VersionedPortfolioSnapshot(version=version + 1, snapshot=updated)\n        except Exception:\n            if conn.in_transaction:\n                conn.execute("ROLLBACK")\n            raise\n        finally:\n            conn.close()\n'''
text = text[:start] + new_store + text[end:]

old_reservation = '''            portfolio_row = conn.execute(\n                "SELECT version FROM portfolio_state WHERE singleton_id = 1"\n            ).fetchone()\n            if portfolio_row is None:\n                conn.execute("ROLLBACK")\n                raise PortfolioNotInitialized("portfolio state is not initialized")\n            portfolio_version = int(portfolio_row["version"])\n'''
new_reservation = '''            portfolio_row = conn.execute(\n                """\n                SELECT version, snapshot_json, snapshot_hash\n                FROM portfolio_state WHERE singleton_id = 1\n                """\n            ).fetchone()\n            if portfolio_row is None:\n                conn.execute("ROLLBACK")\n                raise PortfolioNotInitialized("portfolio state is not initialized")\n            _portfolio_from_storage(\n                snapshot_json=portfolio_row["snapshot_json"],\n                snapshot_hash=portfolio_row["snapshot_hash"],\n            )\n            portfolio_version = int(portfolio_row["version"])\n'''
if text.count(old_reservation) != 1:
    raise SystemExit("reservation portfolio marker mismatch")
text = text.replace(old_reservation, new_reservation, 1)

# Replace portfolio deserializer helpers with strict parsing + hash/migration.
helper_start = text.index("def _portfolio_to_json(snapshot: PortfolioSnapshot) -> str:")
helper_end = text.index("\n\ndef _reservation_from_row", helper_start)
new_helpers = '''def _portfolio_to_json(snapshot: PortfolioSnapshot) -> str:\n    payload = {\n        "snapshot_id": snapshot.snapshot_id,\n        "equity": str(snapshot.equity),\n        "gross_exposure": str(snapshot.gross_exposure),\n        "net_exposure": str(snapshot.net_exposure),\n        "daily_pnl": str(snapshot.daily_pnl),\n        "drawdown": str(snapshot.drawdown),\n        "open_orders": snapshot.open_orders,\n        "signed_position_notional_by_symbol": _decimal_map(snapshot.signed_position_notional_by_symbol),\n        "strategy_gross_exposure": _decimal_map(snapshot.strategy_gross_exposure),\n        "strategy_signed_position_notional_by_symbol": {\n            strategy: _decimal_map(values)\n            for strategy, values in snapshot.strategy_signed_position_notional_by_symbol.items()\n        },\n        "reconciliation_ok": snapshot.reconciliation_ok,\n        "broker_state_known": snapshot.broker_state_known,\n    }\n    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)\n\n\ndef _portfolio_from_json(raw: str) -> PortfolioSnapshot:\n    data = json.loads(raw)\n    if not isinstance(data, dict):\n        raise ValueError("portfolio payload must be object")\n    expected = {\n        "snapshot_id",\n        "equity",\n        "gross_exposure",\n        "net_exposure",\n        "daily_pnl",\n        "drawdown",\n        "open_orders",\n        "signed_position_notional_by_symbol",\n        "strategy_gross_exposure",\n        "strategy_signed_position_notional_by_symbol",\n        "reconciliation_ok",\n        "broker_state_known",\n    }\n    if set(data) != expected:\n        raise ValueError("portfolio payload fields mismatch")\n    if isinstance(data["open_orders"], bool) or not isinstance(data["open_orders"], int):\n        raise ValueError("portfolio open_orders must be integer")\n    if not isinstance(data["reconciliation_ok"], bool):\n        raise ValueError("portfolio reconciliation_ok must be boolean")\n    if not isinstance(data["broker_state_known"], bool):\n        raise ValueError("portfolio broker_state_known must be boolean")\n    if not isinstance(data["strategy_signed_position_notional_by_symbol"], dict):\n        raise ValueError("portfolio strategy position maps must be object")\n    snapshot = PortfolioSnapshot(\n        snapshot_id=data["snapshot_id"],\n        equity=Decimal(data["equity"]),\n        gross_exposure=Decimal(data["gross_exposure"]),\n        net_exposure=Decimal(data["net_exposure"]),\n        daily_pnl=Decimal(data["daily_pnl"]),\n        drawdown=Decimal(data["drawdown"]),\n        open_orders=data["open_orders"],\n        signed_position_notional_by_symbol=_parse_decimal_map(data["signed_position_notional_by_symbol"]),\n        strategy_gross_exposure=_parse_decimal_map(data["strategy_gross_exposure"]),\n        strategy_signed_position_notional_by_symbol={\n            strategy: _parse_decimal_map(values)\n            for strategy, values in data["strategy_signed_position_notional_by_symbol"].items()\n        },\n        reconciliation_ok=data["reconciliation_ok"],\n        broker_state_known=data["broker_state_known"],\n    )\n    validate_portfolio_snapshot(snapshot)\n    return snapshot\n\n\ndef _portfolio_hash(snapshot_json: str) -> str:\n    return sha256(snapshot_json.encode("utf-8")).hexdigest()\n\n\ndef _valid_sha256(value: object) -> bool:\n    return (\n        isinstance(value, str)\n        and len(value) == 64\n        and all(char in "0123456789abcdef" for char in value)\n    )\n\n\ndef _portfolio_for_storage(snapshot: PortfolioSnapshot) -> tuple[str, str]:\n    validate_portfolio_snapshot(snapshot)\n    snapshot_json = _portfolio_to_json(snapshot)\n    return snapshot_json, _portfolio_hash(snapshot_json)\n\n\ndef _portfolio_from_storage(*, snapshot_json: object, snapshot_hash: object) -> PortfolioSnapshot:\n    if not isinstance(snapshot_json, str):\n        raise PortfolioIntegrityError("stored portfolio payload is invalid")\n    if not _valid_sha256(snapshot_hash):\n        raise PortfolioIntegrityError("stored portfolio hash is invalid")\n    if _portfolio_hash(snapshot_json) != snapshot_hash:\n        raise PortfolioIntegrityError("stored portfolio hash mismatch")\n    try:\n        return _portfolio_from_json(snapshot_json)\n    except PortfolioIntegrityError:\n        raise\n    except (json.JSONDecodeError, KeyError, TypeError, ValueError, ArithmeticError) as exc:\n        raise PortfolioIntegrityError("stored portfolio payload is invalid") from exc\n\n\ndef _ensure_portfolio_state_integrity_schema(conn: sqlite3.Connection) -> None:\n    columns = {row["name"] for row in conn.execute("PRAGMA table_info(portfolio_state)").fetchall()}\n    if "snapshot_hash" not in columns:\n        conn.execute("ALTER TABLE portfolio_state ADD COLUMN snapshot_hash TEXT")\n    row = conn.execute(\n        "SELECT snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"\n    ).fetchone()\n    if row is None:\n        return\n    if row["snapshot_hash"] is None:\n        # Legacy migration is conservative: semantically parse/validate before\n        # blessing the existing bytes with their first independent commitment.\n        try:\n            snapshot = _portfolio_from_json(row["snapshot_json"])\n            validate_portfolio_snapshot(snapshot)\n        except Exception as exc:\n            raise PortfolioIntegrityError(\n                "legacy portfolio state cannot be integrity-migrated"\n            ) from exc\n        snapshot_hash = _portfolio_hash(row["snapshot_json"])\n        conn.execute(\n            "UPDATE portfolio_state SET snapshot_hash = ? WHERE singleton_id = 1",\n            (snapshot_hash,),\n        )\n        return\n    _portfolio_from_storage(\n        snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]\n    )\n\n\ndef _decimal_map(values: Iterable[tuple[str, Decimal]] | dict[str, Decimal]) -> dict[str, str]:\n    items = values.items() if hasattr(values, "items") else values\n    return {key: str(value) for key, value in items}\n\n\ndef _parse_decimal_map(values: object) -> dict[str, Decimal]:\n    if not isinstance(values, dict):\n        raise ValueError("portfolio decimal map must be object")\n    parsed: dict[str, Decimal] = {}\n    for key, value in values.items():\n        if not isinstance(key, str) or not isinstance(value, str):\n            raise ValueError("portfolio decimal map entries must be string:string")\n        parsed[key] = Decimal(value)\n    return parsed\n'''
text = text[:helper_start] + new_helpers + text[helper_end:]
persistence.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Fill-aware portfolio projection reads/writes the same portfolio commitment.
# ---------------------------------------------------------------------------
execution = ROOT / "src" / "autotrade" / "execution_state.py"
text = execution.read_text(encoding="utf-8")
old_pimport = '''from .persistence import SQLitePortfolioStore, SQLiteRuntime, _portfolio_from_json, _portfolio_to_json\n'''
new_pimport = '''from .persistence import (\n    SQLitePortfolioStore,\n    SQLiteRuntime,\n    _portfolio_for_storage,\n    _portfolio_from_storage,\n)\n'''
if text.count(old_pimport) != 1:
    raise SystemExit("execution portfolio import marker mismatch")
text = text.replace(old_pimport, new_pimport, 1)
old_select = '''            row = conn.execute(\n                "SELECT version, snapshot_json FROM portfolio_state WHERE singleton_id = 1"\n            ).fetchone()\n'''
new_select = '''            row = conn.execute(\n                "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"\n            ).fetchone()\n'''
if text.count(old_select) != 1:
    raise SystemExit(f"fill-aware portfolio select marker mismatch: {text.count(old_select)}")
text = text.replace(old_select, new_select, 1)
old_parse = '''            version = int(row["version"])\n            snapshot = _portfolio_from_json(row["snapshot_json"])\n'''
new_parse = '''            version = int(row["version"])\n            snapshot = _portfolio_from_storage(\n                snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]\n            )\n'''
if text.count(old_parse) != 1:
    raise SystemExit("fill-aware portfolio parse marker mismatch")
text = text.replace(old_parse, new_parse, 1)
old_update = '''            conn.execute(\n                """\n                UPDATE portfolio_state\n                SET version = ?, snapshot_json = ?, updated_at = ?\n                WHERE singleton_id = 1\n                """,\n                (version + 1, _portfolio_to_json(snapshot), now.isoformat()),\n            )\n'''
new_update = '''            snapshot_json, snapshot_hash = _portfolio_for_storage(snapshot)\n            conn.execute(\n                """\n                UPDATE portfolio_state\n                SET version = ?, snapshot_json = ?, snapshot_hash = ?, updated_at = ?\n                WHERE singleton_id = 1\n                """,\n                (version + 1, snapshot_json, snapshot_hash, now.isoformat()),\n            )\n'''
if text.count(old_update) != 1:
    raise SystemExit("fill-aware portfolio update marker mismatch")
text = text.replace(old_update, new_update, 1)
execution.write_text(text, encoding="utf-8")


# Self-clean patch scaffolding.
shutil.rmtree(ROOT / ".r4state", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r4-state-one-shot.yml"
if workflow.exists():
    workflow.unlink()
print("R4 durable state integrity hardening applied")
