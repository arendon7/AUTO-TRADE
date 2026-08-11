from __future__ import annotations

from dataclasses import replace
import json

import pytest

from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
)
from autotrade.brokers.alpaca_paper_preparation_snapshot import (
    read_preparation_snapshot,
    snapshot_path,
    write_preparation_snapshot,
)
from test_r6_paper_canary_coordinator import decision, market, prepare, stack


def prepared(tmp_path):
    coordinator, _, _, submission, permit = stack(tmp_path / "coordinator")
    return prepare(coordinator, submission, permit)


def write_snapshot(tmp_path):
    result = prepared(tmp_path)
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    path = write_preparation_snapshot(
        workspace,
        package=result.package,
        decision=decision(),
        market=market(),
        approval=result.approval,
    )
    return workspace, result, path


def test_snapshot_roundtrip_is_exact_and_idempotent(tmp_path) -> None:
    workspace, result, first = write_snapshot(tmp_path)
    second = write_preparation_snapshot(
        workspace,
        package=result.package,
        decision=decision(),
        market=market(),
        approval=result.approval,
    )
    assert second == first
    restored = read_preparation_snapshot(workspace, package=result.package)
    assert restored == (decision(), market(), result.approval)
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["credentials_persisted"] is False
    assert payload["network_write_authorized"] is False
    assert payload["next_action"] == "OPERATOR_DECISION_REQUIRED"
    assert payload["live_trading"] == "BLOCKED"


def test_snapshot_rejects_changed_market_decision_or_approval_before_write(tmp_path) -> None:
    result = prepared(tmp_path)
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    with pytest.raises(PaperOperationalIntegrityError, match="MarketSnapshot"):
        write_preparation_snapshot(
            workspace,
            package=result.package,
            decision=decision(),
            market=replace(market(), last=market().last + 1),
            approval=result.approval,
        )
    with pytest.raises(PaperOperationalIntegrityError, match="Safety version"):
        write_preparation_snapshot(
            workspace,
            package=result.package,
            decision=replace(decision(), safety_state_version=1),
            market=market(),
            approval=result.approval,
        )
    with pytest.raises(PaperOperationalIntegrityError, match="notional"):
        write_preparation_snapshot(
            workspace,
            package=result.package,
            decision=decision(),
            market=market(),
            approval=replace(result.approval, notional=result.approval.notional + 1),
        )


def test_snapshot_hash_authority_and_package_tamper_fail_closed(tmp_path) -> None:
    workspace, result, path = write_snapshot(tmp_path)
    for field, value, message in (
        ("snapshot_hash", "f" * 64, "hash mismatch"),
        ("network_write_authorized", True, "cannot authorize"),
        ("live_trading", "ENABLED", "cannot unblock"),
    ):
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw[field] = value
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PaperOperationalIntegrityError, match=message):
            read_preparation_snapshot(workspace, package=result.package)
        path.unlink()
        write_preparation_snapshot(
            workspace,
            package=result.package,
            decision=decision(),
            market=market(),
            approval=result.approval,
        )

    wrong_package = replace(result.package, package_hash="f" * 64)
    with pytest.raises((ValueError, PaperOperationalIntegrityError)):
        read_preparation_snapshot(workspace, package=wrong_package)


def test_snapshot_refuses_conflicting_overwrite_and_symlink(tmp_path) -> None:
    workspace, result, path = write_snapshot(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["unexpected"] = True
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="overwrite"):
        write_preparation_snapshot(
            workspace,
            package=result.package,
            decision=decision(),
            market=market(),
            approval=result.approval,
        )

    path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    try:
        snapshot_path(workspace).symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(PaperOperationalIntegrityError, match="symlink"):
        write_preparation_snapshot(
            workspace,
            package=result.package,
            decision=decision(),
            market=market(),
            approval=result.approval,
        )
