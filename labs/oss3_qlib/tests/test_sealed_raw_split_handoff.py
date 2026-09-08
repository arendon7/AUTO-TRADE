from __future__ import annotations

from dataclasses import replace

import pytest

from labs.oss3_market_data.real_acquisition_campaign import (
    run_restart_safe_campaign,
)
from labs.oss3_market_data.tests.test_real_acquisition_campaign import (
    FrozenArchiveTransport,
    NOW,
    mini_plan,
)
from labs.oss3_qlib.raw_training_bundle_provenance import (
    derive_raw_training_bundle,
)
from labs.oss3_qlib.sealed_raw_split_handoff import (
    DOWNSTREAM_POLICY,
    REVERIFICATION_POLICY,
    SPLIT_POLICY,
    SealedRawSplitHandoffGovernanceError,
    SealedRawSplitHandoffIntegrityError,
    build_sealed_raw_split_handoff,
)


def _complete_campaign(tmp_path):
    plan = mini_plan()
    root = tmp_path / "evidence"
    transport = FrozenArchiveTransport(plan)
    result, material = run_restart_safe_campaign(
        evidence_root=root,
        plan=plan,
        now=NOW,
        allow_network=True,
        transport=transport,
    )
    assert result.complete is True
    assert material is not None
    return plan, root, result, material, transport


def test_d2w_requires_preexisting_complete_d2v_seal(tmp_path):
    plan = mini_plan()
    root = tmp_path / "empty"
    with pytest.raises(SealedRawSplitHandoffGovernanceError, match="pre-existing complete D2V"):
        build_sealed_raw_split_handoff(
            evidence_root=root,
            plan=plan,
            now=NOW,
        )


def test_d2w_builds_exact_d2r_and_d2s_sources_from_one_d2v_seal(tmp_path):
    plan, root, campaign, partition_material, transport = _complete_campaign(tmp_path)
    request_count_before = len(transport.requests)
    handoff = build_sealed_raw_split_handoff(
        evidence_root=root,
        plan=plan,
        now=NOW,
    )
    assert len(transport.requests) == request_count_before
    assert handoff.evidence.collection_id == plan.collection_id
    assert handoff.evidence.d2u_plan_fingerprint == plan.fingerprint
    assert handoff.evidence.d2v_campaign_seal_fingerprint == campaign.campaign_seal_fingerprint
    assert handoff.evidence.d2u_partition_material_fingerprint == partition_material.fingerprint
    assert handoff.evidence.d2u_assembly_evidence_fingerprint == partition_material.evidence.fingerprint
    assert handoff.training_source.warmup_universe.universe_hash == partition_material.training_warmup.universe_hash
    assert handoff.training_source.training_universe.universe_hash == partition_material.training.universe_hash
    assert handoff.development_source.warmup_universe.universe_hash == partition_material.development_warmup.universe_hash
    assert handoff.development_source.development_universe.universe_hash == partition_material.development.universe_hash
    assert handoff.training_source.universe_identity_hash == handoff.development_source.universe_identity_hash
    assert handoff.evidence.research_universe_identity_hash == handoff.training_source.universe_identity_hash
    assert handoff.evidence.raw_training_source_hash == handoff.training_source.source_hash
    assert handoff.evidence.raw_development_source_hash == handoff.development_source.source_hash
    assert handoff.evidence.training_warmup_bars == 20
    assert handoff.evidence.training_bars == 39
    assert handoff.evidence.development_warmup_bars == 20
    assert handoff.evidence.development_bars == 31
    assert handoff.evidence.split_policy == SPLIT_POLICY
    assert handoff.evidence.reverification_policy == REVERIFICATION_POLICY
    assert handoff.evidence.downstream_policy == DOWNSTREAM_POLICY
    assert handoff.evidence.network_used_during_handoff is False
    assert handoff.evidence.train_label_artifact_materialized is False
    assert handoff.evidence.development_label_artifact_materialized is False
    assert handoff.evidence.prediction_values_loaded is False
    assert handoff.evidence.development_metrics_computed is False
    assert handoff.evidence.final_holdout_values_loaded is False
    assert handoff.evidence.qlib_runtime_used is False
    assert handoff.evidence.execution_authorized is False
    assert handoff.evidence.capital_authority == "NONE"
    assert handoff.evidence.live_trading == "BLOCKED"


