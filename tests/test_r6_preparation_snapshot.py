from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
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


def artifact_hash(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()


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


def test_snapshot_binds_full_risk_decision_identity(tmp_path) -> None:
    result = prepared(tmp_path)
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    for forged in (
        replace(decision(), approved_notional=decision().approved_notional + 1),
        replace(decision(), reason_detail="forged reason"),
        replace(decision(), limits_version="forged-limits"),
    ):
        with pytest.raises(PaperOperationalIntegrityError, match="fingerprint"):
            write_preparation_snapshot(
                workspace,
                package=result.package,
                decision=forged,
                market=market(),
                approval=result.approval,
            )


def test_snapshot_rejects_malformed_and_wrong_authority_artifacts(tmp_path) -> None:
    workspace, result, path = write_snapshot(tmp_path)
    mutations = (
        ({"schema_version": 2}, "header"),
        ({"environment": "LIVE"}, "header"),
        ({"credentials_persisted": True}, "persist credentials"),
        ({"next_action": "EXECUTE"}, "action changed"),
    )
    original = json.loads(path.read_text(encoding="utf-8"))
    for changed, message in mutations:
        raw = dict(original)
        raw.update(changed)
        without_hash = dict(raw)
        without_hash.pop("snapshot_hash", None)
        raw["snapshot_hash"] = artifact_hash(without_hash)
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PaperOperationalIntegrityError, match=message):
            read_preparation_snapshot(workspace, package=result.package)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="root must be object"):
        read_preparation_snapshot(workspace, package=result.package)
    path.write_text("{bad-json", encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="cannot read"):
        read_preparation_snapshot(workspace, package=result.package)


def test_snapshot_rejects_nested_shape_and_field_tamper(tmp_path) -> None:
    workspace, result, path = write_snapshot(tmp_path)
    original = json.loads(path.read_text(encoding="utf-8"))
    cases = [
        ("risk_decision", None, "risk_decision must be object"),
        ("market", [], "market must be object"),
        ("approval", "bad", "approval must be object"),
    ]
    for field, value, message in cases:
        raw = dict(original)
        raw[field] = value
        without_hash = dict(raw)
        without_hash.pop("snapshot_hash", None)
        raw["snapshot_hash"] = artifact_hash(without_hash)
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PaperOperationalIntegrityError, match=message):
            read_preparation_snapshot(workspace, package=result.package)

    raw = json.loads(json.dumps(original))
    raw["risk_decision"]["reason_detail"] = "tampered after preparation"
    without_hash = dict(raw)
    without_hash.pop("snapshot_hash", None)
    raw["snapshot_hash"] = artifact_hash(without_hash)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="RiskDecision fingerprint"):
        read_preparation_snapshot(workspace, package=result.package)

    raw = json.loads(json.dumps(original))
    raw["market"]["market_fingerprint"] = "f" * 64
    without_hash = dict(raw)
    without_hash.pop("snapshot_hash", None)
    raw["snapshot_hash"] = artifact_hash(without_hash)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="MarketSnapshot fingerprint"):
        read_preparation_snapshot(workspace, package=result.package)


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

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["package_hash"] = "f" * 64
    without_hash = dict(raw)
    without_hash.pop("snapshot_hash")
    raw["snapshot_hash"] = artifact_hash(without_hash)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="package mismatch"):
        read_preparation_snapshot(workspace, package=result.package)


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
