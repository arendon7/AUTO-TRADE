from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from autotrade.research.market import Bar, MarketDataset
from autotrade.research.universe import AlignedMarketUniverse
from labs.oss3_qlib.dual_holdout_acquisition_preregistration import canonical_oss3d3d_dual_holdout_plan
from labs.oss3_qlib.protected_holdout_materialization import (
    EXPECTED_D3D_PLAN_FINGERPRINT,
    EXPECTED_D3E_CAMPAIGN_SEAL,
    EXPECTED_D3E_CERTIFIED_HEAD,
    MATERIALIZATION_POLICY,
    Q1_FEATURE_POLICY,
    Q1_LABEL_POLICY,
    Q2_POLICY,
    TEMPORAL_SEPARATION_POLICY,
    D2Y_WARMUP_POLICY,
    ProtectedDualHoldoutMaterializationEvidence,
    ProtectedHoldoutMaterializationGovernanceError,
    ProtectedHoldoutMaterializationIntegrityError,
    _verify_half_open_temporal_separation,
    build_economic_holdout_commitment,
    build_predictive_final_holdout_material,
    write_public_d3f_evidence,
)
from labs.oss3_qlib.raw_training_bundle_provenance import (
    canonical_oss3d2r_feature_definitions,
    canonical_oss3d2r_label_definition,
    research_universe_identity_hash,
)

UTC = timezone.utc
HASH = "1" * 64


def _schema_hash() -> str:
    payload = [item.to_dict() for item in canonical_oss3d2r_feature_definitions()]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


def _dataset(*, symbol: str, instrument, start: datetime, count: int, base: Decimal, terminal_multiplier: Decimal = Decimal("1")) -> MarketDataset:
    bars = []
    prior = base
    for index in range(count):
        close = base + Decimal(index + 1)
        if index == count - 1:
            close *= terminal_multiplier
        open_price = prior
        bars.append(Bar(symbol=symbol, started_at=start + timedelta(hours=index), timeframe_seconds=3600, open=open_price, high=max(open_price, close) + Decimal("1"), low=min(open_price, close) - Decimal("1"), close=close, volume=Decimal("1000") + Decimal(index)))
        prior = close
    return MarketDataset(instrument=instrument, bars=tuple(bars), source=f"synthetic:{symbol}:{start.isoformat()}:{count}:{terminal_multiplier}")


def _universes(*, terminal_multiplier: Decimal = Decimal("1")):
    plan = canonical_oss3d3d_dual_holdout_plan()
    instruments = {symbol: next(descriptor.instrument for descriptor in plan.predictive.descriptors if descriptor.instrument.symbol == symbol) for symbol in plan.symbols}
    warmup_start = datetime(2025, 12, 31, 4, tzinfo=UTC)
    holdout_start = datetime(2026, 1, 1, 0, tzinfo=UTC)
    economic_start = holdout_start + timedelta(hours=32)
    warmup = AlignedMarketUniverse.from_datasets(datasets=tuple(_dataset(symbol=symbol, instrument=instruments[symbol], start=warmup_start, count=20, base=Decimal(100 + offset * 100)) for offset, symbol in enumerate(plan.symbols)), universe_name="test-warmup")
    predictive = AlignedMarketUniverse.from_datasets(datasets=tuple(_dataset(symbol=symbol, instrument=instruments[symbol], start=holdout_start, count=32, base=Decimal(200 + offset * 100), terminal_multiplier=terminal_multiplier) for offset, symbol in enumerate(plan.symbols)), universe_name="test-predictive")
    economic = AlignedMarketUniverse.from_datasets(datasets=tuple(_dataset(symbol=symbol, instrument=instruments[symbol], start=economic_start, count=64, base=Decimal(300 + offset * 100)) for offset, symbol in enumerate(plan.symbols)), universe_name="test-economic")
    return warmup, predictive, economic


