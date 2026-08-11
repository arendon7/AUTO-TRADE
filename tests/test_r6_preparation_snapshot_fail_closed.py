from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
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
from test_r6_paper_canary_coordinator import NOW, decision, market, prepare, stack


def setup_snapshot(tmp_path):
    coordinator, _, _, submission, permit = stack(tmp_path / "coordinator")
    result = prepare(coordinator, submission, permit)
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    path = write_preparation_snapshot(
        workspace,
        package=result.package,
        decision=decision(),
        market=market(),
        approval=result.approval,
    )
    return workspace, result, path


def rehash(raw: dict[str, object]) -> None:
    payload = dict(raw)
    payload.pop("snapshot_hash", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    raw["snapshot_hash"] = sha256(encoded).hexdigest()


def write_raw(path, raw: dict[str, object]) -> None:
    rehash(raw)
    path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")


def test_snapshot_path_requires_operational_workspace(tmp_path) -> None:
    with pytest.raises(TypeError, match="operational workspace"):
        snapshot_path(object())  # type: ignore[arg-type]

    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    assert snapshot_path(workspace) == workspace.root / "preparation_snapshot.json"


def test_snapshot_read_rejects_symlink_and_missing_hash_type(tmp_path) -> None:
    workspace, result, path = setup_snapshot(tmp_path)
    path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    try:
        path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(PaperOperationalIntegrityError, match="symlink"):
        read_preparation_snapshot(workspace, package=result.package)

    path.unlink()
    path = write_preparation_snapshot(
        workspace,
        package=result.package,
        decision=decision(),
        market=market(),
        approval=result.approval,
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["snapshot_hash"] = None
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="hash is missing"):
        read_preparation_snapshot(workspace, package=result.package)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("risk_decision", "decision_id", 123),
        ("risk_decision", "status", "NOT_A_STATUS"),
        ("risk_decision", "risk_reducing", "false"),
        ("risk_decision", "safety_state_version", True),
        ("risk_decision", "approved_notional", []),
        ("risk_decision", "approved_notional", "NaN"),
        ("risk_decision", "evaluated_at", "not-a-datetime"),
        ("risk_decision", "evaluated_at", "2026-08-11T20:00:00"),
        ("market", "bid", []),
        ("market", "bid", "NaN"),
        ("market", "observed_at", "2026-08-11T20:00:00"),
        ("approval", "notional", []),
        ("approval", "notional", "NaN"),
        ("approval", "expires_at", "bad-time"),
        ("approval", "approval_hash", 123),
    ],
)
def test_snapshot_rejects_malformed_nested_scalar_fields(
    tmp_path, section, field, value
) -> None:
    workspace, result, path = setup_snapshot(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw[section][field] = value
    write_raw(path, raw)
    with pytest.raises(PaperOperationalIntegrityError, match="snapshot is invalid"):
        read_preparation_snapshot(workspace, package=result.package)


def test_snapshot_rejects_noncanonical_extra_field_even_with_valid_hash(tmp_path) -> None:
    workspace, result, path = setup_snapshot(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["unexpected"] = "extra"
    write_raw(path, raw)
    with pytest.raises(PaperOperationalIntegrityError, match="not canonical"):
        read_preparation_snapshot(workspace, package=result.package)


@pytest.mark.parametrize(
    ("decision_change", "approval_change", "message"),
    [
        ({"decision_id": "different-decision"}, {}, "RiskDecision id"),
        ({"intent_fingerprint": "f" * 64}, {}, "RiskDecision intent"),
        ({"market_fingerprint": "e" * 64}, {}, "RiskDecision market"),
        ({"valid_until": NOW + timedelta(seconds=99)}, {}, "RiskDecision expiry"),
        ({}, {"approval_hash": "f" * 64}, "canary approval differs"),
        ({}, {"order_id": "different-order"}, "order identity"),
        ({}, {"client_order_id": "different-client"}, "order identity"),
        ({}, {"binding_hash": "f" * 64}, "binding differs"),
        ({}, {"account_attestation_fingerprint": "f" * 64}, "account differs"),
        ({}, {"risk_decision_id": "different-risk"}, "RiskDecision differs"),
        ({}, {"effective_notional_cap": 9}, "notional differs"),
        ({}, {"issued_at": NOW - timedelta(seconds=1)}, "validity window"),
        ({}, {"expires_at": NOW + timedelta(seconds=99)}, "validity window"),
    ],
)
def test_snapshot_prewrite_rejects_every_package_binding_mismatch(
    tmp_path, decision_change, approval_change, message
) -> None:
    coordinator, _, _, submission, permit = stack(tmp_path / "coordinator")
    result = prepare(coordinator, submission, permit)
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    current_decision = replace(decision(), **decision_change) if decision_change else decision()
    normalized_approval_change = {
        key: (value if not isinstance(value, int) else result.approval.effective_notional_cap - 1)
        for key, value in approval_change.items()
    }
    current_approval = (
        replace(result.approval, **normalized_approval_change)
        if normalized_approval_change
        else result.approval
    )
    with pytest.raises(PaperOperationalIntegrityError, match=message):
        write_preparation_snapshot(
            workspace,
            package=result.package,
            decision=current_decision,
            market=market(),
            approval=current_approval,
        )
