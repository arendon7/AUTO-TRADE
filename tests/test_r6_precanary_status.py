from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/r6_precanary_status.py"
SPEC = importlib.util.spec_from_file_location("r6_precanary_status_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
status_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status_cli)

NOW = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)


def _report(**overrides):
    payload = {
        "phase": "PREPARATION_REQUIRED",
        "next_action": "RUN_SEPARATE_OFFLINE_CANARY_PREPARATION",
        "account_attested": True,
        "asset_evidence_present": True,
        "flat_account_evidence_present": True,
        "flat_account_clean_for_first_canary": True,
        "market_evidence_present": True,
        "submission_status": None,
    }
    payload.update(overrides)
    return payload


def _artifacts(**overrides):
    payload = {name: False for name in status_cli._ARTIFACTS}
    payload.update(overrides)
    return payload


def test_classify_walks_safe_gate_sequence_without_ever_authorizing_post() -> None:
    cases = (
        (_report(account_attested=False), _artifacts(), "ACCOUNT_PREFLIGHT_REQUIRED"),
        (_report(asset_evidence_present=False), _artifacts(), "ASSET_PREFLIGHT_REQUIRED"),
        (_report(flat_account_evidence_present=False), _artifacts(), "FLAT_ACCOUNT_PREFLIGHT_REQUIRED"),
        (_report(flat_account_clean_for_first_canary=False), _artifacts(), "PAPER_ACCOUNT_NOT_PROVEN_FLAT"),
        (_report(market_evidence_present=False), _artifacts(), "MARKET_DATA_PREFLIGHT_REQUIRED"),
        (_report(), _artifacts(), "CONNECTIVITY_CANDIDATE_REQUIRED"),
        (_report(), _artifacts(candidate=True), "OFFLINE_PREPARATION_REQUIRED"),
        (_report(), _artifacts(candidate=True, preparation=True), "FIRST_HUMAN_DECISION_REQUIRED"),
        (_report(), _artifacts(candidate=True, preparation=True, first_operator_decision=True), "REVIEW_RECEIPT_REQUIRED"),
        (
            _report(),
            _artifacts(candidate=True, preparation=True, first_operator_decision=True, review_receipt=True),
            "SECOND_HUMAN_EXECUTION_INTENT_REQUIRED",
        ),
        (
            _report(),
            _artifacts(
                candidate=True,
                preparation=True,
                first_operator_decision=True,
                review_receipt=True,
                second_execution_intent=True,
                execution_review_binding=True,
            ),
            "REVIEWED_FINAL_FRESHNESS_REQUIRED",
        ),
    )
    for report, artifacts, expected_stage in cases:
        state, stage, _ = status_cli._classify(report, artifacts, freshness_valid=None)
        assert stage == expected_stage
        assert state != "READY_TO_POST"


def test_unknown_is_reconciliation_only_even_if_all_artifacts_exist() -> None:
    state, stage, action = status_cli._classify(
        _report(phase="RECONCILIATION_REQUIRED", submission_status="UNKNOWN"),
        {name: True for name in status_cli._ARTIFACTS},
        freshness_valid=True,
    )
    assert state == "RECONCILIATION_ONLY"
    assert stage == "BROKER_STATE_AMBIGUOUS_OR_UNKNOWN"
    assert "RECONCILIATION" in action


def test_partial_review_binding_is_blocked() -> None:
    state, stage, _ = status_cli._classify(
        _report(),
        _artifacts(
            candidate=True,
            preparation=True,
            first_operator_decision=True,
            review_receipt=True,
            second_execution_intent=True,
        ),
        freshness_valid=None,
    )
    assert state == "NOT_READY"
    assert stage == "INCOMPLETE_SECOND_HUMAN_BINDING"


def test_expired_reviewed_freshness_is_never_ready_for_runtime() -> None:
    state, stage, action = status_cli._classify(
        _report(),
        _artifacts(
            candidate=True,
            preparation=True,
            first_operator_decision=True,
            review_receipt=True,
            second_execution_intent=True,
            execution_review_binding=True,
            execution_freshness_binding=True,
            reviewed_final_freshness=True,
        ),
        freshness_valid=False,
    )
    assert state == "NOT_READY"
    assert stage == "REVIEWED_FINAL_FRESHNESS_EXPIRED"
    assert "DO_NOT_STAGE_STALE_AUTHORITY" in action


