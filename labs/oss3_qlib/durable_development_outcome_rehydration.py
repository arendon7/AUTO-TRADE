"""Reconstruct OSS-3D3C from D3A's already-durable work artifacts.

This module does not rerun Qlib, materialize DEVELOPMENT labels, or recompute
D2D metrics.  It reads the exact candidate artifacts, D2S preregistration and
D2E terminal ledger produced by D3A, rebuilds the deterministic D2H identity,
re-verifies the D2E tournament from persisted terminal records, reproduces the
D2I winner seal and finally emits the D3C portable outcome bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Mapping

from autotrade.research.oss3_concrete_model_family import CANONICAL_CANDIDATES
from autotrade.research.oss3_development_inference import DevelopmentInferenceRequest
from autotrade.research.oss3_development_model_tournament import (
    PRIMARY_METRIC,
    DevelopmentDatasetBinding,
    DevelopmentModelCandidate,
    build_oss3d2e_plan,
    evaluate_oss3d2e_tournament,
)
from autotrade.research.oss3_factor_matrix_artifact import FactorMatrixArtifact
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact
from autotrade.research.trials import SQLiteTrialLedger, TrialStatus
from labs.oss3_qlib.development_winner_seal import seal_development_winner
from labs.oss3_qlib.durable_development_outcome import (
    DurableDevelopmentOutcomeBundle,
    DurableDevelopmentOutcomeIntegrityError,
    build_durable_development_outcome,
    write_durable_development_outcome,
)
from labs.oss3_qlib.family_environment_attestation import CandidateEnvironmentAttestation
from labs.oss3_qlib.family_evaluation_batch import (
    HYPOTHESIS_PREFIX,
    OSS3D2H_BATCH_EVIDENCE_VERSION,
    OSS3D2H_PREREGISTRATION_VERSION,
    CandidateEvaluationBinding,
    FamilyEvaluationBatchEvidence,
    FamilyEvaluationPreregistration,
    FrozenCandidateOutput,
    FrozenCandidateOutputBinding,
)
from labs.oss3_qlib.real_development_campaign import (
    D2S_PREREGISTRATION_ID,
    OSS3D3A_CAMPAIGN_EVIDENCE_VERSION,
    STATUS_COMPLETED,
    STRUCTURAL_FAILURE_CODE,
)


D2S_LEDGER_NAME = "d2s-real.sqlite3"
D2E_LEDGER_NAME = "d2e-real-trials.sqlite3"
CANDIDATE_ROOT_NAME = "candidates"


class DurableDevelopmentOutcomeRehydrationError(RuntimeError):
    pass


class DurableDevelopmentOutcomeRehydrationIntegrityError(
    DurableDevelopmentOutcomeRehydrationError
):
    pass


class DurableDevelopmentOutcomeRehydrationGovernanceError(
    DurableDevelopmentOutcomeRehydrationError
):
    pass


@dataclass(frozen=True, slots=True)
class _D3AEvidenceAdapter:
    payload_json: str
    fingerprint: str

    def to_dict(self) -> dict[str, object]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                "D3A adapter payload is not an object"
            )
        return value


@dataclass(frozen=True, slots=True)
class _SerializedRunEvidence:
    payload_json: str
    fingerprint: str

    def to_dict(self) -> dict[str, object]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                "D2G run evidence payload is not an object"
            )
        return value


def reconstruct_and_write_durable_development_outcome(
    *,
    d3a_result_path: str | Path,
    work_root: str | Path,
    output_path: str | Path,
) -> DurableDevelopmentOutcomeBundle:
    """Rebuild the completed D2H/D2I lineage from D3A durable outputs only."""
    root = Path(work_root).expanduser().resolve()
    if not root.is_dir():
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C work_root does not exist"
        )
    d3a_payload, d3a_fingerprint = _read_d3a_result(d3a_result_path)
    if d3a_payload.get("status") != STATUS_COMPLETED:
        raise DurableDevelopmentOutcomeRehydrationGovernanceError(
            "D3C requires a completed D3A campaign"
        )

    d2s = _read_d2s_preregistration(root / D2S_LEDGER_NAME)
    if _hash_payload(d2s) != d3a_payload.get("d2s_preregistration_fingerprint"):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C durable D2S preregistration differs from D3A result"
        )

    outputs = _load_candidate_outputs(root / CANDIDATE_ROOT_NAME, d3a_payload)
    preregistration = _reconstruct_d2h_preregistration(
        d2s=d2s,
        d3a=d3a_payload,
        outputs=outputs,
    )
    if preregistration.fingerprint != d3a_payload.get("d2h_preregistration_fingerprint"):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C reconstructed D2H preregistration differs from D3A"
        )

    tournament, evaluation_bindings = _reconstruct_terminal_batch_material(
        source_ledger=root / D2E_LEDGER_NAME,
        preregistration=preregistration,
        outputs=outputs,
        d3a=d3a_payload,
    )
    if tournament.fingerprint != d3a_payload.get("d2e_tournament_evidence_fingerprint"):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C reconstructed D2E tournament differs from D3A"
        )

    batch = FamilyEvaluationBatchEvidence(
        evidence_version=OSS3D2H_BATCH_EVIDENCE_VERSION,
        preregistration_fingerprint=preregistration.fingerprint,
        d2e_plan_fingerprint=preregistration.d2e_plan.fingerprint,
        d2h_code_version=preregistration.d2h_code_version,
        development_label_artifact_hash=preregistration.development_label_artifact_hash,
        shared_runner_code_hash=outputs[0].request.manifest.expected_runner_code_hash,
        runtime_environment_hash=outputs[0].runtime_environment.fingerprint,
        evaluations=evaluation_bindings,
        tournament_evidence=tournament,
    )
    if batch.fingerprint != d3a_payload.get("d2h_batch_evidence_fingerprint"):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C reconstructed D2H batch differs from D3A"
        )

    winner = seal_development_winner(
        preregistration=preregistration,
        batch_evidence=batch,
    )
    if winner.fingerprint != d3a_payload.get("d2i_winner_seal_fingerprint"):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C reconstructed D2I winner differs from D3A"
        )

    adapter = _D3AEvidenceAdapter(
        payload_json=_canonical_json(d3a_payload),
        fingerprint=d3a_fingerprint,
    )
    try:
        bundle = build_durable_development_outcome(
            source_d3a_evidence=adapter,
            preregistration=preregistration,
            batch_evidence=batch,
            winner=winner,
        )
    except DurableDevelopmentOutcomeIntegrityError as exc:
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C final bundle rebinding failed"
        ) from exc
    write_durable_development_outcome(bundle, output_path)
    return bundle


def _read_d3a_result(path: str | Path) -> tuple[dict[str, object], str]:
    document = _read_canonical_json_file(path, "D3A result")
    fingerprint = document.pop("fingerprint", None)
    _require_hash(fingerprint, "D3A result fingerprint")
    if document.get("evidence_version") != OSS3D3A_CAMPAIGN_EVIDENCE_VERSION:
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C source is not canonical D3A evidence"
        )
    if _hash_payload(document) != fingerprint:
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3A result fingerprint mismatch"
        )
    return document, fingerprint


def _read_d2s_preregistration(path: Path) -> dict[str, object]:
    _require_regular_file(path, "D2S preregistration ledger")
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(
            "SELECT preregistration_id, fingerprint, plan_json "
            "FROM oss3d2s_raw_development_preregistrations"
        ).fetchall()
    finally:
        conn.close()
    if len(rows) != 1 or rows[0]["preregistration_id"] != D2S_PREREGISTRATION_ID:
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C requires exactly the canonical D3A D2S preregistration"
        )
    try:
        payload = json.loads(str(rows[0]["plan_json"]))
    except json.JSONDecodeError as exc:
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D2S durable preregistration JSON is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D2S durable preregistration must be an object"
        )
    if _canonical_json(payload) != str(rows[0]["plan_json"]):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D2S durable preregistration is not canonical JSON"
        )
    fingerprint = str(rows[0]["fingerprint"])
    _require_hash(fingerprint, "D2S durable fingerprint")
    if _hash_payload(payload) != fingerprint:
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D2S durable fingerprint mismatch"
        )
    return payload


def _load_candidate_outputs(
    candidate_root: Path,
    d3a: Mapping[str, object],
) -> tuple[FrozenCandidateOutput, ...]:
    if not candidate_root.is_dir() or candidate_root.is_symlink():
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C candidate artifact root is invalid"
        )
    expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
    expected_output_pairs = d3a.get("candidate_output_hashes")
    if not isinstance(expected_output_pairs, list) or len(expected_output_pairs) != len(expected_ids):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3A result lacks exact six candidate output hashes"
        )
    expected_hashes: dict[str, str] = {}
    for pair in expected_output_pairs:
        if not isinstance(pair, list) or len(pair) != 2:
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                "D3A candidate output hash entry is invalid"
            )
        candidate_id, digest = pair
        if not isinstance(candidate_id, str):
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                "D3A candidate output id is invalid"
            )
        _require_hash(digest, "D3A candidate output hash")
        expected_hashes[candidate_id] = digest
    if tuple(expected_hashes) != expected_ids:
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3A candidate output family differs from frozen D2F six"
        )

    outputs: list[FrozenCandidateOutput] = []
    common_bundle_hash: str | None = None
    common_development_feature_hash: str | None = None
    for candidate_id in expected_ids:
        root = candidate_root / candidate_id
        if not root.is_dir() or root.is_symlink():
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                f"D3C candidate directory missing: {candidate_id}"
            )
        request = DevelopmentInferenceRequest.read(root / "request.json")
        bundle = TrainingBundleArtifact.read(root / "training-bundle.json")
        development_features = FactorMatrixArtifact.read(root / "development-features.json")
        prediction = QlibPredictionArtifact.read(root / "prediction.json")
        attestation = CandidateEnvironmentAttestation.read(root / "attestation.json")
        receipt = request.bind_prediction(
            prediction=prediction,
            training_bundle=bundle,
            development_features=development_features,
        )
        run_evidence = _read_run_evidence(root / "run-evidence.json")
        output = FrozenCandidateOutput(
            candidate_id=candidate_id,
            request=request,
            prediction=prediction,
            receipt=receipt,
            attestation=attestation,
            run_evidence=run_evidence,
        )
        if output.fingerprint != expected_hashes[candidate_id]:
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                f"D3C candidate output differs from D3A: {candidate_id}"
            )
        if common_bundle_hash is None:
            common_bundle_hash = bundle.artifact_hash
            common_development_feature_hash = development_features.artifact_hash
        elif (
            bundle.artifact_hash != common_bundle_hash
            or development_features.artifact_hash != common_development_feature_hash
        ):
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                "D3C candidate artifacts do not share one TRAIN/DEVELOPMENT input pair"
            )
        outputs.append(output)

    if common_bundle_hash != d3a.get("training_bundle_hash"):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C TRAIN bundle differs from D3A"
        )
    if common_development_feature_hash != d3a.get("development_feature_artifact_hash"):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C DEVELOPMENT feature artifact differs from D3A"
        )
    return tuple(outputs)


def _read_run_evidence(path: Path) -> _SerializedRunEvidence:
    document = _read_canonical_json_file(path, "D2G run evidence")
    if set(document) != {"evidence", "evidence_fingerprint"}:
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D2G run evidence wrapper schema drifted"
        )
    payload = document["evidence"]
    fingerprint = document["evidence_fingerprint"]
    if not isinstance(payload, dict):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D2G run evidence payload is invalid"
        )
    _require_hash(fingerprint, "D2G run evidence fingerprint")
    if _hash_payload(payload) != fingerprint:
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D2G run evidence fingerprint mismatch"
        )
    return _SerializedRunEvidence(
        payload_json=_canonical_json(payload),
        fingerprint=fingerprint,
    )


def _reconstruct_d2h_preregistration(
    *,
    d2s: Mapping[str, object],
    d3a: Mapping[str, object],
    outputs: tuple[FrozenCandidateOutput, ...],
) -> FamilyEvaluationPreregistration:
    required_strings = (
        "source_campaign_id",
        "research_split_hash",
        "research_universe_identity_hash",
        "label_definition_hash",
        "evaluation_keyset_hash",
        "evaluation_start",
        "evaluation_end",
        "tournament_campaign_id",
        "tournament_id",
        "d2f_plan_fingerprint",
        "d2f_request_set_fingerprint",
        "d2h_code_version",
    )
    for name in required_strings:
        if not isinstance(d2s.get(name), str):
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                f"D2S durable preregistration lacks {name}"
            )
    label_hash = d3a.get("development_label_artifact_hash")
    _require_hash(label_hash, "DEVELOPMENT label artifact hash")

    dataset = DevelopmentDatasetBinding(
        source_campaign_id=d2s["source_campaign_id"],
        research_split_hash=d2s["research_split_hash"],
        source_universe_hash=d2s["research_universe_identity_hash"],
        label_definition_hash=d2s["label_definition_hash"],
        development_label_artifact_hash=label_hash,
        evaluation_keyset_hash=d2s["evaluation_keyset_hash"],
        evaluation_start=d2s["evaluation_start"],
        evaluation_end=d2s["evaluation_end"],
    )
    runtime = outputs[0].runtime_environment
    if {output.runtime_environment.fingerprint for output in outputs} != {runtime.fingerprint}:
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            "D3C candidate runtime identities differ"
        )
    candidates = tuple(
        DevelopmentModelCandidate(
            trial_id=output.candidate_id,
            hypothesis_id=f"{HYPOTHESIS_PREFIX}:{output.candidate_id}",
            model_family=output.request.manifest.model_family,
            model_config_hash=output.request.manifest.model_config_hash,
            request_hash=output.request.request_hash,
            qlib_version=output.request.manifest.required_qlib_version,
            expected_runner_code_hash=output.request.manifest.expected_runner_code_hash,
            environment_attestation_hash=output.attestation.artifact_hash,
        )
        for output in outputs
    )
    plan = build_oss3d2e_plan(
        tournament_campaign_id=d2s["tournament_campaign_id"],
        tournament_id=d2s["tournament_id"],
        dataset=dataset,
        runtime_environment=runtime,
        candidates=candidates,
        code_version=d2s["d2h_code_version"],
    )
    return FamilyEvaluationPreregistration(
        preregistration_version=OSS3D2H_PREREGISTRATION_VERSION,
        d2f_plan_fingerprint=d2s["d2f_plan_fingerprint"],
        d2f_request_set_fingerprint=d2s["d2f_request_set_fingerprint"],
        d2h_code_version=d2s["d2h_code_version"],
        development_label_artifact_hash=label_hash,
        candidate_output_bindings=tuple(
            FrozenCandidateOutputBinding.from_output(output) for output in outputs
        ),
        d2e_plan=plan,
    )


def _reconstruct_terminal_batch_material(
    *,
    source_ledger: Path,
    preregistration: FamilyEvaluationPreregistration,
    outputs: tuple[FrozenCandidateOutput, ...],
    d3a: Mapping[str, object],
):
    _require_regular_file(source_ledger, "D2E terminal ledger")
    with tempfile.TemporaryDirectory(prefix="oss3d3c-ledger-") as temporary:
        copy = Path(temporary) / "d2e-verification.sqlite3"
        shutil.copyfile(source_ledger, copy)
        ledger = SQLiteTrialLedger(copy)
        records = tuple(ledger.list_trials(preregistration.d2e_plan.campaign.campaign_id))
        expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
        if tuple(record.spec.trial_id for record in records) != expected_ids:
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                "D3C D2E ledger family differs from exact six"
            )
        if any(not record.status.terminal for record in records):
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                "D3C D2E ledger is not terminal"
            )
        if tuple(record.spec.fingerprint for record in records) != tuple(
            trial.fingerprint for trial in preregistration.d2e_plan.trials
        ):
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                "D3C D2E ledger trial specs differ from reconstructed plan"
            )

        result_items = d3a.get("candidate_results")
        if not isinstance(result_items, list) or len(result_items) != len(expected_ids):
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                "D3A candidate result accounting is incomplete"
            )
        result_by_id = {
            item.get("candidate_id"): item
            for item in result_items
            if isinstance(item, dict) and isinstance(item.get("candidate_id"), str)
        }
        if tuple(result_by_id) != expected_ids:
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                "D3A candidate result family differs from exact six"
            )

        output_by_id = {output.candidate_id: output for output in outputs}
        bindings: list[CandidateEvaluationBinding] = []
        for record in records:
            candidate_id = record.spec.trial_id
            source_result = result_by_id[candidate_id]
            output = output_by_id[candidate_id]
            if record.status is TrialStatus.COMPLETED:
                if source_result.get("status") != "COMPLETED":
                    raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                        f"D3C terminal status mismatch: {candidate_id}"
                    )
                evaluation_hash = record.metrics.get("evaluation_artifact_hash")
                _require_hash(evaluation_hash, "D2D evaluation artifact hash")
                if source_result.get("evaluation_artifact_hash") != evaluation_hash:
                    raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                        f"D3C D2D artifact mismatch: {candidate_id}"
                    )
                if float(record.metrics[PRIMARY_METRIC]) != source_result.get("primary_metric"):
                    raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                        f"D3C primary metric mismatch: {candidate_id}"
                    )
                raw_p = float(record.p_value) if record.p_value is not None else None
                if raw_p != source_result.get("raw_p_value"):
                    raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                        f"D3C raw p-value mismatch: {candidate_id}"
                    )
                bindings.append(
                    CandidateEvaluationBinding(
                        candidate_id=candidate_id,
                        frozen_output_hash=output.fingerprint,
                        request_hash=output.request.request_hash,
                        prediction_artifact_hash=output.prediction.artifact_hash,
                        receipt_hash=output.receipt.fingerprint,
                        environment_attestation_hash=output.attestation.artifact_hash,
                        d2g_run_evidence_hash=output.run_evidence_fingerprint,
                        d2d_evaluation_artifact_hash=evaluation_hash,
                    )
                )
            else:
                if source_result.get("status") != "FAILED":
                    raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                        f"D3C failed-candidate status mismatch: {candidate_id}"
                    )
                if record.failure_code != STRUCTURAL_FAILURE_CODE:
                    raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                        f"D3C unexpected D2E failure code: {candidate_id}"
                    )
                if any(
                    source_result.get(name) is not None
                    for name in ("evaluation_artifact_hash", "primary_metric", "raw_p_value")
                ):
                    raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                        f"D3C failed candidate exposes evaluation values: {candidate_id}"
                    )

        tournament = evaluate_oss3d2e_tournament(ledger, preregistration.d2e_plan)
        return tournament, tuple(bindings)


def _read_canonical_json_file(path: str | Path, name: str) -> dict[str, object]:
    target = Path(path).expanduser().resolve()
    _require_regular_file(target, name)
    try:
        raw = target.read_text(encoding="utf-8")
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except DurableDevelopmentOutcomeRehydrationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            f"{name} is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(document, dict):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            f"{name} top level must be an object"
        )
    if raw != _canonical_json(document) + "\n":
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            f"{name} serialization is not canonical"
        )
    return document


def _require_regular_file(path: Path, name: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            f"{name} must be a regular non-symlink file"
        )


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DurableDevelopmentOutcomeRehydrationIntegrityError(
                f"duplicate JSON key: {key}"
            )
        result[key] = value
    return result


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _hash_payload(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_hash(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise DurableDevelopmentOutcomeRehydrationIntegrityError(
            f"invalid {name}"
        )
