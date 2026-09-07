from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from autotrade.research.oss3_concrete_model_family import build_concrete_model_request_set
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact
from autotrade.research.trials import SQLiteTrialLedger
from labs.oss3_qlib.development_winner_seal import seal_development_winner
from labs.oss3_qlib.family_evaluation_batch import (
    FrozenCandidateOutput,
    evaluate_preregistered_family,
    prepare_family_evaluation_preregistration,
    preregister_family_evaluation,
)
from labs.oss3_qlib.family_model_contract import family_runner_code_hash
from labs.oss3_qlib.final_holdout_protocol import (
    OSS3D2J_COMMITMENT_VERSION,
    OSS3ProtectedFinalHoldoutCommitment,
    SQLiteOSS3FinalHoldoutProtocolRegistry,
)
from labs.oss3_qlib.tests import d2i_fixture


UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class D2LSource:
    train_features: object
    train_labels: object
    training_bundle: TrainingBundleArtifact
    development_features: object
    development_labels: object
    outputs: tuple[FrozenCandidateOutput, ...]
    preregistration: object
    batch_evidence: object
    winner_seal: object
    winner_output: FrozenCandidateOutput
    holdout_commitment: OSS3ProtectedFinalHoldoutCommitment
    protocol: object


def build_d2l_source(tmp_path) -> D2LSource:
    """Build exact D2F->D2H->D2I->D2J lineage without holdout label values."""
    train_features = d2i_fixture._train_features()
    train_labels = d2i_fixture._train_labels(train_features)
    training_bundle = TrainingBundleArtifact.build(
        features=train_features,
        labels=train_labels,
    )
    development_features = d2i_fixture._development_features()
    development_labels = d2i_fixture._development_labels(development_features)

    d2f_plan, request_set = build_concrete_model_request_set(
        training_bundle=training_bundle,
        development_features=development_features,
        shared_runner_code_hash=family_runner_code_hash(),
    )
    outputs = tuple(
        d2i_fixture._candidate_output(
            index=index,
            binding=binding,
            bundle=training_bundle,
            development_features=development_features,
        )
        for index, binding in enumerate(request_set.bindings)
    )
    preregistration = prepare_family_evaluation_preregistration(
        d2f_plan=d2f_plan,
        d2f_request_set=request_set,
        outputs=outputs,
        development_labels=development_labels,
        tournament_campaign_id="oss3d2l-tournament-campaign-001",
        tournament_id="oss3d2l-tournament-001",
    )
    ledger = SQLiteTrialLedger(tmp_path / "d2l-development.sqlite3")
    now = datetime(2026, 6, 1, tzinfo=UTC)
    preregister_family_evaluation(ledger, preregistration, now=now)
    batch_evidence = evaluate_preregistered_family(
        ledger,
        preregistration,
        outputs=outputs,
        development_labels=development_labels,
        now=now + timedelta(minutes=1),
    )
    winner_seal = seal_development_winner(
        preregistration=preregistration,
        batch_evidence=batch_evidence,
    )
    winner_output = next(
        output for output in outputs if output.candidate_id == winner_seal.selected_trial_id
    )

    dataset = preregistration.d2e_plan.dataset
    holdout_start = datetime.fromisoformat(dataset.evaluation_end) + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=31)
    holdout_commitment = OSS3ProtectedFinalHoldoutCommitment(
        commitment_version=OSS3D2J_COMMITMENT_VERSION,
        source_campaign_id=dataset.source_campaign_id,
        research_split_hash=dataset.research_split_hash,
        source_universe_hash=dataset.source_universe_hash,
        label_definition_hash=dataset.label_definition_hash,
        feature_artifact_hash="d" * 64,
        label_artifact_hash="e" * 64,
        evaluation_keyset_hash="f" * 64,
        cross_section_key_hash="a" * 64,
        partition_start=holdout_start.isoformat(),
        partition_end=holdout_end.isoformat(),
        row_count=90,
        cross_section_count=30,
        minimum_cross_section_observation_count=3,
        label_values_exposed=False,
        final_holdout_observed=False,
    )
    protocol_registry = SQLiteOSS3FinalHoldoutProtocolRegistry(
        tmp_path / "d2l-protocol.sqlite3"
    )
    protocol = protocol_registry.preregister_and_record(
        protocol_id="oss3d2l-protocol-001",
        seal=winner_seal,
        preregistration=preregistration,
        batch_evidence=batch_evidence,
        holdout_commitment=holdout_commitment,
    )
    return D2LSource(
        train_features=train_features,
        train_labels=train_labels,
        training_bundle=training_bundle,
        development_features=development_features,
        development_labels=development_labels,
        outputs=outputs,
        preregistration=preregistration,
        batch_evidence=batch_evidence,
        winner_seal=winner_seal,
        winner_output=winner_output,
        holdout_commitment=holdout_commitment,
        protocol=protocol,
    )
