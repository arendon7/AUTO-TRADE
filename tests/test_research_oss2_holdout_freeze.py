from dataclasses import replace
from decimal import Decimal
import json
import sqlite3

import pytest

from autotrade.research.multiple_testing import DeflatedSharpeEvidence, PBOEvidence
from autotrade.research.oss2_holdout_freeze import (
    OSS2F_CONTRACT_VERSION,
    OSS2HoldoutFreezeConflict,
    OSS2HoldoutFreezeIntegrityError,
    OSS2HoldoutFreezeReceipt,
    OSS2HoldoutFreezeState,
    SQLiteOSS2HoldoutFreezeRegistry,
    read_oss2f_freeze_read_only,
)
from autotrade.research.oss2_robustness import (
    OSS2BootstrapEvidence,
    OSS2CostStressEvidence,
    OSS2LocalNeighbor,
    OSS2LocalSensitivityEvidence,
    OSS2RobustnessEvidence,
    canonical_oss2d_policy,
)


D = Decimal
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64
H5 = "5" * 64


def passing_robustness(*, campaign_id: str = "oss2-campaign") -> OSS2RobustnessEvidence:
    return OSS2RobustnessEvidence(
        campaign_id=campaign_id,
        universe_hash=H1,
        policy_fingerprint=canonical_oss2d_policy().fingerprint,
        tournament_fingerprint=H2,
        selected_trial_id="trial-winner",
        selected_common_window_evidence_hash=H3,
        result_universe_hash=H4,
        pbo=PBOEvidence(
            campaign_id=campaign_id,
            partitions=8,
            combinations_evaluated=70,
            pbo=0.20,
            logits=(-1.0, 0.4),
            partition_sizes=(8, 8, 8, 8, 8, 8, 8, 8),
            balanced_partitions=True,
        ),
        deflated_sharpe=DeflatedSharpeEvidence(
            campaign_id=campaign_id,
            selected_trial_id="trial-winner",
            selected_sharpe=0.10,
            expected_max_sharpe=0.04,
            deflated_sharpe_probability=0.92,
            family_size=12,
            sample_size=64,
            metric_name="common_window_sharpe",
            metric_scale=1 / (365**0.5),
        ),
        bootstrap=OSS2BootstrapEvidence(
            observations=64,
            iterations=2000,
            block_size=4,
            seed=20260904,
            mean_compounded_return=0.13,
            median_compounded_return=0.12,
            lower_compounded_return=-0.05,
            upper_compounded_return=0.31,
            probability_positive=0.75,
            distribution_hash=H5,
        ),
        cost_stress=(
            OSS2CostStressEvidence(
                multiplier=D("1.5"),
                total_cost_bps=D("6"),
                config_hash=H1,
                result_hash=H2,
                common_window_net_return=0.08,
                common_window_sharpe=0.70,
                common_window_max_drawdown=0.12,
                sharpe_delta_vs_baseline=-0.12,
                net_return_delta_vs_baseline=-0.03,
            ),
            OSS2CostStressEvidence(
                multiplier=D("2.0"),
                total_cost_bps=D("8"),
                config_hash=H3,
                result_hash=H4,
                common_window_net_return=0.03,
                common_window_sharpe=0.30,
                common_window_max_drawdown=0.18,
                sharpe_delta_vs_baseline=-0.52,
                net_return_delta_vs_baseline=-0.08,
            ),
        ),
        local_sensitivity=OSS2LocalSensitivityEvidence(
            selected_lookback_bars=30,
            selected_rebalance_every_bars=5,
            selected_sharpe=0.90,
            neighbors=(
                OSS2LocalNeighbor("trial-a", 20, 5, 0.50),
                OSS2LocalNeighbor("trial-b", 40, 5, 0.70),
                OSS2LocalNeighbor("trial-c", 30, 10, 0.60),
            ),
            neighbor_median_sharpe=0.60,
            selected_minus_neighbor_median=0.30,
            fraction_selected_at_least_neighbor=1.0,
        ),
    )


def test_eligible_freeze_is_durable_hash_bound_and_read_only_verifiable(tmp_path):
    db = tmp_path / "oss2f.sqlite3"
    registry = SQLiteOSS2HoldoutFreezeRegistry(db)
    receipt = registry.freeze_and_record(
        receipt_id="freeze-001",
        robustness=passing_robustness(),
    )

    assert receipt.contract_version == OSS2F_CONTRACT_VERSION
    assert receipt.decision is OSS2HoldoutFreezeState.HOLDOUT_ELIGIBLE
    assert receipt.failed_gate_ids == ()
    assert receipt.final_holdout_observed is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert len(receipt.receipt_hash) == 64

    assert registry.get_for_campaign("oss2-campaign") == receipt
    assert read_oss2f_freeze_read_only(db, campaign_id="oss2-campaign") == receipt
    assert read_oss2f_freeze_read_only(db, campaign_id="unknown-campaign") is None


