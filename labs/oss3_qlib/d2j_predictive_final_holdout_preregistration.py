"""OSS-3D3G durable D2J predictive FINAL_HOLDOUT preregistration.

D3G consumes only two already-certified, value-opaque handoffs:

* the portable OSS-3D3C DEVELOPMENT outcome bundle; and
* the public OSS-3D3F protected dual-holdout evidence.

It reconstructs the exact typed D2H/D2I lineage embedded in D3C, reconstructs
only the public Q1 commitment identity embedded in D3F, and records the frozen
OSS-3D2J predictive protocol in its append-only registry.  It does not load Q1
feature/label rows, evaluate FINAL_HOLDOUT, issue/consume a permit, run Qlib,
retune/reselect, or authorize execution, PAPER, capital or LIVE trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Mapping

from autotrade.research.multiple_testing import HolmEvidence
from autotrade.research.oss3_development_model_tournament import (
    DevelopmentDatasetBinding,
    DevelopmentModelCandidate,
    OSS3D2ETournamentEvidence,
    RuntimeEnvironmentIdentity,
    build_oss3d2e_plan,
)
from autotrade.research.tournament import (
    RankingDirection,
    TournamentEntry,
    TournamentEvidence,
)
from autotrade.research.trials import TrialStatus
from labs.oss3_qlib.development_winner_seal import (
    DevelopmentWinnerSelectionSeal,
    seal_development_winner,
    verify_development_winner_seal,
)
from labs.oss3_qlib.durable_development_outcome import (
    DurableDevelopmentOutcomeBundle,
    read_durable_development_outcome,
)
from labs.oss3_qlib.family_evaluation_batch import (
    CandidateEvaluationBinding,
    FamilyEvaluationBatchEvidence,
    FamilyEvaluationPreregistration,
    FrozenCandidateOutputBinding,
)
from labs.oss3_qlib.final_holdout_protocol import (
    OSS3D2J_COMMITMENT_VERSION,
    OSS3FinalHoldoutProtocolReceipt,
    OSS3ProtectedFinalHoldoutCommitment,
    SQLiteOSS3FinalHoldoutProtocolRegistry,
    read_oss3d2j_protocol_read_only,
)
from labs.oss3_qlib.protected_holdout_materialization import (
    SOURCE_CAMPAIGN_ID,
    ProtectedDualHoldoutMaterializationEvidence,
)


OSS3D3G_CONTRACT_VERSION = "OSS3D3G_D2J_PREDICTIVE_FINAL_HOLDOUT_PREREGISTRATION_V1"
PROTOCOL_ID = "oss3d3g-real-q1-predictive-protocol-v1"
NEXT_FRONTIER = "SEPARATE_D2K_AUTHORIZATION_REQUIRED_BEFORE_ANY_HOLDOUT_EVALUATION"

EXPECTED_D3C_BUNDLE_FINGERPRINT = "1d271c31f506be21d2a99c857a1007cc5676cfcb5ab83fb6a524171a38dbaf43"
EXPECTED_D3C_SCIENTIFIC_OUTCOME = "5b4261b186e30cbdc30ec2c3025305fbb96650247a066cdf470ce53e80f86ea7"
EXPECTED_D3C_D2H_PREREGISTRATION = "1e90ee15a3ac26b6aaa8dd0703efde7e7046f34e9cc36e9e6f338d802af08238"
EXPECTED_D3C_D2H_BATCH = "064883e0e8c1e5e5ef11595b5cdacdd73e4a3dd2668fe2e8ba005eba71413ce9"
EXPECTED_D3C_D2I_SEAL = "65537f610766852c834893604192e1fe5562d0385fa2b00f4c692b6b074d7cb1"
EXPECTED_SELECTED_TRIAL = "linear-ridge-a10"
EXPECTED_D3F_EVIDENCE = "a9681ff31f4d74176d072c2076d129516d8db19cfb8aea093432ec6f7ac9d5b6"
EXPECTED_Q1_COMMITMENT = "a6fef49419ef22b75be45385a805f8f3242653a3fcb74cb708be6548ac865bde"
MAX_PUBLIC_EVIDENCE_BYTES = 128 * 1024
_HASH_CHARS = frozenset("0123456789abcdef")


class D2JPredictivePreregistrationError(RuntimeError):
    """Base OSS-3D3G failure."""


class D2JPredictivePreregistrationIntegrityError(D2JPredictivePreregistrationError):
    """An upstream certified identity or deterministic rehydration drifted."""


class D2JPredictivePreregistrationGovernanceError(D2JPredictivePreregistrationError):
    """The operation attempts to exceed protocol-only preregistration authority."""


@dataclass(frozen=True, slots=True)
class D2JPredictivePreregistrationEvidence:
    contract_version: str
    protocol_id: str
    next_frontier: str
    source_d3c_bundle_fingerprint: str
    source_scientific_outcome_fingerprint: str
    source_d2h_preregistration_fingerprint: str
    source_d2h_batch_evidence_fingerprint: str
    source_d2i_seal_fingerprint: str
    selected_trial_id: str
    source_d3f_evidence_fingerprint: str
    holdout_commitment_fingerprint: str
    d2j_receipt_hash: str
    d2j_policy_fingerprint: str
    expected_holdout_authorization_id: str
    registry_row_count: int
    idempotent_replay_verified: bool
    final_holdout_evaluations_performed: int
    final_holdout_observed: bool
    final_holdout_consumed: bool
    holdout_permit_issued: bool
    holdout_permit_consumed: bool
    final_holdout_checkout_authorized: bool
    predictive_validation_passed: bool
    profitability_claim_authorized: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.contract_version != OSS3D3G_CONTRACT_VERSION:
            raise D2JPredictivePreregistrationIntegrityError("noncanonical D3G contract version")
        if self.protocol_id != PROTOCOL_ID or self.next_frontier != NEXT_FRONTIER:
            raise D2JPredictivePreregistrationGovernanceError("D3G protocol/frontier drifted")
        expected_roots = {
            "source_d3c_bundle_fingerprint": EXPECTED_D3C_BUNDLE_FINGERPRINT,
            "source_scientific_outcome_fingerprint": EXPECTED_D3C_SCIENTIFIC_OUTCOME,
            "source_d2h_preregistration_fingerprint": EXPECTED_D3C_D2H_PREREGISTRATION,
            "source_d2h_batch_evidence_fingerprint": EXPECTED_D3C_D2H_BATCH,
            "source_d2i_seal_fingerprint": EXPECTED_D3C_D2I_SEAL,
            "source_d3f_evidence_fingerprint": EXPECTED_D3F_EVIDENCE,
            "holdout_commitment_fingerprint": EXPECTED_Q1_COMMITMENT,
        }
        for name, expected in expected_roots.items():
            value = getattr(self, name)
            _require_hash(value, name)
            if value != expected:
                raise D2JPredictivePreregistrationIntegrityError(f"D3G frozen root drifted: {name}")
        for name in ("d2j_receipt_hash", "d2j_policy_fingerprint"):
            _require_hash(getattr(self, name), name)
        if self.selected_trial_id != EXPECTED_SELECTED_TRIAL:
            raise D2JPredictivePreregistrationIntegrityError("D3G selected trial drifted")
        if not isinstance(self.expected_holdout_authorization_id, str) or not self.expected_holdout_authorization_id.startswith("oss3d2j:"):
            raise D2JPredictivePreregistrationIntegrityError("invalid D2J expected authorization identity")
        if self.registry_row_count != 1 or self.idempotent_replay_verified is not True:
            raise D2JPredictivePreregistrationIntegrityError("D3G durable registry/idempotence proof failed")
        if self.final_holdout_evaluations_performed != 0:
            raise D2JPredictivePreregistrationGovernanceError("D3G may not evaluate FINAL_HOLDOUT")
        _deny_authority(self)

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def read_public_d3f_evidence(path: str | Path) -> ProtectedDualHoldoutMaterializationEvidence:
    target = Path(path).expanduser().resolve()
    if not target.is_file() or target.is_symlink():
        raise D2JPredictivePreregistrationIntegrityError("D3G D3F public evidence path is invalid")
    raw_bytes = target.read_bytes()
    if len(raw_bytes) > MAX_PUBLIC_EVIDENCE_BYTES:
        raise D2JPredictivePreregistrationGovernanceError("D3F public evidence exceeds D3G size bound")
    try:
        raw = raw_bytes.decode("utf-8")
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs, parse_constant=_reject_nonfinite)
    except UnicodeDecodeError as exc:
        raise D2JPredictivePreregistrationIntegrityError("D3F public evidence is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise D2JPredictivePreregistrationIntegrityError("D3F public evidence is invalid JSON") from exc
    if not isinstance(document, dict):
        raise D2JPredictivePreregistrationIntegrityError("D3F public evidence must be an object")
    fingerprint = document.pop("fingerprint", None)
    _require_hash(fingerprint, "D3F public evidence fingerprint")
    if raw != _canonical_json({**document, "fingerprint": fingerprint}) + "\n":
        raise D2JPredictivePreregistrationIntegrityError("D3F public evidence is not canonical JSON")
    try:
        evidence = ProtectedDualHoldoutMaterializationEvidence(**document)
    except Exception as exc:
        raise D2JPredictivePreregistrationIntegrityError("D3F public evidence contract reconstruction failed") from exc
    if evidence.fingerprint != fingerprint or fingerprint != EXPECTED_D3F_EVIDENCE:
        raise D2JPredictivePreregistrationIntegrityError("D3F public evidence fingerprint drifted")
    return evidence


def rehydrate_d3c_lineage(*, preregistration_payload: Mapping[str, object], batch_payload: Mapping[str, object], winner_payload: Mapping[str, object]) -> tuple[FamilyEvaluationPreregistration, FamilyEvaluationBatchEvidence, DevelopmentWinnerSelectionSeal]:
    """Rebuild exact D2H/D2I dataclasses solely from canonical D3C payloads."""
    try:
        prereg_document = dict(preregistration_payload)
        batch_document = dict(batch_payload)
        winner_document = dict(winner_payload)
        plan_payload = _require_mapping(prereg_document.get("d2e_plan"), "D2E plan")
        tournament_evidence_payload = _require_mapping(batch_document.get("tournament_evidence"), "D2E tournament evidence")
        tournament_payload = _require_mapping(tournament_evidence_payload.get("tournament"), "tournament")
        dataset = DevelopmentDatasetBinding(**dict(_require_mapping(plan_payload.get("dataset"), "D2E dataset")))
        runtime = RuntimeEnvironmentIdentity(**dict(_require_mapping(plan_payload.get("runtime_environment"), "runtime environment")))
        candidate_values = plan_payload.get("candidates")
        if not isinstance(candidate_values, list):
            raise D2JPredictivePreregistrationIntegrityError("D2E candidates must be a list")
        candidates = tuple(DevelopmentModelCandidate(**dict(_require_mapping(item, "candidate"))) for item in candidate_values)
        campaign_id = tournament_payload.get("campaign_id")
        tournament_id = tournament_payload.get("tournament_id")
        code_version = prereg_document.get("d2h_code_version")
        if not all(isinstance(value, str) and value for value in (campaign_id, tournament_id, code_version)):
            raise D2JPredictivePreregistrationIntegrityError("D3C plan lacks campaign/tournament/code identity")
        plan = build_oss3d2e_plan(tournament_campaign_id=campaign_id, tournament_id=tournament_id, dataset=dataset, runtime_environment=runtime, candidates=candidates, code_version=code_version)
        if plan.to_dict() != plan_payload or plan.fingerprint != prereg_document.get("d2e_plan_fingerprint"):
            raise D2JPredictivePreregistrationIntegrityError("D3C D2E plan does not deterministically rehydrate")
        binding_values = prereg_document.get("candidate_output_bindings")
        if not isinstance(binding_values, list):
            raise D2JPredictivePreregistrationIntegrityError("D2H candidate bindings must be a list")
        preregistration = FamilyEvaluationPreregistration(
            preregistration_version=prereg_document["preregistration_version"], d2f_plan_fingerprint=prereg_document["d2f_plan_fingerprint"], d2f_request_set_fingerprint=prereg_document["d2f_request_set_fingerprint"], d2h_code_version=prereg_document["d2h_code_version"], development_label_artifact_hash=prereg_document["development_label_artifact_hash"], candidate_output_bindings=tuple(FrozenCandidateOutputBinding(**dict(_require_mapping(item, "candidate binding"))) for item in binding_values), d2e_plan=plan, label_values_used=prereg_document["label_values_used"], development_metrics_computed=prereg_document["development_metrics_computed"], final_holdout_observed=prereg_document["final_holdout_observed"], execution_authorized=prereg_document["execution_authorized"], paper_execution_authorized=prereg_document["paper_execution_authorized"], capital_authority=prereg_document["capital_authority"], live_trading=prereg_document["live_trading"])
        if preregistration.to_dict() != prereg_document:
            raise D2JPredictivePreregistrationIntegrityError("D3C D2H preregistration payload changed on rehydration")
        entry_values = tournament_payload.get("entries")
        if not isinstance(entry_values, list):
            raise D2JPredictivePreregistrationIntegrityError("D2E tournament entries must be a list")
        tournament_entries: list[TournamentEntry] = []
        for item in entry_values:
            entry = _require_mapping(item, "tournament entry")
            metric = entry.get("metric_value")
            tournament_entries.append(TournamentEntry(rank=entry["rank"], trial_id=entry["trial_id"], strategy_id=entry["strategy_id"], strategy_version=entry["strategy_version"], status=TrialStatus(entry["status"]), eligible=entry["eligible"], metric_value=Decimal(metric) if metric is not None else None, failure_code=entry["failure_code"], result_hash=entry["result_hash"]))
        tournament = TournamentEvidence(tournament_id=tournament_payload["tournament_id"], campaign_id=tournament_payload["campaign_id"], metric_name=tournament_payload["metric_name"], direction=RankingDirection(tournament_payload["direction"]), spec_fingerprint=tournament_payload["spec_fingerprint"], result_universe_hash=tournament_payload["result_universe_hash"], entries=tuple(tournament_entries), winner_trial_id=tournament_payload["winner_trial_id"])
        if tournament.to_payload() != tournament_payload:
            raise D2JPredictivePreregistrationIntegrityError("D3C tournament payload changed on rehydration")
        holm_payload = _require_mapping(tournament_evidence_payload.get("holm"), "Holm evidence")
        holm = HolmEvidence(campaign_id=holm_payload["campaign_id"], family_size=holm_payload["family_size"], raw_p_values=dict(_require_mapping(holm_payload.get("raw_p_values"), "raw p-values")), adjusted_p_values=dict(_require_mapping(holm_payload.get("adjusted_p_values"), "adjusted p-values")), failed_trial_ids=tuple(holm_payload["failed_trial_ids"]))
        tournament_evidence = OSS3D2ETournamentEvidence(evidence_version=tournament_evidence_payload["evidence_version"], plan_fingerprint=tournament_evidence_payload["plan_fingerprint"], runtime_environment_hash=tournament_evidence_payload["runtime_environment_hash"], tournament=tournament, holm=holm, family_size=tournament_evidence_payload["family_size"], winner_trial_id=tournament_evidence_payload["winner_trial_id"], winner_primary_metric=tournament_evidence_payload["winner_primary_metric"], winner_raw_p_value=tournament_evidence_payload["winner_raw_p_value"], winner_holm_adjusted_p_value=tournament_evidence_payload["winner_holm_adjusted_p_value"], common_cross_section_key_hash=tournament_evidence_payload["common_cross_section_key_hash"], research_only=tournament_evidence_payload["research_only"], final_holdout_observed=tournament_evidence_payload["final_holdout_observed"], promotion_authorized=tournament_evidence_payload["promotion_authorized"], execution_authorized=tournament_evidence_payload["execution_authorized"], paper_execution_authorized=tournament_evidence_payload["paper_execution_authorized"], capital_authority=tournament_evidence_payload["capital_authority"], live_trading=tournament_evidence_payload["live_trading"])
        if tournament_evidence.to_dict() != tournament_evidence_payload or tournament_evidence.fingerprint != batch_document.get("tournament_evidence_hash"):
            raise D2JPredictivePreregistrationIntegrityError("D3C D2E evidence changed on rehydration")
        evaluation_values = batch_document.get("evaluations")
        if not isinstance(evaluation_values, list):
            raise D2JPredictivePreregistrationIntegrityError("D2H evaluations must be a list")
        batch = FamilyEvaluationBatchEvidence(evidence_version=batch_document["evidence_version"], preregistration_fingerprint=batch_document["preregistration_fingerprint"], d2e_plan_fingerprint=batch_document["d2e_plan_fingerprint"], d2h_code_version=batch_document["d2h_code_version"], development_label_artifact_hash=batch_document["development_label_artifact_hash"], shared_runner_code_hash=batch_document["shared_runner_code_hash"], runtime_environment_hash=batch_document["runtime_environment_hash"], evaluations=tuple(CandidateEvaluationBinding(**dict(_require_mapping(item, "evaluation binding"))) for item in evaluation_values), tournament_evidence=tournament_evidence, label_values_used_after_preregistration=batch_document["label_values_used_after_preregistration"], development_metrics_computed=batch_document["development_metrics_computed"], final_holdout_observed=batch_document["final_holdout_observed"], promotion_authorized=batch_document["promotion_authorized"], execution_authorized=batch_document["execution_authorized"], paper_execution_authorized=batch_document["paper_execution_authorized"], capital_authority=batch_document["capital_authority"], live_trading=batch_document["live_trading"])
        if batch.to_dict() != batch_document:
            raise D2JPredictivePreregistrationIntegrityError("D3C D2H batch payload changed on rehydration")
        winner = seal_development_winner(preregistration=preregistration, batch_evidence=batch)
        verify_development_winner_seal(seal=winner, preregistration=preregistration, batch_evidence=batch)
        if winner.to_dict() != winner_document:
            raise D2JPredictivePreregistrationIntegrityError("D3C D2I winner payload changed on rehydration")
        return preregistration, batch, winner
    except D2JPredictivePreregistrationError:
        raise
    except Exception as exc:
        raise D2JPredictivePreregistrationIntegrityError("D3C typed lineage reconstruction failed") from exc


def rehydrate_certified_d3c_lineage(bundle: DurableDevelopmentOutcomeBundle) -> tuple[FamilyEvaluationPreregistration, FamilyEvaluationBatchEvidence, DevelopmentWinnerSelectionSeal]:
    if not isinstance(bundle, DurableDevelopmentOutcomeBundle):
        raise TypeError("bundle must be DurableDevelopmentOutcomeBundle")
    if bundle.fingerprint != EXPECTED_D3C_BUNDLE_FINGERPRINT or bundle.scientific_outcome_fingerprint != EXPECTED_D3C_SCIENTIFIC_OUTCOME:
        raise D2JPredictivePreregistrationIntegrityError("D3G requires exact certified D3C science bundle")
    if bundle.d2h_preregistration_fingerprint != EXPECTED_D3C_D2H_PREREGISTRATION or bundle.d2h_batch_evidence_fingerprint != EXPECTED_D3C_D2H_BATCH:
        raise D2JPredictivePreregistrationIntegrityError("D3G D2H roots drifted")
    if bundle.d2i_winner_seal_fingerprint != EXPECTED_D3C_D2I_SEAL or bundle.selected_trial_id != EXPECTED_SELECTED_TRIAL:
        raise D2JPredictivePreregistrationIntegrityError("D3G D2I winner identity drifted")
    preregistration, batch, winner = rehydrate_d3c_lineage(preregistration_payload=bundle.d2h_preregistration, batch_payload=bundle.d2h_batch_evidence, winner_payload=bundle.d2i_winner_seal)
    if preregistration.fingerprint != EXPECTED_D3C_D2H_PREREGISTRATION or batch.fingerprint != EXPECTED_D3C_D2H_BATCH or winner.fingerprint != EXPECTED_D3C_D2I_SEAL:
        raise D2JPredictivePreregistrationIntegrityError("D3G typed D3C roots differ after reconstruction")
    return preregistration, batch, winner


def build_predictive_commitment_from_public_evidence(evidence: ProtectedDualHoldoutMaterializationEvidence) -> OSS3ProtectedFinalHoldoutCommitment:
    if not isinstance(evidence, ProtectedDualHoldoutMaterializationEvidence):
        raise TypeError("evidence must be ProtectedDualHoldoutMaterializationEvidence")
    if evidence.fingerprint != EXPECTED_D3F_EVIDENCE:
        raise D2JPredictivePreregistrationIntegrityError("D3G requires exact certified D3F public evidence")
    commitment = OSS3ProtectedFinalHoldoutCommitment(commitment_version=OSS3D2J_COMMITMENT_VERSION, source_campaign_id=SOURCE_CAMPAIGN_ID, research_split_hash=evidence.research_split_hash, source_universe_hash=evidence.research_universe_identity_hash, label_definition_hash=evidence.label_definition_hash, feature_artifact_hash=evidence.predictive_feature_artifact_hash, label_artifact_hash=evidence.predictive_label_artifact_hash, evaluation_keyset_hash=evidence.predictive_evaluation_keyset_hash, cross_section_key_hash=evidence.predictive_cross_section_key_hash, partition_start=evidence.predictive_partition_start, partition_end=evidence.predictive_partition_end, row_count=evidence.predictive_row_count, cross_section_count=evidence.predictive_cross_section_count, minimum_cross_section_observation_count=evidence.predictive_minimum_cross_section_observation_count, label_values_exposed=False, final_holdout_observed=False)
    if commitment.fingerprint != evidence.predictive_commitment_fingerprint or commitment.fingerprint != EXPECTED_Q1_COMMITMENT:
        raise D2JPredictivePreregistrationIntegrityError("D3G Q1 commitment does not reproduce certified D3F identity")
    return commitment


def preregister_predictive_final_holdout(*, d3c_bundle_path: str | Path, d3f_public_evidence_path: str | Path, registry_path: str | Path) -> D2JPredictivePreregistrationEvidence:
    bundle = read_durable_development_outcome(d3c_bundle_path)
    preregistration, batch, winner = rehydrate_certified_d3c_lineage(bundle)
    d3f = read_public_d3f_evidence(d3f_public_evidence_path)
    commitment = build_predictive_commitment_from_public_evidence(d3f)
    registry = SQLiteOSS3FinalHoldoutProtocolRegistry(registry_path)
    first = registry.preregister_and_record(protocol_id=PROTOCOL_ID, seal=winner, preregistration=preregistration, batch_evidence=batch, holdout_commitment=commitment)
    second = registry.preregister_and_record(protocol_id=PROTOCOL_ID, seal=winner, preregistration=preregistration, batch_evidence=batch, holdout_commitment=commitment)
    if first != second:
        raise D2JPredictivePreregistrationIntegrityError("D2J exact replay is not idempotent")
    read_back = read_oss3d2j_protocol_read_only(registry.path, seal_fingerprint=winner.fingerprint)
    if read_back != first:
        raise D2JPredictivePreregistrationIntegrityError("D2J read-only receipt differs from recorded receipt")
    return _build_stage_evidence(bundle=bundle, d3f=d3f, winner=winner, commitment=commitment, receipt=first, row_count=_registry_row_count(registry.path))


def write_d3g_evidence(evidence: D2JPredictivePreregistrationEvidence, path: str | Path) -> None:
    if not isinstance(evidence, D2JPredictivePreregistrationEvidence):
        raise TypeError("evidence must be D2JPredictivePreregistrationEvidence")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = evidence.to_dict()
    payload["fingerprint"] = evidence.fingerprint
    raw = _canonical_json(payload) + "\n"
    for forbidden in ('"feature_rows"', '"label_rows"', '"prediction_rows"', '"label_values"', '"outcomes"'):
        if forbidden in raw:
            raise D2JPredictivePreregistrationGovernanceError("D3G attempted to serialize protected holdout values")
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(raw, encoding="utf-8")
    temporary.replace(target)


def _build_stage_evidence(*, bundle: DurableDevelopmentOutcomeBundle, d3f: ProtectedDualHoldoutMaterializationEvidence, winner: DevelopmentWinnerSelectionSeal, commitment: OSS3ProtectedFinalHoldoutCommitment, receipt: OSS3FinalHoldoutProtocolReceipt, row_count: int) -> D2JPredictivePreregistrationEvidence:
    if receipt.source_d2i_seal_fingerprint != winner.fingerprint or receipt.holdout_commitment_fingerprint != commitment.fingerprint:
        raise D2JPredictivePreregistrationIntegrityError("D2J receipt does not bind exact D3C/D3F roots")
    return D2JPredictivePreregistrationEvidence(contract_version=OSS3D3G_CONTRACT_VERSION, protocol_id=receipt.protocol_id, next_frontier=NEXT_FRONTIER, source_d3c_bundle_fingerprint=bundle.fingerprint, source_scientific_outcome_fingerprint=bundle.scientific_outcome_fingerprint, source_d2h_preregistration_fingerprint=receipt.source_d2h_preregistration_fingerprint, source_d2h_batch_evidence_fingerprint=receipt.source_d2h_batch_evidence_fingerprint, source_d2i_seal_fingerprint=receipt.source_d2i_seal_fingerprint, selected_trial_id=receipt.selected_trial_id, source_d3f_evidence_fingerprint=d3f.fingerprint, holdout_commitment_fingerprint=receipt.holdout_commitment_fingerprint, d2j_receipt_hash=receipt.receipt_hash, d2j_policy_fingerprint=receipt.policy_fingerprint, expected_holdout_authorization_id=receipt.expected_holdout_authorization_id, registry_row_count=row_count, idempotent_replay_verified=True, final_holdout_evaluations_performed=0, final_holdout_observed=receipt.final_holdout_observed, final_holdout_consumed=receipt.final_holdout_consumed, holdout_permit_issued=receipt.holdout_permit_issued, holdout_permit_consumed=receipt.holdout_permit_consumed, final_holdout_checkout_authorized=receipt.final_holdout_checkout_authorized, predictive_validation_passed=receipt.predictive_validation_passed, profitability_claim_authorized=receipt.profitability_claim_authorized, promotion_authorized=receipt.promotion_authorized, execution_authorized=receipt.execution_authorized, paper_execution_authorized=receipt.paper_execution_authorized, capital_authority=receipt.capital_authority, live_trading=receipt.live_trading)


def _registry_row_count(path: str | Path) -> int:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise D2JPredictivePreregistrationIntegrityError("D2J registry is missing")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute("SELECT COUNT(*) FROM oss3_final_holdout_protocols").fetchone()
    finally:
        conn.close()
    if row is None or row[0] != 1:
        raise D2JPredictivePreregistrationIntegrityError("D3G D2J registry must contain exactly one protocol")
    return int(row[0])


def _deny_authority(evidence: D2JPredictivePreregistrationEvidence) -> None:
    if evidence.final_holdout_observed or evidence.final_holdout_consumed:
        raise D2JPredictivePreregistrationGovernanceError("D3G cannot observe or consume FINAL_HOLDOUT")
    if evidence.holdout_permit_issued or evidence.holdout_permit_consumed or evidence.final_holdout_checkout_authorized:
        raise D2JPredictivePreregistrationGovernanceError("D3G is not holdout authorization")
    if evidence.predictive_validation_passed or evidence.profitability_claim_authorized:
        raise D2JPredictivePreregistrationGovernanceError("D3G cannot claim predictive validation or profitability")
    if evidence.promotion_authorized or evidence.execution_authorized or evidence.paper_execution_authorized:
        raise D2JPredictivePreregistrationGovernanceError("D3G cannot authorize promotion or execution")
    if evidence.capital_authority != "NONE" or evidence.live_trading != "BLOCKED":
        raise D2JPredictivePreregistrationGovernanceError("D3G cannot grant capital or LIVE authority")


def _require_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise D2JPredictivePreregistrationIntegrityError(f"{name} must be an object")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise D2JPredictivePreregistrationIntegrityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> object:
    raise D2JPredictivePreregistrationIntegrityError(f"non-finite JSON number: {value}")


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(character not in _HASH_CHARS for character in value):
        raise D2JPredictivePreregistrationIntegrityError(f"invalid {name}")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()
