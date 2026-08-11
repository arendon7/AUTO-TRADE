from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalConflict,
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
    read_prepared_package,
)
from autotrade.brokers.alpaca_paper_operator_decision import PaperOperatorDecisionContext
from test_r6_paper_canary_coordinator import (
    attestation as coordinator_attestation,
    prepare,
    stack,
)


def prepared(tmp_path):
    coordinator, _, _, submission, permit = stack(tmp_path / "prepare")
    return prepare(coordinator, submission, permit)


def workspace_with_account(tmp_path):
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    account = coordinator_attestation()
    workspace.write_account_attestation(account)
    return workspace, account


def test_workspace_writes_only_sanitized_canonical_artifacts(tmp_path) -> None:
    workspace, account = workspace_with_account(tmp_path)
    prepared_result = prepared(tmp_path)
    package_path, context_path, manifest_path = workspace.write_prepared_canary(
        prepared_result.package
    )

    assert read_prepared_package(package_path) == prepared_result.package
    context = PaperOperatorDecisionContext.from_dict(
        json.loads(context_path.read_text(encoding="utf-8"))
    )
    assert context.prepared_package_hash == prepared_result.package.package_hash
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["network_write_authorized"] is False
    assert manifest["next_action"] == "OPERATOR_DECISION_REQUIRED"
    assert manifest["external_order_submitted"] is False
    assert manifest["live_trading"] == "BLOCKED"
    assert manifest["files"]["account_attestation.json"]

    account_payload = json.loads(
        workspace.account_attestation_path.read_text(encoding="utf-8")
    )
    assert account_payload["attestation_fingerprint"] == account.fingerprint
    assert account_payload["credentials_persisted"] is False

    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            workspace.account_attestation_path,
            package_path,
            context_path,
            manifest_path,
        )
    )
    assert "APCA-API-SECRET-KEY" not in combined
    assert "secret_key" not in combined

    if os.name == "posix":
        assert workspace.root.stat().st_mode & 0o777 == 0o700
        for path in (
            workspace.account_attestation_path,
            package_path,
            context_path,
            manifest_path,
        ):
            assert path.stat().st_mode & 0o777 == 0o600


def test_operational_artifact_writes_are_idempotent_but_never_overwrite_conflict(tmp_path) -> None:
    workspace, account = workspace_with_account(tmp_path)
    prepared_result = prepared(tmp_path)
    assert workspace.write_account_attestation(account) == workspace.account_attestation_path
    first = workspace.write_prepared_canary(prepared_result.package)
    second = workspace.write_prepared_canary(prepared_result.package)
    assert second == first

    package_payload = json.loads(workspace.prepared_package_path.read_text(encoding="utf-8"))
    package_payload["attempt_id"] = "tampered-attempt"
    workspace.prepared_package_path.write_text(
        json.dumps(package_payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(PaperOperationalConflict, match="refusing to overwrite"):
        workspace.write_prepared_canary(prepared_result.package)


def test_conflicting_account_attestation_is_never_overwritten(tmp_path) -> None:
    workspace, account = workspace_with_account(tmp_path)
    different = replace(account, request_id="coordinator-account-request-002")
    with pytest.raises(PaperOperationalConflict, match="account_attestation.json"):
        workspace.write_account_attestation(different)


def test_prepared_package_requires_exact_persisted_account_evidence(tmp_path) -> None:
    result = prepared(tmp_path)
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "missing")
    with pytest.raises(PaperOperationalIntegrityError, match="must be persisted before"):
        workspace.write_prepared_canary(result.package)

    mismatch = PaperOperationalWorkspace.initialize(tmp_path / "mismatch")
    mismatch.write_account_attestation(
        replace(coordinator_attestation(), request_id="mismatch-request")
    )
    with pytest.raises(PaperOperationalIntegrityError, match="does not match"):
        mismatch.write_prepared_canary(result.package)


def test_tampered_account_environment_or_credential_persistence_blocks_package(tmp_path) -> None:
    result = prepared(tmp_path)
    for name, field, value, message in (
        ("environment", "environment", "LIVE", "not PAPER"),
        ("credentials", "credentials_persisted", True, "persisted credentials"),
    ):
        workspace, _ = workspace_with_account(tmp_path / name)
        payload = json.loads(workspace.account_attestation_path.read_text(encoding="utf-8"))
        payload[field] = value
        workspace.account_attestation_path.write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        with pytest.raises(PaperOperationalIntegrityError, match=message):
            workspace.write_prepared_canary(result.package)


def test_read_prepared_package_detects_hash_and_noncanonical_tamper(tmp_path) -> None:
    workspace, _ = workspace_with_account(tmp_path)
    result = prepared(tmp_path)
    workspace.write_prepared_canary(result.package)
    raw = json.loads(workspace.prepared_package_path.read_text(encoding="utf-8"))
    raw["package_hash"] = "f" * 64
    workspace.prepared_package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="invalid"):
        read_prepared_package(workspace.prepared_package_path)


def test_read_prepared_package_rejects_noncanonical_extra_field(tmp_path) -> None:
    workspace, _ = workspace_with_account(tmp_path)
    result = prepared(tmp_path)
    workspace.write_prepared_canary(result.package)
    raw = json.loads(workspace.prepared_package_path.read_text(encoding="utf-8"))
    raw["unexpected"] = "field"
    workspace.prepared_package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="not canonical"):
        read_prepared_package(workspace.prepared_package_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("order_id", 123),
        ("risk_decision_safety_state_version", True),
        ("notional", []),
        ("notional", "NaN"),
        ("network_write_authorized", "false"),
        ("prepared_at", "not-a-datetime"),
        ("prepared_at", "2026-08-11T17:45:00"),
    ],
)
def test_read_prepared_package_rejects_invalid_field_shapes(tmp_path, field, value) -> None:
    workspace, _ = workspace_with_account(tmp_path / field)
    result = prepared(tmp_path / field)
    workspace.write_prepared_canary(result.package)
    raw = json.loads(workspace.prepared_package_path.read_text(encoding="utf-8"))
    raw[field] = value
    workspace.prepared_package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="invalid"):
        read_prepared_package(workspace.prepared_package_path)


def test_read_json_artifact_rejects_missing_invalid_or_non_object(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(PaperOperationalIntegrityError, match="cannot read"):
        read_prepared_package(missing)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json", encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="cannot read"):
        read_prepared_package(invalid)

    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="root must be an object"):
        read_prepared_package(array)


def test_package_claiming_network_authority_cannot_be_written(tmp_path) -> None:
    workspace, _ = workspace_with_account(tmp_path)
    result = prepared(tmp_path)
    with pytest.raises(ValueError, match="cannot authorize network write"):
        forged = replace(result.package, network_write_authorized=True)
        workspace.write_prepared_canary(forged)


def test_workspace_rejects_wrong_types_and_symlink_root(tmp_path) -> None:
    with pytest.raises(TypeError, match="pathlib.Path"):
        PaperOperationalWorkspace.initialize(str(tmp_path))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="pathlib.Path"):
        PaperOperationalWorkspace(root="bad")  # type: ignore[arg-type]

    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(actual, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(PaperOperationalIntegrityError, match="symlink"):
        PaperOperationalWorkspace.initialize(link)


def test_workspace_rejects_symlink_artifact_path(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    try:
        workspace.account_attestation_path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(PaperOperationalIntegrityError, match="artifact path cannot be symlink"):
        workspace.write_account_attestation(coordinator_attestation())
