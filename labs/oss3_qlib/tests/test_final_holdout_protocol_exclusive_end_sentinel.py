from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from labs.oss3_qlib.final_holdout_protocol import (
    DEVELOPMENT_EXCLUSIVE_END_SENTINEL,
    OSS3FinalHoldoutProtocolIntegrityError,
    SQLiteOSS3FinalHoldoutProtocolRegistry,
)
from labs.oss3_qlib.tests.test_final_holdout_protocol import (
    _holdout_commitment,
    _source,
)


def test_exact_d2r_one_microsecond_exclusive_end_sentinel_is_adjacent(tmp_path):
    preregistration, batch, seal = _source(tmp_path)
    development_end = datetime.fromisoformat(
        preregistration.d2e_plan.dataset.evaluation_end
    )
    assert DEVELOPMENT_EXCLUSIVE_END_SENTINEL == timedelta(microseconds=1)
    commitment = _holdout_commitment(
        preregistration,
        partition_start=(development_end - DEVELOPMENT_EXCLUSIVE_END_SENTINEL).isoformat(),
        partition_end=(development_end + timedelta(days=40)).isoformat(),
    )
    receipt = SQLiteOSS3FinalHoldoutProtocolRegistry(
        tmp_path / "d2j-sentinel.sqlite3"
    ).preregister_and_record(
        protocol_id="oss3d2j-exclusive-end-sentinel-v1",
        seal=seal,
        preregistration=preregistration,
        batch_evidence=batch,
        holdout_commitment=commitment,
    )
    assert receipt.holdout_commitment_fingerprint == commitment.fingerprint
    assert receipt.final_holdout_observed is False
    assert receipt.holdout_permit_issued is False
    assert receipt.holdout_permit_consumed is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"


def test_more_than_one_microsecond_overlap_remains_blocked(tmp_path):
    preregistration, batch, seal = _source(tmp_path)
    development_end = datetime.fromisoformat(
        preregistration.d2e_plan.dataset.evaluation_end
    )
    commitment = _holdout_commitment(
        preregistration,
        partition_start=(development_end - timedelta(microseconds=2)).isoformat(),
        partition_end=(development_end + timedelta(days=40)).isoformat(),
    )
    registry = SQLiteOSS3FinalHoldoutProtocolRegistry(
        tmp_path / "d2j-overlap.sqlite3"
    )
    with pytest.raises(
        OSS3FinalHoldoutProtocolIntegrityError,
        match="chronological boundary overlaps DEVELOPMENT",
    ):
        registry.preregister_and_record(
            protocol_id="oss3d2j-overlap-v1",
            seal=seal,
            preregistration=preregistration,
            batch_evidence=batch,
            holdout_commitment=commitment,
        )
