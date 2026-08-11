from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation
from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalConflict,
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
    read_prepared_package,
)
from autotrade.brokers.alpaca_paper_operator_decision import PaperOperatorDecisionContext
from test_r6_paper_canary_coordinator import NOW, h, prepare, stack


def attestation() -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="e6fe16f3-64a4-4921-8928-cadf02f92f98",
        account_reference=h("operational-account-number"),
        credential_reference=h("operational-key-id"),
        status="ACTIVE",
        currency="USD",
        buying_power=__import__("decimal").Decimal("100000"),
        portfolio_value=__import__("decimal").Decimal("100000"),
        shorting_enabled=True,
        attested_at=NOW,
        request_id="operational-request-001",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )


def prepared(tmp_path):
    coordinator, _, _, submission, permit = stack(tmp_path / "prepare")
    return prepare(coordinator, submission, permit)


def test_workspace_writes_only_sanitized_canonical_artifacts(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    prepared_result = prepared(tmp_path)
    workspace.write_account_attestation(attestation())
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
    assert "operational-super-secret" not in combined


def test_operational_artifact_writes_are_idempotent_but_never_overwrite_conflict(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    prepared_result = prepared(tmp_path)
    workspace.write_account_attestation(attestation())
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


def test_read_prepared_package_detects_hash_and_noncanonical_tamper(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    result = prepared(tmp_path)
    workspace.write_prepared_canary(result.package)
    raw = json.loads(workspace.prepared_package_path.read_text(encoding="utf-8"))
    raw["package_hash"] = "f" * 64
    workspace.prepared_package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperOperationalIntegrityError, match="invalid"):
        read_prepared_package(workspace.prepared_package_path)


def test_package_claiming_network_authority_cannot_be_written(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    result = prepared(tmp_path)
    with pytest.raises(ValueError, match="cannot authorize network write"):
        forged = replace(result.package, network_write_authorized=True)
        workspace.write_prepared_canary(forged)


def test_workspace_rejects_symlink_root(tmp_path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(actual, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(PaperOperationalIntegrityError, match="symlink"):
        PaperOperationalWorkspace.initialize(link)
