from __future__ import annotations

from dataclasses import replace

import pytest

from autotrade.research.oss3_training_bundle import (
    TrainingBundleCompatibilityError,
    TrainingBundleIntegrityError,
)
from test_research_oss3_training_bundle import _bundle, _prediction


def test_manifest_rejects_noncanonical_partition_timestamps():
    manifest = _bundle().manifest
    with pytest.raises(ValueError, match=r"canonical \+00:00"):
        replace(manifest, partition_start="2026-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(manifest, partition_start="2026-01-01T00:00:00")
    with pytest.raises(ValueError, match="canonical UTC"):
        replace(manifest, partition_start="2026-01-01T01:00:00+01:00")


def test_manifest_rejects_invalid_iso_timestamp():
    with pytest.raises(ValueError, match="valid ISO-8601"):
        replace(_bundle().manifest, partition_start="not-a-time")


def test_receipt_rejects_noncanonical_windows():
    bundle = _bundle()
    receipt = bundle.bind_prediction(_prediction(bundle))
    with pytest.raises(ValueError, match=r"canonical \+00:00"):
        replace(receipt, train_start="2026-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(receipt, train_start="2026-01-01T00:00:00")
    with pytest.raises(ValueError, match="canonical UTC"):
        replace(receipt, train_start="2026-01-01T01:00:00+01:00")


def test_receipt_rejects_invalid_training_and_inference_order():
    bundle = _bundle()
    receipt = bundle.bind_prediction(_prediction(bundle))
    with pytest.raises(TrainingBundleIntegrityError, match="training window"):
        replace(receipt, train_end=receipt.train_start)
    with pytest.raises(TrainingBundleIntegrityError, match="inference window"):
        replace(receipt, inference_start=receipt.train_start)
    with pytest.raises(TrainingBundleIntegrityError, match="inference window"):
        replace(receipt, inference_end=receipt.inference_start)


def test_receipt_authority_defaults_cannot_be_escalated_together():
    bundle = _bundle()
    receipt = bundle.bind_prediction(_prediction(bundle))
    with pytest.raises(TrainingBundleCompatibilityError, match="authorize execution"):
        replace(receipt, execution_authorized=True, paper_execution_authorized=True)
