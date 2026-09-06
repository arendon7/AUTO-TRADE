from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from autotrade.research.oss3_concrete_model_family import build_concrete_model_request_set
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact
from autotrade.research.trials import SQLiteTrialLedger
from labs.oss3_qlib.development_winner_seal import seal_development_winner
from labs.oss3_qlib.family_evaluation_batch import (
    evaluate_preregistered_family,
    prepare_family_evaluation_preregistration,
    preregister_family_evaluation,
)
from labs.oss3_qlib.family_model_contract import family_runner_code_hash
from labs.oss3_qlib.final_holdout_evaluator import (
    FinalHoldoutFeatureRow,
    FinalHoldoutLabelRow,
    OSS3D2K_MATERIAL_VERSION,
    OSS3FinalHoldoutMaterial,
    ProtectedOSS3FinalHoldout,
)
from labs.oss3_qlib.final_holdout_protocol import SQLiteOSS3FinalHoldoutProtocolRegistry
from labs.oss3_qlib.tests import d2i_fixture


UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class D2KSource:
    train_features: object
    train_labels: object
    training_bundle: TrainingBundleArtifact
    development_features: object
    development_labels: object
    source_request: object
    preregistration: object
    batch_evidence: object
    winner_seal: object
    material: OSS3FinalHoldoutMaterial
    holdout: ProtectedOSS3FinalHoldout
    protocol: object


def build_d2k_source(tmp_path, *, label_mode: str = "aligned") -> D2KSource:
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
        tournament_campaign_id="oss3d2k-source-tournament-campaign-001",
        tournament_id="oss3d2k-source-tournament-001",
    )
    ledger = SQLiteTrialLedger(tmp_path / "d2k-source-trials.sqlite3")
    start = datetime(2026, 6, 1, tzinfo=UTC)
    preregister_family_evaluation(ledger, preregistration, now=start)
    batch = evaluate_preregistered_family(
        ledger,
        preregistration,
        outputs=outputs,
        development_labels=development_labels,
        now=start + timedelta(minutes=1),
    )
    seal = seal_development_winner(
        preregistration=preregistration,
        batch_evidence=batch,
    )
    binding = next(
        item for item in request_set.bindings if item.candidate_id == seal.selected_trial_id
    )

    material = build_final_holdout_material(
        source_request=binding.request,
        train_features=train_features,
        label_mode=label_mode,
    )
    holdout = ProtectedOSS3FinalHoldout(material)
    protocol_registry = SQLiteOSS3FinalHoldoutProtocolRegistry(
        tmp_path / "d2k-protocol.sqlite3"
    )
    protocol = protocol_registry.preregister_and_record(
        protocol_id=f"oss3d2k-protocol-{label_mode}",
        seal=seal,
        preregistration=preregistration,
        batch_evidence=batch,
        holdout_commitment=material.commitment,
    )
    return D2KSource(
        train_features=train_features,
        train_labels=train_labels,
        training_bundle=training_bundle,
        development_features=development_features,
        development_labels=development_labels,
        source_request=binding.request,
        preregistration=preregistration,
        batch_evidence=batch,
        winner_seal=seal,
        material=material,
        holdout=holdout,
        protocol=protocol,
    )


def build_final_holdout_material(*, source_request, train_features, label_mode: str = "aligned"):
    if label_mode not in {"aligned", "reversed", "constant"}:
        raise ValueError("unsupported label_mode")
    request = source_request.manifest
    start = datetime.fromisoformat(request.inference_end) + timedelta(days=1)
    end = start + timedelta(days=41)
    feature_names = tuple(feature.name for feature in train_features.features)
    symbols = d2i_fixture.SYMBOLS

    feature_rows: list[FinalHoldoutFeatureRow] = []
    label_rows: list[FinalHoldoutLabelRow] = []
    aligned = {"BTCUSDT": 0.01, "ETHUSDT": 0.02, "SOLUSDT": 0.03}
    reversed_values = {"BTCUSDT": 0.03, "ETHUSDT": 0.02, "SOLUSDT": 0.01}
    for day in range(40):
        timestamp = start + timedelta(days=day)
        for index, symbol in enumerate(symbols, start=1):
            # Preserve the source TRAIN feature relation while moving strictly
            # beyond DEVELOPMENT.  The selected Lasso winner therefore has a
            # real, non-degenerate Qlib prediction surface on FINAL_HOLDOUT.
            feature_rows.append(
                FinalHoldoutFeatureRow(
                    as_of=timestamp.isoformat(),
                    available_at=(timestamp - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=(
                        5.0 + 0.2 * index + 0.01 * day,
                        0.4 + 0.02 * index + 0.001 * day,
                    ),
                )
            )
            if label_mode == "aligned":
                target = aligned[symbol]
            elif label_mode == "reversed":
                target = reversed_values[symbol]
            else:
                target = 0.02
            label_rows.append(
                FinalHoldoutLabelRow(
                    label_as_of=timestamp.isoformat(),
                    horizon_end=(timestamp + timedelta(hours=1)).isoformat(),
                    available_at=(timestamp + timedelta(hours=1, minutes=1)).isoformat(),
                    symbol=symbol,
                    value=target,
                )
            )

    return OSS3FinalHoldoutMaterial(
        material_version=OSS3D2K_MATERIAL_VERSION,
        source_campaign_id=request.campaign_id,
        research_split_hash=request.research_split_hash,
        source_universe_hash=request.source_universe_hash,
        feature_schema_hash=request.feature_schema_hash,
        label_definition_hash=request.label_definition_hash,
        feature_source_dataset_hash="d" * 64,
        label_source_dataset_hash="e" * 64,
        partition_start=start.isoformat(),
        partition_end=end.isoformat(),
        feature_names=feature_names,
        feature_rows=tuple(feature_rows),
        label_rows=tuple(label_rows),
    )
