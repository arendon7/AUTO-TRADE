from dataclasses import replace
from hashlib import sha256
import json
import sqlite3

import pytest

from autotrade.research.oss2_final_holdout_protocol import (
    OSS2G_CONTRACT_VERSION,
    OSS2FinalHoldoutProtocolConflict,
    OSS2FinalHoldoutProtocolGovernanceError,
    OSS2FinalHoldoutProtocolIntegrityError,
    OSS2FinalHoldoutProtocolPolicy,
    SQLiteOSS2FinalHoldoutProtocolRegistry,
    canonical_oss2g_policy,
    read_oss2g_protocol_read_only,
)
from autotrade.research.oss2_holdout_freeze import (
    OSS2F_CONTRACT_VERSION,
    OSS2HoldoutFreezeReceipt,
    OSS2HoldoutFreezeState,
)


H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def freeze_receipt(
    *,
    campaign_id: str = "oss2-campaign",
    receipt_id: str = "freeze-001",
    decision: OSS2HoldoutFreezeState = OSS2HoldoutFreezeState.HOLDOUT_ELIGIBLE,
) -> OSS2HoldoutFreezeReceipt:
    failed = () if decision is OSS2HoldoutFreezeState.HOLDOUT_ELIGIBLE else ("PBO_MAX",)
    payload = {
        "receipt_id": receipt_id,
        "contract_version": OSS2F_CONTRACT_VERSION,
        "campaign_id": campaign_id,
        "selected_trial_id": "trial-winner",
        "oss2d_evidence_fingerprint": H1,
        "oss2e_policy_fingerprint": H2,
        "oss2e_evidence_fingerprint": H3,
        "candidate_freeze_fingerprint": H4,
        "decision": decision.value,
        "failed_gate_ids": list(failed),
        "final_holdout_observed": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS2HoldoutFreezeReceipt(
        receipt_id=receipt_id,
        contract_version=OSS2F_CONTRACT_VERSION,
        campaign_id=campaign_id,
        selected_trial_id="trial-winner",
        oss2d_evidence_fingerprint=H1,
        oss2e_policy_fingerprint=H2,
        oss2e_evidence_fingerprint=H3,
        candidate_freeze_fingerprint=H4,
        decision=decision,
        failed_gate_ids=failed,
        final_holdout_observed=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
        receipt_hash=_hash(payload),
    )


def test_canonical_policy_is_frozen_before_holdout():
    policy = canonical_oss2g_policy()
    assert policy.min_net_return == 0.0
    assert policy.min_sharpe == 0.0
    assert policy.max_drawdown == 0.35
    assert policy.max_evaluations == 1
    assert policy.retuning_allowed is False
    assert policy.reselection_allowed is False
    assert policy.second_attempt_allowed is False
    assert policy.failure_is_terminal is True
    assert policy.split_name == "FINAL_HOLDOUT"
    assert policy.permit_purpose == "final_validation"
    assert len(policy.fingerprint) == 64


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_evaluations": 2},
        {"retuning_allowed": True},
        {"reselection_allowed": True},
        {"second_attempt_allowed": True},
        {"failure_is_terminal": False},
        {"split_name": "DEVELOPMENT"},
        {"permit_purpose": "research"},
        {"max_drawdown": 1.1},
    ],
)
def test_policy_rejects_authority_or_threshold_drift(kwargs):
    with pytest.raises(ValueError):
        OSS2FinalHoldoutProtocolPolicy(**kwargs)


def test_eligible_freeze_preregisters_durable_protocol(tmp_path):
    db = tmp_path / "oss2g.sqlite3"
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(db)
    receipt = registry.preregister_and_record(
        protocol_id="protocol-001",
        freeze=freeze_receipt(),
    )

    assert receipt.contract_version == OSS2G_CONTRACT_VERSION
    assert receipt.campaign_id == "oss2-campaign"
    assert receipt.selected_trial_id == "trial-winner"
    assert receipt.freeze_receipt_id == "freeze-001"
    assert receipt.max_evaluations == 1
    assert receipt.final_holdout_observed is False
    assert receipt.final_holdout_consumed is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert receipt.holdout_authorization_id.startswith("oss2g:")
    assert receipt.gate_specification == (
        ("FINAL_NET_RETURN_MIN", ">=", 0.0),
        ("FINAL_SHARPE_MIN", ">=", 0.0),
        ("FINAL_DRAWDOWN_MAX", "<=", 0.35),
    )
    assert len(receipt.receipt_hash) == 64
    assert registry.get_for_campaign("oss2-campaign") == receipt
    assert read_oss2g_protocol_read_only(db, campaign_id="oss2-campaign") == receipt
    assert read_oss2g_protocol_read_only(db, campaign_id="unknown") is None