def test_fresh_reviewed_chain_only_reaches_separate_runtime_review() -> None:
    state, stage, action = status_cli._classify(
        _report(),
        _artifacts(
            candidate=True,
            preparation=True,
            first_operator_decision=True,
            review_receipt=True,
            second_execution_intent=True,
            execution_review_binding=True,
            execution_freshness_binding=True,
            reviewed_final_freshness=True,
        ),
        freshness_valid=True,
    )
    assert state == "READY_FOR_SEPARATE_CERTIFIED_RUNTIME_REVIEW"
    assert stage == "REVIEWED_FRESHNESS_PRESENT"
    assert action.startswith("STOP_IN_SAFE_CONSOLE")


def test_status_refuses_enabled_write_or_loaded_credentials(monkeypatch) -> None:
    monkeypatch.setenv(status_cli._WRITE_ENV, "ENABLED")
    with pytest.raises(status_cli.PreCanaryStatusError, match="R6_EXTERNAL_PAPER_WRITE=ENABLED"):
        status_cli._validate_offline_environment()
    monkeypatch.setenv(status_cli._WRITE_ENV, "DISABLED")
    monkeypatch.setenv(status_cli._KEY_ENV, "paper-key")
    with pytest.raises(status_cli.PreCanaryStatusError, match="refuses Alpaca credentials"):
        status_cli._validate_offline_environment()


def test_workspace_rejects_symlink_before_resolution(tmp_path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(status_cli.PreCanaryStatusError, match="non-symlink"):
        status_cli._workspace(link)


def test_workspace_rejects_any_child_symlink(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    (root / "market_snapshot.json").symlink_to(target)
    with pytest.raises(status_cli.PreCanaryStatusError, match="forbidden symlink"):
        status_cli._workspace(root)


def _write_hashed(path: Path, body: dict[str, object], hash_key: str) -> None:
    payload = dict(body)
    payload[hash_key] = status_cli._hash(body)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_artifact_inspection_detects_receipt_tamper_and_freshness_expiry(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    receipt_body = {
        "environment": "PAPER",
        "purpose": "CONNECTIVITY_CANARY",
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "symbol": "AAPL",
    }
    _write_hashed(root / status_cli._ARTIFACTS["review_receipt"], receipt_body, "receipt_hash")

    reviewed_body = {
        "environment": "PAPER",
        "purpose": "CONNECTIVITY_CANARY",
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "operator_review_receipt_hash": "a" * 64,
    }
    _write_hashed(
        root / status_cli._ARTIFACTS["reviewed_final_freshness"],
        reviewed_body,
        "binding_hash",
    )
    freshness = {
        "environment": "PAPER",
        "purpose": "CONNECTIVITY_CANARY",
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "binding": {
            "expires_at": (NOW + timedelta(seconds=2)).isoformat(),
        },
    }
    (root / status_cli._ARTIFACTS["execution_freshness_binding"]).write_text(
        json.dumps(freshness), encoding="utf-8"
    )

    present, valid = status_cli._inspect_artifacts(root, now=NOW)
    assert present["review_receipt"] is True
    assert valid is True
    _, expired = status_cli._inspect_artifacts(root, now=NOW + timedelta(seconds=3))
    assert expired is False

    tampered = json.loads((root / status_cli._ARTIFACTS["review_receipt"]).read_text())
    tampered["symbol"] = "MSFT"
    (root / status_cli._ARTIFACTS["review_receipt"]).write_text(json.dumps(tampered))
    with pytest.raises(status_cli.PreCanaryStatusError, match="receipt hash mismatch"):
        status_cli._inspect_artifacts(root, now=NOW)


def test_build_status_keeps_all_authority_false(monkeypatch, tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(status_cli, "inspect_market_aware_readiness", lambda **_: _report())
    result = status_cli.build_status(root, now=NOW)
    assert result["status"] == "READY_FOR_NEXT_SAFE_GATE"
    assert result["network_used"] is False
    assert result["credentials_used"] is False
    assert result["broker_write_performed"] is False
    assert result["execution_authorized"] is False
    assert result["external_post_authorized"] is False
    assert result["external_order_submitted_by_status"] is False
    assert result["capital_authority"] == "NONE"
    assert result["profitability_claim"] is False
    assert result["live_trading"] == "BLOCKED"