def test_identical_freeze_is_idempotent(tmp_path):
    registry = SQLiteOSS2HoldoutFreezeRegistry(tmp_path / "freeze.sqlite3")
    robustness = passing_robustness()
    first = registry.freeze_and_record(receipt_id="freeze-001", robustness=robustness)
    second = registry.freeze_and_record(receipt_id="freeze-001", robustness=robustness)
    assert second == first


def test_same_campaign_cannot_be_refrozen_with_new_receipt_id(tmp_path):
    registry = SQLiteOSS2HoldoutFreezeRegistry(tmp_path / "freeze.sqlite3")
    robustness = passing_robustness()
    registry.freeze_and_record(receipt_id="freeze-001", robustness=robustness)
    with pytest.raises(OSS2HoldoutFreezeConflict, match="different evidence"):
        registry.freeze_and_record(receipt_id="freeze-002", robustness=robustness)


def test_same_campaign_cannot_drift_after_freeze(tmp_path):
    registry = SQLiteOSS2HoldoutFreezeRegistry(tmp_path / "freeze.sqlite3")
    original = passing_robustness()
    registry.freeze_and_record(receipt_id="freeze-001", robustness=original)
    drifted = replace(original, pbo=replace(original.pbo, pbo=0.21))
    with pytest.raises(OSS2HoldoutFreezeConflict, match="different evidence"):
        registry.freeze_and_record(receipt_id="freeze-001", robustness=drifted)


def test_reject_freeze_cannot_be_upgraded_later(tmp_path):
    registry = SQLiteOSS2HoldoutFreezeRegistry(tmp_path / "freeze.sqlite3")
    passing = passing_robustness()
    rejected = replace(passing, pbo=replace(passing.pbo, pbo=0.80))
    receipt = registry.freeze_and_record(receipt_id="freeze-reject", robustness=rejected)

    assert receipt.decision is OSS2HoldoutFreezeState.REJECT
    assert receipt.failed_gate_ids == ("PBO_MAX",)
    with pytest.raises(OSS2HoldoutFreezeConflict):
        registry.freeze_and_record(receipt_id="freeze-reject", robustness=passing)


def test_receipt_id_cannot_be_reused_for_another_campaign(tmp_path):
    registry = SQLiteOSS2HoldoutFreezeRegistry(tmp_path / "freeze.sqlite3")
    registry.freeze_and_record(
        receipt_id="freeze-shared",
        robustness=passing_robustness(campaign_id="campaign-a"),
    )
    with pytest.raises(OSS2HoldoutFreezeConflict, match="another campaign"):
        registry.freeze_and_record(
            receipt_id="freeze-shared",
            robustness=passing_robustness(campaign_id="campaign-b"),
        )


def test_sqlite_triggers_make_registry_physically_append_only(tmp_path):
    db = tmp_path / "freeze.sqlite3"
    registry = SQLiteOSS2HoldoutFreezeRegistry(db)
    registry.freeze_and_record(
        receipt_id="freeze-001",
        robustness=passing_robustness(),
    )
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE oss2_holdout_freezes SET decision='REJECT' WHERE campaign_id='oss2-campaign'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM oss2_holdout_freezes WHERE campaign_id='oss2-campaign'"
            )
    finally:
        conn.close()


def test_read_only_reader_fails_when_registry_missing(tmp_path):
    with pytest.raises(OSS2HoldoutFreezeIntegrityError, match="does not exist"):
        read_oss2f_freeze_read_only(
            tmp_path / "missing.sqlite3",
            campaign_id="oss2-campaign",
        )


def test_read_only_reader_fails_when_table_missing(tmp_path):
    db = tmp_path / "empty.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated(x INTEGER)")
    conn.close()
    with pytest.raises(OSS2HoldoutFreezeIntegrityError, match="table is missing"):
        read_oss2f_freeze_read_only(db, campaign_id="oss2-campaign")