def test_rejected_freeze_cannot_preregister_protocol(tmp_path):
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(tmp_path / "oss2g.sqlite3")
    with pytest.raises(OSS2FinalHoldoutProtocolGovernanceError, match="HOLDOUT_ELIGIBLE"):
        registry.preregister_and_record(
            protocol_id="protocol-rejected",
            freeze=freeze_receipt(decision=OSS2HoldoutFreezeState.REJECT),
        )


def test_identical_protocol_preregistration_is_idempotent(tmp_path):
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(tmp_path / "oss2g.sqlite3")
    freeze = freeze_receipt()
    first = registry.preregister_and_record(protocol_id="protocol-001", freeze=freeze)
    second = registry.preregister_and_record(protocol_id="protocol-001", freeze=freeze)
    assert second == first


def test_same_campaign_cannot_change_protocol_id(tmp_path):
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(tmp_path / "oss2g.sqlite3")
    freeze = freeze_receipt()
    registry.preregister_and_record(protocol_id="protocol-001", freeze=freeze)
    with pytest.raises(OSS2FinalHoldoutProtocolConflict, match="different frozen protocol"):
        registry.preregister_and_record(protocol_id="protocol-002", freeze=freeze)


def test_protocol_id_cannot_be_reused_for_other_campaign(tmp_path):
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(tmp_path / "oss2g.sqlite3")
    registry.preregister_and_record(
        protocol_id="protocol-shared",
        freeze=freeze_receipt(campaign_id="campaign-a", receipt_id="freeze-a"),
    )
    with pytest.raises(OSS2FinalHoldoutProtocolConflict, match="another campaign"):
        registry.preregister_and_record(
            protocol_id="protocol-shared",
            freeze=freeze_receipt(campaign_id="campaign-b", receipt_id="freeze-b"),
        )


def test_freeze_receipt_cannot_back_multiple_protocols(tmp_path):
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(tmp_path / "oss2g.sqlite3")
    freeze = freeze_receipt(campaign_id="campaign-a", receipt_id="freeze-shared")
    registry.preregister_and_record(protocol_id="protocol-a", freeze=freeze)
    alternate = freeze_receipt(campaign_id="campaign-b", receipt_id="freeze-shared")
    with pytest.raises(OSS2FinalHoldoutProtocolConflict):
        registry.preregister_and_record(protocol_id="protocol-b", freeze=alternate)


def test_authorization_identity_is_deterministic_and_candidate_bound(tmp_path):
    registry_a = SQLiteOSS2FinalHoldoutProtocolRegistry(tmp_path / "a.sqlite3")
    registry_b = SQLiteOSS2FinalHoldoutProtocolRegistry(tmp_path / "b.sqlite3")
    first = registry_a.preregister_and_record(
        protocol_id="protocol-001", freeze=freeze_receipt()
    )
    second = registry_b.preregister_and_record(
        protocol_id="protocol-001", freeze=freeze_receipt()
    )
    assert first.holdout_authorization_id == second.holdout_authorization_id
    assert first.receipt_hash == second.receipt_hash


def test_sqlite_registry_is_physically_append_only(tmp_path):
    db = tmp_path / "oss2g.sqlite3"
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(db)
    registry.preregister_and_record(protocol_id="protocol-001", freeze=freeze_receipt())
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE oss2_final_holdout_protocols SET protocol_id='changed' "
                "WHERE campaign_id='oss2-campaign'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM oss2_final_holdout_protocols WHERE campaign_id='oss2-campaign'"
            )
    finally:
        conn.close()


def test_read_only_reader_requires_existing_registry(tmp_path):
    with pytest.raises(OSS2FinalHoldoutProtocolIntegrityError, match="does not exist"):
        read_oss2g_protocol_read_only(
            tmp_path / "missing.sqlite3", campaign_id="oss2-campaign"
        )


