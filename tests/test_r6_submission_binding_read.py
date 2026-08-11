from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionBinding,
    PaperSubmissionIntegrityError,
    SQLitePaperSubmissionRegistry,
)
from autotrade.persistence import SQLiteRuntime


NOW = datetime(2026, 8, 11, 14, 50, tzinfo=timezone.utc)


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def binding() -> PaperSubmissionBinding:
    return PaperSubmissionBinding(
        order_id="binding-read-order-001",
        client_order_id="autotrade-binding-read-001",
        intent_id="binding-read-intent-001",
        intent_fingerprint=h("intent"),
        risk_decision_id="binding-read-risk-001",
        account_attestation_fingerprint=h("account"),
        order_payload_hash=h("payload"),
        created_at=NOW,
    )


def test_get_binding_is_durable_verified_read(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "binding-read.sqlite")
    registry = SQLitePaperSubmissionRegistry(runtime)
    frozen = binding()
    registry.prepare(frozen)
    assert registry.get_binding(frozen.order_id) == frozen
    assert SQLitePaperSubmissionRegistry(runtime).get_binding(frozen.order_id) == frozen


def test_get_binding_detects_binding_tamper(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "binding-tamper.sqlite")
    registry = SQLitePaperSubmissionRegistry(runtime)
    frozen = binding()
    registry.prepare(frozen)
    with sqlite3.connect(runtime.path) as conn:
        conn.execute(
            "UPDATE alpaca_paper_submission_bindings SET binding_json=? WHERE order_id=?",
            ('{}', frozen.order_id),
        )
        conn.commit()
    with pytest.raises(PaperSubmissionIntegrityError):
        registry.get_binding(frozen.order_id)
