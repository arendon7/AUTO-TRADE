from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3

import pytest

from autotrade.domain import Fill, Side
from autotrade.execution_state import FillIntegrityConflict, SQLiteFillStore
from autotrade.persistence import (
    SQLitePortfolioStore,
    SQLiteRuntime,
    _portfolio_to_json,
)
from autotrade.portfolio_integrity import PortfolioIntegrityError


def make_fill(now, *, fill_id="fill-r4", order_id="order-r4"):
    return Fill(
        fill_id=fill_id,
        order_id=order_id,
        symbol="TEST-USD",
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        occurred_at=now,
    )


def test_fill_read_cross_checks_hash_and_independent_identity_columns(tmp_path, now):
    runtime = SQLiteRuntime(tmp_path / "fills.db")
    store = SQLiteFillStore(runtime)
    fill = make_fill(now)
    assert store.record(fill) is True
    assert store.fills_for_order(fill.order_id) == (fill,)

    conn = runtime.connect()
    try:
        payload = json.loads(
            conn.execute("SELECT fill_json FROM order_fills WHERE fill_id = ?", (fill.fill_id,)).fetchone()[0]
        )
        payload["price"] = "101"
        conn.execute(
            "UPDATE order_fills SET fill_json = ? WHERE fill_id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), fill.fill_id),
        )
    finally:
        conn.close()

    with pytest.raises(FillIntegrityConflict, match="stored fill hash mismatch"):
        store.fills_for_order(fill.order_id)


def test_fill_duplicate_replay_does_not_ignore_corrupted_existing_payload(tmp_path, now):
    runtime = SQLiteRuntime(tmp_path / "duplicate-fill.db")
    store = SQLiteFillStore(runtime)
    fill = make_fill(now)
    store.record(fill)

    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE order_fills SET fill_json = '{bad-json' WHERE fill_id = ?",
            (fill.fill_id,),
        )
    finally:
        conn.close()

    with pytest.raises(FillIntegrityConflict, match="stored fill payload is invalid"):
        store.record(fill)


def test_fill_read_rejects_hash_column_tamper(tmp_path, now):
    runtime = SQLiteRuntime(tmp_path / "fill-hash.db")
    store = SQLiteFillStore(runtime)
    fill = make_fill(now)
    store.record(fill)

    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE order_fills SET fill_hash = ? WHERE fill_id = ?",
            ("0" * 64, fill.fill_id),
        )
    finally:
        conn.close()

    with pytest.raises(FillIntegrityConflict, match="stored fill hash mismatch"):
        store.fills_for_order(fill.order_id)


def test_fill_read_rejects_rehashed_payload_with_row_identity_mismatch(tmp_path, now):
    runtime = SQLiteRuntime(tmp_path / "fill-row-identity.db")
    store = SQLiteFillStore(runtime)
    fill = make_fill(now)
    store.record(fill)

    conn = runtime.connect()
    try:
        row = conn.execute(
            "SELECT fill_json FROM order_fills WHERE fill_id = ?", (fill.fill_id,)
        ).fetchone()
        payload = json.loads(row["fill_json"])
        payload["order_id"] = "other-order"
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        # Recompute the semantic Fill hash exactly; the independent order_id
        # column must still catch the corruption.
        altered = replace(fill, order_id="other-order")
        from autotrade.execution_state import fill_fingerprint

        conn.execute(
            "UPDATE order_fills SET fill_json = ?, fill_hash = ? WHERE fill_id = ?",
            (raw, fill_fingerprint(altered), fill.fill_id),
        )
    finally:
        conn.close()

    with pytest.raises(FillIntegrityConflict, match="stored order_id column mismatch"):
        store.fills_for_order(fill.order_id)


def test_portfolio_state_persists_independent_hash_and_survives_restart(tmp_path, now, empty_portfolio):
    db = tmp_path / "portfolio.db"
    runtime = SQLiteRuntime(db)
    store = SQLitePortfolioStore(runtime)
    initialized = store.initialize(empty_portfolio, now=now)
    assert initialized.version == 1

    conn = runtime.connect()
    try:
        row = conn.execute(
            "SELECT snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"
        ).fetchone()
        assert row["snapshot_hash"] == sha256(row["snapshot_json"].encode("utf-8")).hexdigest()
    finally:
        conn.close()

    restarted = SQLitePortfolioStore(SQLiteRuntime(db))
    assert restarted.get() == initialized


def test_portfolio_payload_tamper_with_original_hash_fails_closed(tmp_path, now, empty_portfolio):
    runtime = SQLiteRuntime(tmp_path / "portfolio-payload.db")
    store = SQLitePortfolioStore(runtime)
    store.initialize(empty_portfolio, now=now)

    conn = runtime.connect()
    try:
        row = conn.execute("SELECT snapshot_json FROM portfolio_state WHERE singleton_id = 1").fetchone()
        payload = json.loads(row["snapshot_json"])
        payload["equity"] = "99999"
        conn.execute(
            "UPDATE portfolio_state SET snapshot_json = ? WHERE singleton_id = 1",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")),),
        )
    finally:
        conn.close()

    with pytest.raises(PortfolioIntegrityError, match="stored portfolio hash mismatch"):
        store.get()