def test_read_only_reader_requires_protocol_table(tmp_path):
    db = tmp_path / "empty.sqlite3"
    sqlite3.connect(db).close()
    with pytest.raises(OSS2FinalHoldoutProtocolIntegrityError, match="table is missing"):
        read_oss2g_protocol_read_only(db, campaign_id="oss2-campaign")


def _tamper_json(db, mutator):
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TRIGGER oss2_final_holdout_protocols_no_update")
        raw = conn.execute(
            "SELECT receipt_json FROM oss2_final_holdout_protocols "
            "WHERE campaign_id='oss2-campaign'"
        ).fetchone()[0]
        payload = json.loads(raw)
        mutator(payload)
        conn.execute(
            "UPDATE oss2_final_holdout_protocols SET receipt_json=? "
            "WHERE campaign_id='oss2-campaign'",
            (_canonical_json(payload),),
        )
        conn.commit()
    finally:
        conn.close()


def test_reader_rejects_policy_drift_inside_json(tmp_path):
    db = tmp_path / "oss2g.sqlite3"
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(db)
    registry.preregister_and_record(protocol_id="protocol-001", freeze=freeze_receipt())
    _tamper_json(db, lambda payload: payload.__setitem__("max_drawdown", 0.99))
    with pytest.raises(OSS2FinalHoldoutProtocolIntegrityError, match="policy fields drifted"):
        read_oss2g_protocol_read_only(db, campaign_id="oss2-campaign")


def test_reader_rejects_attempt_to_mark_holdout_observed(tmp_path):
    db = tmp_path / "oss2g.sqlite3"
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(db)
    registry.preregister_and_record(protocol_id="protocol-001", freeze=freeze_receipt())
    _tamper_json(db, lambda payload: payload.__setitem__("final_holdout_observed", True))
    with pytest.raises(OSS2FinalHoldoutProtocolIntegrityError, match="observe or consume"):
        read_oss2g_protocol_read_only(db, campaign_id="oss2-campaign")


def test_reader_rejects_second_attempt_authority(tmp_path):
    db = tmp_path / "oss2g.sqlite3"
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(db)
    registry.preregister_and_record(protocol_id="protocol-001", freeze=freeze_receipt())
    _tamper_json(db, lambda payload: payload.__setitem__("second_attempt_allowed", True))
    with pytest.raises(OSS2FinalHoldoutProtocolIntegrityError, match="policy fields drifted"):
        read_oss2g_protocol_read_only(db, campaign_id="oss2-campaign")


def test_reader_rejects_authorization_id_corruption(tmp_path):
    db = tmp_path / "oss2g.sqlite3"
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(db)
    registry.preregister_and_record(protocol_id="protocol-001", freeze=freeze_receipt())
    _tamper_json(db, lambda payload: payload.__setitem__("holdout_authorization_id", "oss2g:wrong"))
    with pytest.raises(OSS2FinalHoldoutProtocolIntegrityError, match="authorization identity"):
        read_oss2g_protocol_read_only(db, campaign_id="oss2-campaign")


def test_reader_rejects_side_column_corruption(tmp_path):
    db = tmp_path / "oss2g.sqlite3"
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(db)
    registry.preregister_and_record(protocol_id="protocol-001", freeze=freeze_receipt())
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TRIGGER oss2_final_holdout_protocols_no_update")
        conn.execute(
            "UPDATE oss2_final_holdout_protocols SET selected_trial_id='other-trial' "
            "WHERE campaign_id='oss2-campaign'"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(OSS2FinalHoldoutProtocolIntegrityError, match="side-column mismatch"):
        read_oss2g_protocol_read_only(db, campaign_id="oss2-campaign")


def test_receipt_cannot_be_reconstructed_with_live_or_capital_authority(tmp_path):
    registry = SQLiteOSS2FinalHoldoutProtocolRegistry(tmp_path / "oss2g.sqlite3")
    receipt = registry.preregister_and_record(
        protocol_id="protocol-001", freeze=freeze_receipt()
    )
    with pytest.raises(OSS2FinalHoldoutProtocolIntegrityError, match="capital or LIVE"):
        replace(receipt, capital_authority="USD_5")
    with pytest.raises(OSS2FinalHoldoutProtocolIntegrityError, match="capital or LIVE"):
        replace(receipt, live_trading="ENABLED")
