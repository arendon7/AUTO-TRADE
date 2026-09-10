"""OSS-3D3C durable DEVELOPMENT outcome bundle.

D3C serializes the already-completed D3A DEVELOPMENT result together with the
exact D2H preregistration, D2H batch evidence and D2I winner seal that produced
one observed replay.  It also binds that replay to the previously certified D3A
baseline through a deliberately stable scientific-outcome identity.

The stable identity includes market/data lineage, frozen family/request set,
exact prediction artifacts, structural profiles, DEVELOPMENT metrics, p-values,
winner and authority state.  It deliberately excludes container fingerprints
whose only observed drift is runtime/environment provenance.  Those provenance
fingerprints are not discarded: the current complete D2H/D2I lineage remains
embedded separately in the bundle.

D3C never reranks candidates, recomputes DEVELOPMENT metrics, observes
FINAL_HOLDOUT or grants execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

from autotrade.research.oss3_concrete_model_family import CANONICAL_CANDIDATES
from labs.oss3_qlib.development_winner_seal import (
    NEXT_FRONTIER,
    OSS3D2I_CONTRACT_VERSION,
    DevelopmentWinnerSelectionSeal,
    verify_development_winner_seal,
)
from labs.oss3_qlib.family_evaluation_batch import (
    OSS3D2H_BATCH_EVIDENCE_VERSION,
    OSS3D2H_PREREGISTRATION_VERSION,
    FamilyEvaluationBatchEvidence,
    FamilyEvaluationPreregistration,
)


OSS3D3C_CONTRACT_VERSION = "OSS3D3C_DURABLE_DEVELOPMENT_OUTCOME_BUNDLE_V1"
SOURCE_D3A_EVIDENCE_VERSION = "OSS3D3A_REAL_DEVELOPMENT_CAMPAIGN_EVIDENCE_V1"
SOURCE_D3A_COMPLETED_STATUS = "COMPLETED"
SCIENTIFIC_IDENTITY_POLICY = "D3A_STABLE_SCIENTIFIC_OUTPUT_EXCLUDING_RUNTIME_PROVENANCE_V1"
MAX_BUNDLE_BYTES = 4 * 1024 * 1024

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class DurableDevelopmentOutcomeError(RuntimeError):
    """Base OSS-3D3C failure."""


class DurableDevelopmentOutcomeIntegrityError(DurableDevelopmentOutcomeError):
    """Serialized DEVELOPMENT lineage is incomplete, noncanonical or cross-wired."""


class DurableDevelopmentOutcomeGovernanceError(DurableDevelopmentOutcomeError):
    """Attempted D3C operation exceeds DEVELOPMENT-only evidence authority."""


@dataclass(frozen=True, slots=True)
class DurableDevelopmentOutcomeBundle:
    contract_version: str
    next_frontier: str
    scientific_identity_policy: str
    certified_d3a_baseline_fingerprint: str
    source_d3a_evidence_fingerprint: str
    scientific_outcome_fingerprint: str
    d2h_preregistration_fingerprint: str
    d2h_batch_evidence_fingerprint: str
    d2i_winner_seal_fingerprint: str
    certified_d3a_baseline_json: str
    source_d3a_evidence_json: str
    d2h_preregistration_json: str
    d2h_batch_evidence_json: str
    d2i_winner_seal_json: str
    final_holdout_observed: bool = False
    final_holdout_authorized: bool = False
    holdout_permit_consumed: bool = False
    profitability_claim_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.contract_version != OSS3D3C_CONTRACT_VERSION:
            raise DurableDevelopmentOutcomeIntegrityError("noncanonical D3C contract version")
        if self.next_frontier != NEXT_FRONTIER:
            raise DurableDevelopmentOutcomeGovernanceError(
                "D3C next frontier must remain D2J preregistration only"
            )
        if self.scientific_identity_policy != SCIENTIFIC_IDENTITY_POLICY:
            raise DurableDevelopmentOutcomeGovernanceError(
                "D3C scientific identity policy drifted"
            )
        for name in (
            "certified_d3a_baseline_fingerprint",
            "source_d3a_evidence_fingerprint",
            "scientific_outcome_fingerprint",
            "d2h_preregistration_fingerprint",
            "d2h_batch_evidence_fingerprint",
            "d2i_winner_seal_fingerprint",
        ):
            _require_hash(getattr(self, name), name)
        _deny_authority(self)

        baseline = _parse_canonical_document(
            self.certified_d3a_baseline_json,
            "certified D3A baseline",
        )
        d3a = _parse_canonical_document(
            self.source_d3a_evidence_json,
            "source D3A replay evidence",
        )
        d2h = _parse_canonical_document(
            self.d2h_preregistration_json,
            "D2H preregistration",
        )
        batch = _parse_canonical_document(
            self.d2h_batch_evidence_json,
            "D2H batch evidence",
        )
        winner = _parse_canonical_document(
            self.d2i_winner_seal_json,
            "D2I winner seal",
        )

        if _hash_payload(baseline) != self.certified_d3a_baseline_fingerprint:
            raise DurableDevelopmentOutcomeIntegrityError(
                "D3C certified D3A baseline fingerprint mismatch"
            )
        if _hash_payload(d3a) != self.source_d3a_evidence_fingerprint:
            raise DurableDevelopmentOutcomeIntegrityError(
                "D3C source D3A replay fingerprint mismatch"
            )
        if _hash_payload(d2h) != self.d2h_preregistration_fingerprint:
            raise DurableDevelopmentOutcomeIntegrityError(
                "D3C D2H preregistration fingerprint mismatch"
            )
        if _hash_payload(batch) != self.d2h_batch_evidence_fingerprint:
            raise DurableDevelopmentOutcomeIntegrityError(
                "D3C D2H batch fingerprint mismatch"
            )
        if _hash_payload(winner) != self.d2i_winner_seal_fingerprint:
            raise DurableDevelopmentOutcomeIntegrityError(
                "D3C D2I winner fingerprint mismatch"
            )

        baseline_science = d3a_scientific_outcome_fingerprint(baseline)
        replay_science = d3a_scientific_outcome_fingerprint(d3a)
        if baseline_science != replay_science:
            raise DurableDevelopmentOutcomeIntegrityError(
                "D3C replay changed stable D3A scientific outcome"
            )
        if replay_science != self.scientific_outcome_fingerprint:
            raise DurableDevelopmentOutcomeIntegrityError(
                "D3C scientific outcome fingerprint mismatch"
            )

        _verify_payload_links(
            d3a=d3a,
            d2h=d2h,
            batch=batch,
            winner=winner,
            preregistration_fingerprint=self.d2h_preregistration_fingerprint,
            batch_fingerprint=self.d2h_batch_evidence_fingerprint,
            winner_fingerprint=self.d2i_winner_seal_fingerprint,
        )
        _deny_nested_authority(baseline, "certified D3A baseline")

    @property
    def fingerprint(self) -> str:
        return _hash_payload(self.to_dict())

    @property
    def certified_d3a_baseline(self) -> dict[str, object]:
        return _parse_canonical_document(
            self.certified_d3a_baseline_json,
            "certified D3A baseline",
        )

    @property
    def source_d3a_evidence(self) -> dict[str, object]:
        return _parse_canonical_document(
            self.source_d3a_evidence_json,
            "source D3A replay evidence",
        )

    @property
    def d2h_preregistration(self) -> dict[str, object]:
        return _parse_canonical_document(
            self.d2h_preregistration_json,
            "D2H preregistration",
        )

    @property
    def d2h_batch_evidence(self) -> dict[str, object]:
        return _parse_canonical_document(
            self.d2h_batch_evidence_json,
            "D2H batch evidence",
        )

    @property
    def d2i_winner_seal(self) -> dict[str, object]:
        return _parse_canonical_document(
            self.d2i_winner_seal_json,
            "D2I winner seal",
        )

    @property
    def selected_trial_id(self) -> str:
        value = self.d2i_winner_seal.get("selected_trial_id")
        if not isinstance(value, str) or not value:
            raise DurableDevelopmentOutcomeIntegrityError(
                "D3C winner lacks selected_trial_id"
            )
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "next_frontier": self.next_frontier,
            "scientific_identity_policy": self.scientific_identity_policy,
            "certified_d3a_baseline_fingerprint": self.certified_d3a_baseline_fingerprint,
            "source_d3a_evidence_fingerprint": self.source_d3a_evidence_fingerprint,
            "scientific_outcome_fingerprint": self.scientific_outcome_fingerprint,
            "d2h_preregistration_fingerprint": self.d2h_preregistration_fingerprint,
            "d2h_batch_evidence_fingerprint": self.d2h_batch_evidence_fingerprint,
            "d2i_winner_seal_fingerprint": self.d2i_winner_seal_fingerprint,
            "certified_d3a_baseline": self.certified_d3a_baseline,
            "source_d3a_evidence": self.source_d3a_evidence,
            "d2h_preregistration": self.d2h_preregistration,
            "d2h_batch_evidence": self.d2h_batch_evidence,
            "d2i_winner_seal": self.d2i_winner_seal,
            "final_holdout_observed": self.final_holdout_observed,
            "final_holdout_authorized": self.final_holdout_authorized,
            "holdout_permit_consumed": self.holdout_permit_consumed,
            "profitability_claim_authorized": self.profitability_claim_authorized,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


def d3a_scientific_outcome_projection(
    document: Mapping[str, object],
) -> dict[str, object]:
    """Return the stable D3A scientific result, excluding runtime provenance IDs.

    Excluded identities are deliberately limited to values transitively bound to
    the observed environment attestation: full candidate-output hashes, D2S/D2H
    container fingerprints, D2E tournament fingerprint, D2I seal fingerprint
    and D2D evaluation-artifact hashes.  Exact prediction artifact hashes,
    structural evidence, persisted metrics/p-values and the winner remain in the
    scientific identity.
    """
    if not isinstance(document, Mapping):
        raise TypeError("D3A scientific outcome source must be a mapping")
    if document.get("evidence_version") != SOURCE_D3A_EVIDENCE_VERSION:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C scientific identity requires canonical D3A evidence"
        )
    if document.get("status") != SOURCE_D3A_COMPLETED_STATUS:
        raise DurableDevelopmentOutcomeGovernanceError(
            "D3C scientific identity requires completed D3A evidence"
        )

    expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
    structural_profiles = document.get("structural_profiles")
    if not isinstance(structural_profiles, list) or tuple(
        item.get("candidate_id")
        for item in structural_profiles
        if isinstance(item, dict)
    ) != expected_ids:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C scientific identity requires exact six structural profiles"
        )
    candidate_results = document.get("candidate_results")
    if not isinstance(candidate_results, list) or tuple(
        item.get("candidate_id")
        for item in candidate_results
        if isinstance(item, dict)
    ) != expected_ids:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C scientific identity requires exact six candidate results"
        )

    stable_results: list[dict[str, object]] = []
    for item in candidate_results:
        assert isinstance(item, dict)
        required = (
            "candidate_id",
            "status",
            "structural_profile_hash",
            "prediction_artifact_hash",
            "primary_metric",
            "raw_p_value",
        )
        if any(name not in item for name in required):
            raise DurableDevelopmentOutcomeIntegrityError(
                "D3C candidate result lacks stable scientific field"
            )
        stable_results.append({name: item[name] for name in required})

    stable_fields = (
        "evidence_version",
        "status",
        "d2w_evidence_fingerprint",
        "research_split_hash",
        "raw_training_source_hash",
        "raw_development_source_hash",
        "d2r_receipt_hash",
        "training_bundle_hash",
        "training_feature_artifact_hash",
        "training_label_artifact_hash",
        "development_feature_artifact_hash",
        "d2f_plan_fingerprint",
        "d2f_request_set_fingerprint",
        "structural_policy",
        "structural_profiles",
        "evaluable_candidate_ids",
        "development_label_artifact_hash",
        "selected_trial_id",
        "winner_primary_metric",
        "winner_raw_p_value",
        "winner_holm_adjusted_p_value",
        "predictions_frozen_before_development_labels",
        "development_labels_materialized",
        "development_metrics_computed",
        "family_retuned",
        "fallback_candidate_used",
        "reselection_allowed",
        "statistical_significance_claim_authorized",
        "profitability_claim_authorized",
        "final_holdout_observed",
        "final_holdout_authorized",
        "holdout_permit_consumed",
        "promotion_authorized",
        "execution_authorized",
        "paper_execution_authorized",
        "capital_authority",
        "live_trading",
    )
    missing = tuple(name for name in stable_fields if name not in document)
    if missing:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C D3A scientific source lacks fields: " + ",".join(missing)
        )
    projection = {name: document[name] for name in stable_fields}
    projection["candidate_results"] = stable_results
    _deny_nested_authority(projection, "D3A scientific identity")
    return projection


def d3a_scientific_outcome_fingerprint(document: Mapping[str, object]) -> str:
    return _hash_payload(d3a_scientific_outcome_projection(document))


def build_durable_development_outcome(
    *,
    certified_d3a_baseline: object,
    source_d3a_evidence: object,
    preregistration: FamilyEvaluationPreregistration,
    batch_evidence: FamilyEvaluationBatchEvidence,
    winner: DevelopmentWinnerSelectionSeal,
) -> DurableDevelopmentOutcomeBundle:
    """Freeze a current complete D3A lineage after proving stable science equality."""
    if not isinstance(preregistration, FamilyEvaluationPreregistration):
        raise TypeError("preregistration must be FamilyEvaluationPreregistration")
    if not isinstance(batch_evidence, FamilyEvaluationBatchEvidence):
        raise TypeError("batch_evidence must be FamilyEvaluationBatchEvidence")
    if not isinstance(winner, DevelopmentWinnerSelectionSeal):
        raise TypeError("winner must be DevelopmentWinnerSelectionSeal")

    try:
        verify_development_winner_seal(
            seal=winner,
            preregistration=preregistration,
            batch_evidence=batch_evidence,
        )
    except Exception as exc:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C D2I winner does not rebind to supplied D2H evidence"
        ) from exc

    baseline_payload, baseline_fingerprint = _object_payload_and_fingerprint(
        certified_d3a_baseline,
        "certified D3A baseline",
    )
    source_payload, source_fingerprint = _object_payload_and_fingerprint(
        source_d3a_evidence,
        "source D3A replay",
    )
    baseline_science = d3a_scientific_outcome_fingerprint(baseline_payload)
    source_science = d3a_scientific_outcome_fingerprint(source_payload)
    if baseline_science != source_science:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C source replay differs scientifically from certified D3A baseline"
        )

    return DurableDevelopmentOutcomeBundle(
        contract_version=OSS3D3C_CONTRACT_VERSION,
        next_frontier=NEXT_FRONTIER,
        scientific_identity_policy=SCIENTIFIC_IDENTITY_POLICY,
        certified_d3a_baseline_fingerprint=baseline_fingerprint,
        source_d3a_evidence_fingerprint=source_fingerprint,
        scientific_outcome_fingerprint=source_science,
        d2h_preregistration_fingerprint=preregistration.fingerprint,
        d2h_batch_evidence_fingerprint=batch_evidence.fingerprint,
        d2i_winner_seal_fingerprint=winner.fingerprint,
        certified_d3a_baseline_json=_canonical_json(baseline_payload),
        source_d3a_evidence_json=_canonical_json(source_payload),
        d2h_preregistration_json=_canonical_json(preregistration.to_dict()),
        d2h_batch_evidence_json=_canonical_json(batch_evidence.to_dict()),
        d2i_winner_seal_json=_canonical_json(winner.to_dict()),
    )


def write_durable_development_outcome(
    bundle: DurableDevelopmentOutcomeBundle,
    path: str | Path,
) -> None:
    if not isinstance(bundle, DurableDevelopmentOutcomeBundle):
        raise TypeError("bundle must be DurableDevelopmentOutcomeBundle")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = bundle.to_dict()
    payload["artifact_hash"] = bundle.fingerprint
    raw = _canonical_json(payload).encode("utf-8") + b"\n"
    if len(raw) > MAX_BUNDLE_BYTES:
        raise DurableDevelopmentOutcomeGovernanceError(
            "D3C serialized bundle exceeds size bound"
        )
    staging = target.with_name(target.name + ".tmp")
    staging.write_bytes(raw)
    staging.replace(target)


def read_durable_development_outcome(
    path: str | Path,
) -> DurableDevelopmentOutcomeBundle:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_BUNDLE_BYTES:
        raise DurableDevelopmentOutcomeGovernanceError(
            "D3C serialized bundle exceeds size bound"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C artifact is not UTF-8"
        ) from exc
    document = _strict_json_loads(text)
    if not isinstance(document, dict):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C top level must be an object"
        )
    expected_keys = {
        "contract_version",
        "next_frontier",
        "scientific_identity_policy",
        "certified_d3a_baseline_fingerprint",
        "source_d3a_evidence_fingerprint",
        "scientific_outcome_fingerprint",
        "d2h_preregistration_fingerprint",
        "d2h_batch_evidence_fingerprint",
        "d2i_winner_seal_fingerprint",
        "certified_d3a_baseline",
        "source_d3a_evidence",
        "d2h_preregistration",
        "d2h_batch_evidence",
        "d2i_winner_seal",
        "final_holdout_observed",
        "final_holdout_authorized",
        "holdout_permit_consumed",
        "profitability_claim_authorized",
        "promotion_authorized",
        "execution_authorized",
        "paper_execution_authorized",
        "capital_authority",
        "live_trading",
        "artifact_hash",
    }
    if set(document) != expected_keys:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C top-level schema drifted"
        )
    artifact_hash = document.pop("artifact_hash")
    _require_hash(artifact_hash, "artifact_hash")

    for name in (
        "certified_d3a_baseline",
        "source_d3a_evidence",
        "d2h_preregistration",
        "d2h_batch_evidence",
        "d2i_winner_seal",
    ):
        if not isinstance(document[name], dict):
            raise DurableDevelopmentOutcomeIntegrityError(
                f"D3C {name} must be an object"
            )

    bundle = DurableDevelopmentOutcomeBundle(
        contract_version=document["contract_version"],
        next_frontier=document["next_frontier"],
        scientific_identity_policy=document["scientific_identity_policy"],
        certified_d3a_baseline_fingerprint=document[
            "certified_d3a_baseline_fingerprint"
        ],
        source_d3a_evidence_fingerprint=document[
            "source_d3a_evidence_fingerprint"
        ],
        scientific_outcome_fingerprint=document["scientific_outcome_fingerprint"],
        d2h_preregistration_fingerprint=document["d2h_preregistration_fingerprint"],
        d2h_batch_evidence_fingerprint=document["d2h_batch_evidence_fingerprint"],
        d2i_winner_seal_fingerprint=document["d2i_winner_seal_fingerprint"],
        certified_d3a_baseline_json=_canonical_json(
            document["certified_d3a_baseline"]
        ),
        source_d3a_evidence_json=_canonical_json(document["source_d3a_evidence"]),
        d2h_preregistration_json=_canonical_json(document["d2h_preregistration"]),
        d2h_batch_evidence_json=_canonical_json(document["d2h_batch_evidence"]),
        d2i_winner_seal_json=_canonical_json(document["d2i_winner_seal"]),
        final_holdout_observed=document["final_holdout_observed"],
        final_holdout_authorized=document["final_holdout_authorized"],
        holdout_permit_consumed=document["holdout_permit_consumed"],
        profitability_claim_authorized=document["profitability_claim_authorized"],
        promotion_authorized=document["promotion_authorized"],
        execution_authorized=document["execution_authorized"],
        paper_execution_authorized=document["paper_execution_authorized"],
        capital_authority=document["capital_authority"],
        live_trading=document["live_trading"],
    )
    if bundle.fingerprint != artifact_hash:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C artifact hash mismatch"
        )
    return bundle


def _object_payload_and_fingerprint(
    value: object,
    name: str,
) -> tuple[dict[str, object], str]:
    to_dict = getattr(value, "to_dict", None)
    fingerprint = getattr(value, "fingerprint", None)
    if not callable(to_dict) or not isinstance(fingerprint, str):
        raise TypeError(f"{name} must expose to_dict() and fingerprint")
    payload = to_dict()
    if not isinstance(payload, Mapping):
        raise TypeError(f"{name}.to_dict() must return a mapping")
    result = dict(payload)
    _require_hash(fingerprint, f"{name} fingerprint")
    if _hash_payload(result) != fingerprint:
        raise DurableDevelopmentOutcomeIntegrityError(
            f"D3C {name} object fingerprint is inconsistent"
        )
    return result, fingerprint


def _verify_payload_links(
    *,
    d3a: Mapping[str, object],
    d2h: Mapping[str, object],
    batch: Mapping[str, object],
    winner: Mapping[str, object],
    preregistration_fingerprint: str,
    batch_fingerprint: str,
    winner_fingerprint: str,
) -> None:
    if (
        d3a.get("evidence_version") != SOURCE_D3A_EVIDENCE_VERSION
        or d3a.get("status") != SOURCE_D3A_COMPLETED_STATUS
    ):
        raise DurableDevelopmentOutcomeGovernanceError(
            "D3C requires a completed canonical D3A campaign"
        )
    if d2h.get("preregistration_version") != OSS3D2H_PREREGISTRATION_VERSION:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C noncanonical D2H preregistration"
        )
    if batch.get("evidence_version") != OSS3D2H_BATCH_EVIDENCE_VERSION:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C noncanonical D2H batch evidence"
        )
    if (
        winner.get("contract_version") != OSS3D2I_CONTRACT_VERSION
        or winner.get("next_frontier") != NEXT_FRONTIER
    ):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C noncanonical D2I winner seal"
        )

    for actual, expected, message in (
        (
            d3a.get("d2h_preregistration_fingerprint"),
            preregistration_fingerprint,
            "D3A/D2H preregistration",
        ),
        (
            d3a.get("d2h_batch_evidence_fingerprint"),
            batch_fingerprint,
            "D3A/D2H batch",
        ),
        (
            d3a.get("d2i_winner_seal_fingerprint"),
            winner_fingerprint,
            "D3A/D2I winner",
        ),
        (
            winner.get("preregistration_fingerprint"),
            preregistration_fingerprint,
            "D2I/D2H preregistration",
        ),
        (
            winner.get("d2h_batch_evidence_fingerprint"),
            batch_fingerprint,
            "D2I/D2H batch",
        ),
        (
            batch.get("preregistration_fingerprint"),
            preregistration_fingerprint,
            "batch/D2H preregistration",
        ),
    ):
        if actual != expected:
            raise DurableDevelopmentOutcomeIntegrityError(
                f"D3C cross-link mismatch: {message}"
            )

    if d3a.get("d2f_plan_fingerprint") != d2h.get("d2f_plan_fingerprint"):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C D3A/D2H D2F plan mismatch"
        )
    if d3a.get("d2f_request_set_fingerprint") != d2h.get(
        "d2f_request_set_fingerprint"
    ):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C D3A/D2H request-set mismatch"
        )
    if d3a.get("development_label_artifact_hash") != d2h.get(
        "development_label_artifact_hash"
    ):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C DEVELOPMENT label artifact mismatch"
        )
    if batch.get("development_label_artifact_hash") != d2h.get(
        "development_label_artifact_hash"
    ):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C D2H/batch label artifact mismatch"
        )
    if batch.get("d2e_plan_fingerprint") != d2h.get("d2e_plan_fingerprint"):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C D2E plan fingerprint mismatch"
        )
    if batch.get("tournament_evidence_hash") != d3a.get(
        "d2e_tournament_evidence_fingerprint"
    ):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C D3A/batch tournament mismatch"
        )
    if winner.get("d2e_tournament_evidence_fingerprint") != batch.get(
        "tournament_evidence_hash"
    ):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C winner/batch tournament mismatch"
        )
    if winner.get("shared_runner_code_hash") != batch.get("shared_runner_code_hash"):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C winner/batch runner mismatch"
        )
    if winner.get("runtime_environment_hash") != batch.get(
        "runtime_environment_hash"
    ):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C winner/batch runtime mismatch"
        )

    selected = winner.get("selected_trial_id")
    if not isinstance(selected, str) or not selected:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C winner selected_trial_id missing"
        )
    if d3a.get("selected_trial_id") != selected:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C D3A/D2I selected winner mismatch"
        )
    for d3a_name, winner_name in (
        ("winner_primary_metric", "winner_primary_metric"),
        ("winner_raw_p_value", "winner_raw_p_value"),
        ("winner_holm_adjusted_p_value", "winner_holm_adjusted_p_value"),
    ):
        if d3a.get(d3a_name) != winner.get(winner_name):
            raise DurableDevelopmentOutcomeIntegrityError(
                f"D3C D3A/D2I metric mismatch: {d3a_name}"
            )

    expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
    outputs = d3a.get("candidate_output_hashes")
    if not isinstance(outputs, list) or tuple(
        item[0]
        for item in outputs
        if isinstance(item, list) and len(item) == 2
    ) != expected_ids:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C D3A output family differs from exact D2F six"
        )
    bindings = d2h.get("candidate_output_bindings")
    if not isinstance(bindings, list) or tuple(
        item.get("candidate_id")
        for item in bindings
        if isinstance(item, dict)
    ) != expected_ids:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C D2H binding family differs from exact D2F six"
        )

    selected_bindings = [
        item
        for item in bindings
        if isinstance(item, dict) and item.get("candidate_id") == selected
    ]
    evaluations = batch.get("evaluations")
    if not isinstance(evaluations, list):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C D2H batch evaluations missing"
        )
    selected_evaluations = [
        item
        for item in evaluations
        if isinstance(item, dict) and item.get("candidate_id") == selected
    ]
    if len(selected_bindings) != 1 or len(selected_evaluations) != 1:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C winner lacks unique D2H/batch lineage"
        )
    binding = selected_bindings[0]
    evaluation = selected_evaluations[0]
    for winner_name, expected in (
        ("request_hash", binding.get("request_hash")),
        ("prediction_artifact_hash", binding.get("prediction_artifact_hash")),
        ("prediction_receipt_hash", binding.get("prediction_receipt_hash")),
        ("environment_attestation_hash", binding.get("environment_attestation_hash")),
        ("d2g_run_evidence_hash", binding.get("d2g_run_evidence_hash")),
        ("model_config_hash", binding.get("model_config_hash")),
        ("shared_runner_code_hash", binding.get("shared_runner_code_hash")),
        ("runtime_environment_hash", binding.get("runtime_environment_hash")),
        (
            "d2d_evaluation_artifact_hash",
            evaluation.get("d2d_evaluation_artifact_hash"),
        ),
    ):
        if winner.get(winner_name) != expected:
            raise DurableDevelopmentOutcomeIntegrityError(
                f"D3C winner lineage mismatch: {winner_name}"
            )

    if (
        d2h.get("label_values_used") is not False
        or d2h.get("development_metrics_computed") is not False
    ):
        raise DurableDevelopmentOutcomeGovernanceError(
            "D3C requires pre-metric D2H preregistration semantics"
        )
    if (
        batch.get("label_values_used_after_preregistration") is not True
        or batch.get("development_metrics_computed") is not True
    ):
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C completed D2H batch semantics are incomplete"
        )

    _deny_nested_authority(d3a, "D3A replay")
    _deny_nested_authority(d2h, "D2H preregistration")
    _deny_nested_authority(batch, "D2H batch")
    _deny_nested_authority(winner, "D2I winner")


def _deny_nested_authority(document: Mapping[str, object], source: str) -> None:
    for key in (
        "final_holdout_observed",
        "final_holdout_authorized",
        "holdout_permit_consumed",
        "statistical_significance_claim_authorized",
        "alpha_claim_authorized",
        "profitability_claim_authorized",
        "promotion_authorized",
        "execution_authorized",
        "paper_execution_authorized",
    ):
        if document.get(key) is True:
            raise DurableDevelopmentOutcomeGovernanceError(
                f"D3C {source} escalates authority: {key}"
            )
    if document.get("capital_authority", "NONE") != "NONE":
        raise DurableDevelopmentOutcomeGovernanceError(
            f"D3C {source} grants capital authority"
        )
    if document.get("live_trading", "BLOCKED") != "BLOCKED":
        raise DurableDevelopmentOutcomeGovernanceError(
            f"D3C {source} grants LIVE authority"
        )


def _deny_authority(bundle: DurableDevelopmentOutcomeBundle) -> None:
    if (
        bundle.final_holdout_observed
        or bundle.final_holdout_authorized
        or bundle.holdout_permit_consumed
        or bundle.profitability_claim_authorized
        or bundle.promotion_authorized
        or bundle.execution_authorized
        or bundle.paper_execution_authorized
    ):
        raise DurableDevelopmentOutcomeGovernanceError(
            "D3C cannot escalate research authority"
        )
    if bundle.capital_authority != "NONE" or bundle.live_trading != "BLOCKED":
        raise DurableDevelopmentOutcomeGovernanceError(
            "D3C cannot grant capital or LIVE"
        )


def _parse_canonical_document(raw: str, name: str) -> dict[str, object]:
    if not isinstance(raw, str):
        raise TypeError(f"{name} must be canonical JSON text")
    document = _strict_json_loads(raw)
    if not isinstance(document, dict):
        raise DurableDevelopmentOutcomeIntegrityError(
            f"{name} must be an object"
        )
    if _canonical_json(document) != raw:
        raise DurableDevelopmentOutcomeIntegrityError(
            f"{name} is not canonical JSON"
        )
    return document


def _strict_json_loads(raw: str) -> object:
    def pairs_hook(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DurableDevelopmentOutcomeIntegrityError(
                    "D3C JSON contains duplicate object key"
                )
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=pairs_hook)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise DurableDevelopmentOutcomeIntegrityError(
            "D3C artifact contains invalid JSON"
        ) from exc


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
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise DurableDevelopmentOutcomeIntegrityError(
            f"D3C invalid {name}"
        )