def _predictive(*, terminal_multiplier: Decimal = Decimal("1")):
    warmup, predictive, _ = _universes(terminal_multiplier=terminal_multiplier)
    start = predictive.datasets[0].bars[0].started_at.astimezone(UTC)
    end = predictive.datasets[0].bars[-1].ended_at.astimezone(UTC)
    return build_predictive_final_holdout_material(
        warmup_universe=warmup,
        holdout_universe=predictive,
        source_campaign_id="oss3d3a-real-development-campaign-v1",
        research_split_hash="2" * 64,
        expected_source_universe_hash=research_universe_identity_hash(predictive),
        feature_schema_hash=_schema_hash(),
        label_definition_hash=canonical_oss3d2r_label_definition().fingerprint,
        partition_start=start.isoformat(),
        partition_end=end.isoformat(),
    )


def _public_evidence() -> ProtectedDualHoldoutMaterializationEvidence:
    predictive = _predictive()
    _, _, economic_universe = _universes()
    economic = build_economic_holdout_commitment(economic_universe)
    commitment = predictive.commitment
    return ProtectedDualHoldoutMaterializationEvidence(
        evidence_version="OSS3D3F_PROTECTED_DUAL_HOLDOUT_MATERIALIZATION_EVIDENCE_V1",
        d3d_plan_fingerprint=EXPECTED_D3D_PLAN_FINGERPRINT,
        d3e_certified_head=EXPECTED_D3E_CERTIFIED_HEAD,
        d3e_campaign_seal_fingerprint=EXPECTED_D3E_CAMPAIGN_SEAL,
        d2w_evidence_fingerprint="3" * 64,
        research_split_hash="2" * 64,
        research_universe_identity_hash=commitment.source_universe_hash,
        training_bundle_hash="4" * 64,
        feature_schema_hash=_schema_hash(),
        label_definition_hash=canonical_oss3d2r_label_definition().fingerprint,
        predictive_commitment_fingerprint=commitment.fingerprint,
        predictive_feature_artifact_hash=commitment.feature_artifact_hash,
        predictive_label_artifact_hash=commitment.label_artifact_hash,
        predictive_evaluation_keyset_hash=commitment.evaluation_keyset_hash,
        predictive_cross_section_key_hash=commitment.cross_section_key_hash,
        predictive_partition_start=commitment.partition_start,
        predictive_partition_end=commitment.partition_end,
        predictive_row_count=commitment.row_count,
        predictive_cross_section_count=commitment.cross_section_count,
        predictive_minimum_cross_section_observation_count=commitment.minimum_cross_section_observation_count,
        economic_commitment_fingerprint=economic.fingerprint,
        economic_universe_hash=economic.universe_hash,
        economic_source_dataset_set_hash=economic.source_dataset_set_hash,
        economic_partition_start=economic.partition_start,
        economic_partition_end=economic.partition_end,
        economic_bar_count=economic.bar_count,
        economic_symbol_count=economic.symbol_count,
        materialization_policy=MATERIALIZATION_POLICY,
        q1_feature_policy=Q1_FEATURE_POLICY,
        q1_label_policy=Q1_LABEL_POLICY,
        q2_policy=Q2_POLICY,
        temporal_separation_policy=TEMPORAL_SEPARATION_POLICY,
        d2y_warmup_policy=D2Y_WARMUP_POLICY,
        d3e_full_offline_reverification_complete=True,
        d2y_full_offline_reverification_complete=True,
        predictive_protected_feature_values_materialized=True,
        predictive_protected_label_values_materialized=True,
        label_values_exposed=False,
        prediction_values_materialized=False,
        predictive_metrics_computed=False,
        economic_outcomes_observed=False,
        final_holdout_observed=False,
        economic_holdout_observed=False,
        d2j_protocol_registered=False,
        d2m_protocol_registered=False,
        holdout_permit_issued=False,
        holdout_permit_consumed=False,
        profitability_claim_authorized=False,
        promotion_authorized=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )


def test_predictive_material_builds_value_opaque_d2j_commitment():
    material = _predictive()
    commitment = material.commitment
    assert commitment.row_count == 31 * 3
    assert commitment.cross_section_count == 31
    assert commitment.minimum_cross_section_observation_count == 3
    assert commitment.label_values_exposed is False
    assert commitment.final_holdout_observed is False
    assert commitment.source_campaign_id == "oss3d3a-real-development-campaign-v1"
    assert material.feature_names == ("momentum_20", "volatility_20")