def test_read_only_reader_detects_json_corruption(tmp_path):
    db = tmp_path / "freeze.sqlite3"
    registry = SQLiteOSS2HoldoutFreezeRegistry(db)
    registry.freeze_and_record(receipt_id="freeze-001", robustness=passing_robustness())
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TRIGGER oss2_holdout_freezes_no_update")
        conn.execute(
            "UPDATE oss2_holdout_freezes SET receipt_json = ? WHERE campaign_id = ?",
            ("{not-json", "oss2-campaign"),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(OSS2HoldoutFreezeIntegrityError, match="receipt JSON"):
        read_oss2f_freeze_read_only(db, campaign_id="oss2-campaign")


def test_read_only_reader_detects_side_column_corruption(tmp_path):
    db = tmp_path / "freeze.sqlite3"
    registry = SQLiteOSS2HoldoutFreezeRegistry(db)
    registry.freeze_and_record(receipt_id="freeze-001", robustness=passing_robustness())
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TRIGGER oss2_holdout_freezes_no_update")
        conn.execute(
            "UPDATE oss2_holdout_freezes SET selected_trial_id = ? WHERE campaign_id = ?",
            ("trial-tampered", "oss2-campaign"),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(OSS2HoldoutFreezeIntegrityError, match="side-column mismatch"):
        read_oss2f_freeze_read_only(db, campaign_id="oss2-campaign")


def test_read_only_reader_detects_hash_corruption_inside_json(tmp_path):
    db = tmp_path / "freeze.sqlite3"
    registry = SQLiteOSS2HoldoutFreezeRegistry(db)
    registry.freeze_and_record(receipt_id="freeze-001", robustness=passing_robustness())
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT receipt_json FROM oss2_holdout_freezes WHERE campaign_id='oss2-campaign'"
        ).fetchone()
        payload = json.loads(row["receipt_json"])
        payload["receipt_hash"] = "f" * 64
        conn.execute("DROP TRIGGER oss2_holdout_freezes_no_update")
        conn.execute(
            "UPDATE oss2_holdout_freezes SET receipt_json = ? WHERE campaign_id = ?",
            (json.dumps(payload), "oss2-campaign"),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(OSS2HoldoutFreezeIntegrityError, match="receipt hash mismatch"):
        read_oss2f_freeze_read_only(db, campaign_id="oss2-campaign")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("contract_version", "OSS2F_V0", "contract version"),
        ("final_holdout_observed", True, "FINAL_HOLDOUT"),
        ("paper_execution_authorized", True, "PAPER"),
        ("capital_authority", "ALLOCATE", "capital or LIVE"),
        ("live_trading", "ENABLED", "capital or LIVE"),
        ("receipt_hash", "f" * 64, "receipt hash mismatch"),
    ),
)
def test_receipt_constructor_rejects_authority_or_integrity_tampering(
    tmp_path, field, value, match
):
    receipt = SQLiteOSS2HoldoutFreezeRegistry(tmp_path / "freeze.sqlite3").freeze_and_record(
        receipt_id="freeze-001",
        robustness=passing_robustness(),
    )
    with pytest.raises(OSS2HoldoutFreezeIntegrityError, match=match):
        replace(receipt, **{field: value})


def test_receipt_constructor_rejects_eligible_with_failed_gates(tmp_path):
    receipt = SQLiteOSS2HoldoutFreezeRegistry(tmp_path / "freeze.sqlite3").freeze_and_record(
        receipt_id="freeze-001",
        robustness=passing_robustness(),
    )
    with pytest.raises(OSS2HoldoutFreezeIntegrityError, match="eligible freeze"):
        replace(receipt, failed_gate_ids=("PBO_MAX",))


def test_receipt_constructor_rejects_reject_without_failed_gates(tmp_path):
    passing = passing_robustness()
    rejected = replace(passing, pbo=replace(passing.pbo, pbo=0.80))
    receipt = SQLiteOSS2HoldoutFreezeRegistry(tmp_path / "freeze.sqlite3").freeze_and_record(
        receipt_id="freeze-reject",
        robustness=rejected,
    )
    with pytest.raises(OSS2HoldoutFreezeIntegrityError, match="rejected freeze"):
        replace(receipt, failed_gate_ids=())


def test_invalid_ids_fail_closed(tmp_path):
    registry = SQLiteOSS2HoldoutFreezeRegistry(tmp_path / "freeze.sqlite3")
    with pytest.raises(OSS2HoldoutFreezeIntegrityError, match="receipt_id"):
        registry.freeze_and_record(receipt_id=" bad id ", robustness=passing_robustness())
    with pytest.raises(OSS2HoldoutFreezeIntegrityError, match="campaign_id"):
        registry.get_for_campaign("bad campaign id")