def test_portfolio_hash_column_tamper_fails_closed(tmp_path, now, empty_portfolio):
    runtime = SQLiteRuntime(tmp_path / "portfolio-hash.db")
    store = SQLitePortfolioStore(runtime)
    store.initialize(empty_portfolio, now=now)
    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE portfolio_state SET snapshot_hash = ? WHERE singleton_id = 1",
            ("0" * 64,),
        )
    finally:
        conn.close()
    with pytest.raises(PortfolioIntegrityError, match="stored portfolio hash mismatch"):
        store.get()


def test_semantically_invalid_portfolio_is_rejected_even_with_matching_hash(tmp_path, now, empty_portfolio):
    runtime = SQLiteRuntime(tmp_path / "portfolio-semantic.db")
    store = SQLitePortfolioStore(runtime)
    store.initialize(empty_portfolio, now=now)

    conn = runtime.connect()
    try:
        row = conn.execute("SELECT snapshot_json FROM portfolio_state WHERE singleton_id = 1").fetchone()
        payload = json.loads(row["snapshot_json"])
        payload["gross_exposure"] = "100"
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        conn.execute(
            "UPDATE portfolio_state SET snapshot_json = ?, snapshot_hash = ? WHERE singleton_id = 1",
            (raw, sha256(raw.encode("utf-8")).hexdigest()),
        )
    finally:
        conn.close()

    with pytest.raises(PortfolioIntegrityError, match="gross_exposure does not match position map"):
        store.get()


def test_invalid_candidate_snapshot_never_overwrites_valid_portfolio(tmp_path, now, empty_portfolio):
    runtime = SQLiteRuntime(tmp_path / "portfolio-cas.db")
    store = SQLitePortfolioStore(runtime)
    current = store.initialize(empty_portfolio, now=now)
    invalid = replace(empty_portfolio, gross_exposure=Decimal("100"))

    with pytest.raises(PortfolioIntegrityError, match="gross_exposure does not match position map"):
        store.compare_and_set(
            expected_version=current.version,
            snapshot=invalid,
            now=now + timedelta(seconds=1),
        )
    assert store.get() == current


def _create_legacy_portfolio_db(path, snapshot_json: str, now) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE portfolio_state (
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                version INTEGER NOT NULL CHECK(version > 0),
                snapshot_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO portfolio_state(singleton_id, version, snapshot_json, updated_at) VALUES (1, 7, ?, ?)",
            (snapshot_json, now.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def test_valid_legacy_portfolio_row_is_semantically_validated_then_hash_migrated(
    tmp_path, now, empty_portfolio
):
    db = tmp_path / "legacy-valid.db"
    raw = _portfolio_to_json(empty_portfolio)
    _create_legacy_portfolio_db(db, raw, now)

    runtime = SQLiteRuntime(db)
    store = SQLitePortfolioStore(runtime)
    current = store.get()
    assert current.version == 7
    assert current.snapshot == empty_portfolio

    conn = runtime.connect()
    try:
        row = conn.execute("SELECT snapshot_hash FROM portfolio_state WHERE singleton_id = 1").fetchone()
        assert row["snapshot_hash"] == sha256(raw.encode("utf-8")).hexdigest()
    finally:
        conn.close()


def test_invalid_legacy_portfolio_row_is_never_blessed_with_new_hash(
    tmp_path, now, empty_portfolio
):
    db = tmp_path / "legacy-invalid.db"
    payload = json.loads(_portfolio_to_json(empty_portfolio))
    payload["gross_exposure"] = "100"
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    _create_legacy_portfolio_db(db, raw, now)

    with pytest.raises(PortfolioIntegrityError, match="legacy portfolio state"):
        SQLiteRuntime(db)


def test_malformed_portfolio_json_fails_closed(tmp_path, now, empty_portfolio):
    runtime = SQLiteRuntime(tmp_path / "portfolio-json.db")
    store = SQLitePortfolioStore(runtime)
    store.initialize(empty_portfolio, now=now)
    conn = runtime.connect()
    try:
        raw = "{bad-json"
        conn.execute(
            "UPDATE portfolio_state SET snapshot_json = ?, snapshot_hash = ? WHERE singleton_id = 1",
            (raw, sha256(raw.encode("utf-8")).hexdigest()),
        )
    finally:
        conn.close()
    with pytest.raises(PortfolioIntegrityError, match="stored portfolio payload is invalid"):
        store.get()