def test_terminal_future_bar_changes_label_not_prior_causal_feature_rows():
    original = _predictive()
    changed = _predictive(terminal_multiplier=Decimal("1.10"))
    assert original.feature_rows == changed.feature_rows
    assert original.label_rows[:-3] == changed.label_rows[:-3]
    assert original.label_rows[-3:] != changed.label_rows[-3:]


def test_short_warmup_is_rejected():
    warmup, predictive, _ = _universes()
    broken = AlignedMarketUniverse.from_datasets(datasets=tuple(dataset.slice(0, 19) for dataset in warmup.datasets), universe_name="short-warmup")
    with pytest.raises(ProtectedHoldoutMaterializationGovernanceError, match="exactly twenty warmup bars"):
        build_predictive_final_holdout_material(
            warmup_universe=broken,
            holdout_universe=predictive,
            source_campaign_id="oss3d3a-real-development-campaign-v1",
            research_split_hash="2" * 64,
            expected_source_universe_hash=research_universe_identity_hash(predictive),
            feature_schema_hash=_schema_hash(),
            label_definition_hash=canonical_oss3d2r_label_definition().fingerprint,
            partition_start=predictive.datasets[0].bars[0].started_at.isoformat(),
            partition_end=predictive.datasets[0].bars[-1].ended_at.isoformat(),
        )


def test_feature_schema_drift_is_rejected():
    warmup, predictive, _ = _universes()
    with pytest.raises(ProtectedHoldoutMaterializationIntegrityError, match="feature schema"):
        build_predictive_final_holdout_material(
            warmup_universe=warmup,
            holdout_universe=predictive,
            source_campaign_id="oss3d3a-real-development-campaign-v1",
            research_split_hash="2" * 64,
            expected_source_universe_hash=research_universe_identity_hash(predictive),
            feature_schema_hash=HASH,
            label_definition_hash=canonical_oss3d2r_label_definition().fingerprint,
            partition_start=predictive.datasets[0].bars[0].started_at.isoformat(),
            partition_end=predictive.datasets[0].bars[-1].ended_at.isoformat(),
        )


def test_economic_commitment_is_raw_identity_only():
    _, _, economic_universe = _universes()
    commitment = build_economic_holdout_commitment(economic_universe)
    assert commitment.bar_count == 64
    assert commitment.symbol_count == 3
    assert commitment.market_values_exposed is False
    assert commitment.economic_outcomes_observed is False
    assert commitment.source_dataset_set_hash
    assert commitment.universe_hash == economic_universe.universe_hash


def test_half_open_temporal_boundary_accepts_equality_and_rejects_overlap():
    predictive = _predictive().commitment
    _, _, economic_universe = _universes()
    economic = build_economic_holdout_commitment(economic_universe)
    assert predictive.partition_end == economic.partition_start
    _verify_half_open_temporal_separation(predictive, economic, require_contiguous=True)
    overlapping = replace(economic, partition_start=(datetime.fromisoformat(economic.partition_start) - timedelta(microseconds=1)).isoformat())
    with pytest.raises(ProtectedHoldoutMaterializationGovernanceError, match="overlap"):
        _verify_half_open_temporal_separation(predictive, overlapping, require_contiguous=False)


def test_public_writer_never_serializes_protected_rows_or_market_values(tmp_path):
    evidence = _public_evidence()
    target = tmp_path / "d3f-public.json"
    write_public_d3f_evidence(evidence, target)
    raw = target.read_text(encoding="utf-8")
    document = json.loads(raw)
    assert document["fingerprint"] == evidence.fingerprint
    for forbidden in ('"feature_rows"', '"label_rows"', '"prediction_rows"', '"open"', '"high"', '"low"', '"close"', '"volume"'):
        assert forbidden not in raw
    assert document["label_values_exposed"] is False
    assert document["prediction_values_materialized"] is False
    assert document["predictive_metrics_computed"] is False


def test_public_evidence_rejects_authority_escalation():
    evidence = _public_evidence()
    with pytest.raises(ProtectedHoldoutMaterializationGovernanceError, match="cannot expose/evaluate/register/authorize"):
        replace(evidence, execution_authorized=True)