def test_d2w_repeated_offline_handoff_is_bitwise_identity_stable(tmp_path):
    plan, root, _, _, _ = _complete_campaign(tmp_path)
    first = build_sealed_raw_split_handoff(
        evidence_root=root,
        plan=plan,
        now=NOW,
    )
    second = build_sealed_raw_split_handoff(
        evidence_root=root,
        plan=plan,
        now=NOW,
    )
    assert first.evidence.research_split_hash == second.evidence.research_split_hash
    assert first.evidence.fingerprint == second.evidence.fingerprint
    assert first.fingerprint == second.fingerprint
    assert first.training_source.source_hash == second.training_source.source_hash
    assert first.development_source.source_hash == second.development_source.source_hash


def test_d2w_fails_if_any_sealed_raw_byte_no_longer_reverifies(tmp_path):
    plan, root, _, _, _ = _complete_campaign(tmp_path)
    descriptor = plan.descriptors[0]
    archive_path = root / descriptor.instrument.symbol / descriptor.period / descriptor.archive_filename
    archive_path.write_bytes(archive_path.read_bytes() + b"tamper")
    with pytest.raises(Exception):
        build_sealed_raw_split_handoff(
            evidence_root=root,
            plan=plan,
            now=NOW,
        )


def test_d2w_source_is_directly_compatible_with_existing_d2r_derivation(tmp_path):
    plan, root, _, _, _ = _complete_campaign(tmp_path)
    handoff = build_sealed_raw_split_handoff(
        evidence_root=root,
        plan=plan,
        now=NOW,
    )
    features, labels, bundle, receipt = derive_raw_training_bundle(
        raw_source=handoff.training_source,
        campaign_id=handoff.evidence.source_campaign_id,
        research_split_hash=handoff.evidence.research_split_hash,
    )
    assert features.manifest.research_split_hash == handoff.evidence.research_split_hash
    assert labels.manifest.research_split_hash == handoff.evidence.research_split_hash
    assert bundle.manifest.feature_artifact_hash == features.artifact_hash
    assert bundle.manifest.label_artifact_hash == labels.artifact_hash
    assert receipt.raw_training_source_hash == handoff.training_source.source_hash
    assert receipt.research_universe_identity_hash == handoff.evidence.research_universe_identity_hash
    assert receipt.development_values_loaded is False
    assert receipt.final_holdout_values_loaded is False
    assert receipt.network_allowed is False
    assert receipt.execution_authorized is False
    assert receipt.capital_authority == "NONE"


def test_d2w_evidence_cannot_be_mutated_into_labels_metrics_or_execution(tmp_path):
    plan, root, _, _, _ = _complete_campaign(tmp_path)
    handoff = build_sealed_raw_split_handoff(
        evidence_root=root,
        plan=plan,
        now=NOW,
    )
    for changes in (
        {"network_used_during_handoff": True},
        {"train_label_artifact_materialized": True},
        {"development_label_artifact_materialized": True},
        {"prediction_values_loaded": True},
        {"development_metrics_computed": True},
        {"final_holdout_values_loaded": True},
        {"qlib_runtime_used": True},
        {"promotion_authorized": True},
        {"execution_authorized": True},
        {"paper_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
    ):
        with pytest.raises(SealedRawSplitHandoffGovernanceError):
            replace(handoff.evidence, **changes)


def test_d2w_rejects_different_collection_plan_without_matching_d2v_seal(tmp_path):
    plan, root, _, _, _ = _complete_campaign(tmp_path)
    other_plan = replace(plan, collection_id=plan.collection_id + "-other")
    with pytest.raises(SealedRawSplitHandoffGovernanceError, match="pre-existing complete D2V"):
        build_sealed_raw_split_handoff(
            evidence_root=root,
            plan=other_plan,
            now=NOW,
        )
