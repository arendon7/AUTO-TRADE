from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from autotrade.paper_close_attempt import (
    PaperCloseAttemptConflict,
    PaperCloseAttemptIntegrityError,
    PaperCloseAttemptWorkspace,
    paper_close_plan_from_dict,
    pending_burned_close_attempts,
)
from autotrade.paper_close_lifecycle import SQLitePaperCloseLifecycle
from autotrade.paper_close_plan import PaperCloseMode, PaperCryptoClosePlan
from autotrade.persistence import SQLiteRuntime


NOW = datetime(2026, 8, 21, 22, 30, tzinfo=timezone.utc)


def _plan(*, suffix: str = "a") -> PaperCryptoClosePlan:
    values = {
        "account_reference": "1" * 64,
        "credential_reference": "2" * 64,
        "portfolio_fingerprint": suffix * 64,
        "broker_symbol": "BTCUSD",
        "symbol": "BTC/USD",
        "asset_class": "crypto",
        "mode": PaperCloseMode.FULL,
        "side": "sell",
        "quantity": Decimal("0.000143959"),
        "observed_position_quantity": Decimal("0.000143959"),
        "observed_available_quantity": Decimal("0.000143959"),
        "reference_price": Decimal("72800"),
        "limit_price": Decimal("72781.8"),
        "max_slippage_bps": Decimal("25"),
        "order_type": "limit",
        "time_in_force": "ioc",
        "prepared_at": NOW,
        "expires_at": NOW + timedelta(seconds=15),
        "risk_reducing": True,
        "network_write_authorized": False,
        "retry_post": False,
        "live_trading": "BLOCKED",
    }
    from hashlib import sha256

    payload = dict(values)
    payload["mode"] = PaperCloseMode.FULL.value
    for key in (
        "quantity",
        "observed_position_quantity",
        "observed_available_quantity",
        "reference_price",
        "limit_price",
        "max_slippage_bps",
    ):
        text = format(payload[key], "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        payload[key] = text or "0"
    for key in ("prepared_at", "expires_at"):
        payload[key] = payload[key].astimezone(timezone.utc).isoformat()
    plan_hash = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return PaperCryptoClosePlan(**values, plan_hash=plan_hash)


def _attempt(tmp_path: Path, token: str = "1") -> PaperCloseAttemptWorkspace:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    return PaperCloseAttemptWorkspace.create(
        workspace_path=workspace,
        attempt_id="r7-close-" + token * 32,
    )


def test_plan_round_trip_is_hash_sealed_and_private(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    plan = _plan()
    attempt.write_plan(plan)
    assert attempt.read_plan() == plan
    assert paper_close_plan_from_dict(json.loads(attempt.plan_path.read_text())) == plan
    assert attempt.plan_path.stat().st_mode & 0o777 == 0o600
    assert attempt.root.stat().st_mode & 0o777 == 0o700


def test_write_once_is_idempotent_only_for_exact_same_bytes(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    attempt.write_plan(_plan())
    attempt.write_plan(_plan())
    other = _plan(suffix="b")
    with pytest.raises(PaperCloseAttemptConflict, match="different content"):
        attempt.write_plan(other)


def test_receipt_refuses_any_credential_material(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    with pytest.raises(PaperCloseAttemptIntegrityError, match="may not persist credentials"):
        attempt.write_receipt({"paper_secret": "never-store-this"})


def test_prepared_zero_submit_attempt_is_not_burned(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    plan = _plan()
    attempt.write_plan(plan)
    lifecycle = SQLitePaperCloseLifecycle(SQLiteRuntime(attempt.database_path))
    lifecycle.prepare(attempt_id=attempt.attempt_id, plan=plan, at=NOW)
    assert pending_burned_close_attempts(workspace_path=attempt.workspace_root) == ()


def test_unknown_attempt_is_discovered_restart_safe_read_only(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    plan = _plan()
    attempt.write_plan(plan)
    lifecycle = SQLitePaperCloseLifecycle(SQLiteRuntime(attempt.database_path))
    lifecycle.prepare(attempt_id=attempt.attempt_id, plan=plan, at=NOW)
    lifecycle.mark_submission_unknown(attempt.attempt_id, at=NOW + timedelta(milliseconds=1))
    before = attempt.database_path.read_bytes()
    assert pending_burned_close_attempts(workspace_path=attempt.workspace_root) == (attempt.attempt_id,)
    assert attempt.database_path.read_bytes() == before


def test_terminal_flat_attempt_is_not_pending(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    plan = _plan()
    attempt.write_plan(plan)
    lifecycle = SQLitePaperCloseLifecycle(SQLiteRuntime(attempt.database_path))
    lifecycle.prepare(attempt_id=attempt.attempt_id, plan=plan, at=NOW)
    lifecycle.mark_submission_unknown(attempt.attempt_id, at=NOW + timedelta(milliseconds=1))
    lifecycle.reconcile(
        attempt.attempt_id,
        broker_order_id="broker-close-1",
        broker_status="filled",
        filled_quantity=plan.quantity,
        remaining_position=Decimal("0"),
        at=NOW + timedelta(milliseconds=2),
    )
    assert pending_burned_close_attempts(workspace_path=attempt.workspace_root) == ()


def test_terminal_partial_or_canceled_attempt_is_terminal_not_retryable(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    plan = _plan()
    attempt.write_plan(plan)
    lifecycle = SQLitePaperCloseLifecycle(SQLiteRuntime(attempt.database_path))
    lifecycle.prepare(attempt_id=attempt.attempt_id, plan=plan, at=NOW)
    lifecycle.mark_submission_unknown(attempt.attempt_id, at=NOW + timedelta(milliseconds=1))
    lifecycle.reconcile(
        attempt.attempt_id,
        broker_order_id="broker-close-2",
        broker_status="canceled",
        filled_quantity=Decimal("0.00004"),
        remaining_position=Decimal("0.000103959"),
        at=NOW + timedelta(milliseconds=2),
    )
    state = lifecycle.snapshot(attempt.attempt_id).state
    assert state.submission_attempt_count == 1
    assert state.retry_post is False
    assert pending_burned_close_attempts(workspace_path=attempt.workspace_root) == ()


def test_multiple_burned_attempts_are_reported_not_collapsed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    ids = []
    for token, suffix in (("1", "a"), ("2", "b")):
        attempt = PaperCloseAttemptWorkspace.create(
            workspace_path=workspace,
            attempt_id="r7-close-" + token * 32,
        )
        plan = _plan(suffix=suffix)
        attempt.write_plan(plan)
        lifecycle = SQLitePaperCloseLifecycle(SQLiteRuntime(attempt.database_path))
        lifecycle.prepare(attempt_id=attempt.attempt_id, plan=plan, at=NOW)
        lifecycle.mark_submission_unknown(attempt.attempt_id, at=NOW + timedelta(milliseconds=1))
        ids.append(attempt.attempt_id)
    assert pending_burned_close_attempts(workspace_path=workspace) == tuple(ids)


def test_symlink_attempt_path_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    close_root = workspace / "r7_paper_close"
    close_root.mkdir(mode=0o700)
    target = tmp_path / "outside"
    target.mkdir()
    (close_root / ("r7-close-" + "1" * 32)).symlink_to(target, target_is_directory=True)
    with pytest.raises(PaperCloseAttemptIntegrityError, match="unsafe attempt path"):
        pending_burned_close_attempts(workspace_path=workspace)


def test_tampered_plan_hash_is_rejected_on_read(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    attempt.write_plan(_plan())
    document = json.loads(attempt.plan_path.read_text())
    document["quantity"] = "0.0001"
    attempt.plan_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(PaperCloseAttemptIntegrityError, match="plan artifact is invalid"):
        attempt.read_plan()
