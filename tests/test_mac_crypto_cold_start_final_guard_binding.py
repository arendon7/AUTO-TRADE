from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import mac_crypto_cold_start_final_guard_binding as binding


def _preparation(now: datetime) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "binding_type": "R6_CRYPTO_PAPER_COLD_START_FINAL_GUARD_BINDING_UAT",
        "environment": "PAPER",
        "symbol": "BTC/USD",
        "scope": "FIRST_TECHNICAL_CANARY_ONLY",
        "issued_at": now.isoformat(),
        "valid_until": (now + timedelta(seconds=25)).isoformat(),
        "broker_reads": 15,
        "qualification_attestation_hash": "1" * 64,
        "qualification_attestation_package_hash": "2" * 64,
        "qualification_attestation_valid_until": (now + timedelta(seconds=25)).isoformat(),
        "account_reference": "3" * 64,
        "credential_reference": "4" * 64,
        "portfolio_version": 1,
        "portfolio_snapshot_id": "r6-crypto-paper-cold-start:3" * 1,
        "portfolio_equity": "100000",
        "portfolio_gross_exposure": "0",
        "portfolio_net_exposure": "0",
        "kill_switch_active": True,
        "kill_switch_reason": "R6_HEALTH_R4_EVIDENCE_REQUIRED",
        "kill_switch_reset": False,
        "strategy_health_state_rows": 0,
        "portfolio_health_state_rows": 0,
        "health_bridge_rows": 0,
        "health_missing_expected": True,
        "health_override_authorized": False,
        "normal_health_path_modified": False,
        "binding_package_hash": "5" * 64,
        "binding_payload_hash": "6" * 64,
        "binding_client_order_id": "atr6c-entry-test",
        "binding_notional": "2.00",
        "binding_safety_hard_cap": "5",
        "binding_payload": {"symbol": "BTC/USD", "side": "buy", "type": "limit", "time_in_force": "ioc"},
        "operator_attempt_id": "approval-uat-test",
        "operator_preparation_hash": "7" * 64,
        "operator_context": {"sanitized": True},
        "operator_challenge": "APPROVE CRYPTO PAPER BTC/USD deadbeef0000",
        "operator_decision_recorded": False,
        "operator_decision_consumed": False,
        "protection_required_after_reconciled_fill": True,
        "ambiguity_policy": "UNKNOWN_BEFORE_IO_RECONCILE_ONLY_NO_BLIND_RETRY",
        "qualification_and_binding_packages_are_distinct": True,
        "cold_start_binding_candidate": True,
        "cold_start_binding_sealed": False,
        "normal_final_guard_opened": False,
        "cold_start_final_guard_opened": False,
        "oms_submitting": False,
        "lifecycle_unknown": False,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "execution_authority": "NONE",
        "capital_authority": "NONE",
        "reusable_for_real_execution": False,
        "profitability_evidence": False,
        "live_trading": "BLOCKED",
    }
    document["binding_preparation_hash"] = binding._hash_payload(document, hash_key="binding_preparation_hash")
    return document


def _receipt(now: datetime, preparation: dict[str, object]) -> dict[str, object]:
    return {
        "status": "CRYPTO_PAPER_ONE_SHOT_APPROVAL_RECORDED_UAT",
        "decision_status": "ISSUED",
        "operator_id": "operator-001",
        "attempt_id": preparation["operator_attempt_id"],
        "preparation_hash": preparation["operator_preparation_hash"],
        "decision_hash": "8" * 64,
        "event_hash": "9" * 64,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=20)).isoformat(),
        "decision_consumed": False,
        "uat_only": True,
        "reusable_for_real_execution": False,
        "execution_authority": "NONE",
        "broker_write_performed": False,
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


def test_seal_binding_is_hash_bound_and_non_executable(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    preparation = _preparation(now)
    receipt = _receipt(now, preparation)
    result = binding.seal_binding(
        workspace_path=tmp_path,
        preparation=preparation,
        approval_receipt=receipt,
        now=now + timedelta(seconds=1),
    )
    assert result["status"] == "CRYPTO_COLD_START_FINAL_GUARD_BINDING_SEALED_UAT_NO_EXECUTION"
    assert result["operator_decision_status"] == "ISSUED"
    assert result["operator_decision_consumed"] is False
    assert result["cold_start_final_guard_binding"] is True
    assert result["normal_final_guard_opened"] is False
    assert result["final_guard_pre_consume_authorized"] is False
    assert result["health_override_authorized"] is False
    assert result["kill_switch_active"] is True
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["execution_authority"] == "NONE"
    assert result["capital_authority"] == "NONE"
    assert result["new_execution_approval_required"] is True
    assert result["live_trading"] == "BLOCKED"
    path = Path(str(result["receipt_path"]))
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600


def test_seal_binding_rejects_tampered_preparation(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    preparation = _preparation(now)
    preparation["binding_notional"] = "4.99"
    with pytest.raises(binding.CryptoColdStartFinalGuardBindingError, match="tampered"):
        binding.seal_binding(
            workspace_path=tmp_path,
            preparation=preparation,
            approval_receipt=_receipt(now, preparation),
            now=now + timedelta(seconds=1),
        )


def test_seal_binding_rejects_consumed_or_expired_approval(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    preparation = _preparation(now)
    consumed = _receipt(now, preparation)
    consumed["decision_consumed"] = True
    with pytest.raises(binding.CryptoColdStartFinalGuardBindingError, match="decision_consumed"):
        binding.seal_binding(
            workspace_path=tmp_path,
            preparation=preparation,
            approval_receipt=consumed,
            now=now + timedelta(seconds=1),
        )
    expired = _receipt(now, preparation)
    expired["expires_at"] = (now + timedelta(milliseconds=500)).isoformat()
    with pytest.raises(binding.CryptoColdStartFinalGuardBindingError, match="expired"):
        binding.seal_binding(
            workspace_path=tmp_path,
            preparation=preparation,
            approval_receipt=expired,
            now=now + timedelta(seconds=1),
        )


def test_binding_refuses_external_writer_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    preparation = _preparation(now)
    monkeypatch.setenv(binding.WRITE_ENV, "ENABLED")
    with pytest.raises(binding.CryptoColdStartFinalGuardBindingError, match="refuses"):
        binding.seal_binding(
            workspace_path=tmp_path,
            preparation=preparation,
            approval_receipt=_receipt(now, preparation),
            now=now + timedelta(seconds=1),
        )
