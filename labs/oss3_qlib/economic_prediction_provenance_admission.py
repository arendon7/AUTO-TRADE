"""OSS-3D2P replay-verified provenance admission for D2N.

D2O proves a software provenance primitive. D2P turns that primitive into a
durable, pre-D2K admission condition for the economic campaign.

The canonical order is::

    D2M economic protocol
      -> exact D2O feature artifact
        -> D2O real-Qlib prediction
          -> D2N exact prediction precommit
            -> D2P independent D2O replay + exact equality
              -> durable append-only provenance admission
                -> D2K predictive FINAL_HOLDOUT
                  -> D2N economic one-shot evaluation

D2P deliberately replays D2O instead of trusting a caller-supplied provenance
boolean or receipt. The replay must reproduce the exact already-precommitted
prediction artifact and payload. Admission is committed before any D2K start or
permit consumption. A SQLite trigger then refuses every D2N start that is not
bound to the exact admitted prediction and lineage.

D2P sees the point-in-time economic feature artifact needed for inference, but
it receives no economic labels, forward returns, PnL, fills or D2N gate results.
It grants no broker, OMS, Safety, OrderIntent, PAPER, capital or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from autotrade.research.oss3_development_inference import DevelopmentInferenceRequest
from autotrade.research.oss3_factor_matrix_artifact import FactorMatrixArtifact
from autotrade.research.oss3_supervised_label_artifact import SupervisedLabelArtifact
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact

from .economic_prediction_precommit import (
    OSS3EconomicPredictionPrecommitReceipt,
    SQLiteOSS3EconomicPredictionPrecommitRegistry,
    read_oss3d2n_prediction_precommit_read_only,
)
from .economic_prediction_provenance import (
    EconomicPredictionFeatureArtifact,
    OSS3EconomicPredictionProvenanceReceipt,
    economic_prediction_provenance_semantic_hash,
    run_economic_prediction_provenance,
)
from .final_holdout_protocol import OSS3FinalHoldoutProtocolReceipt
from .predictive_economic_protocol import OSS3PredictiveEconomicProtocolReceipt
from .predictive_strategy_contract import OSS3PredictiveStrategyPreregistrationReceipt


OSS3D2P_ADMISSION_VERSION = "OSS3D2P_ECONOMIC_PREDICTION_PROVENANCE_ADMISSION_V1"
OSS3D2P_ORDERING_CONTRACT = "OSS3D2P_D2O_PRECOMMIT_D2K_D2N_SHARED_SQLITE_ORDERING_V1"
OSS3D2P_REPLAY_POLICY = "INDEPENDENT_D2O_EXACT_PREDICTION_REPLAY_V1"

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")

SEMANTIC_FILES = (
    "labs/oss3_qlib/economic_prediction_provenance_admission.py",
    "labs/oss3_qlib/economic_prediction_provenance.py",
    "labs/oss3_qlib/economic_prediction_precommit.py",
    "labs/oss3_qlib/economic_holdout_evaluator.py",
    "labs/oss3_qlib/predictive_economic_protocol.py",
    "labs/oss3_qlib/predictive_strategy_contract.py",
    "labs/oss3_qlib/final_holdout_protocol.py",
    "labs/oss3_qlib/family_model_contract.py",
    "labs/oss3_qlib/family_environment_attestation.py",
    "labs/oss3_qlib/network_guard.py",
    "labs/oss3_qlib/requirements.txt",
    "src/autotrade/research/oss3_development_inference.py",
    "src/autotrade/research/oss3_factor_matrix_artifact.py",
    "src/autotrade/research/oss3_supervised_label_artifact.py",
    "src/autotrade/research/oss3_training_bundle.py",
    "src/autotrade/research/oss3_qlib_artifact.py",
)


class EconomicPredictionProvenanceAdmissionError(RuntimeError):
    """Base D2P failure."""


class EconomicPredictionProvenanceAdmissionIntegrityError(
    EconomicPredictionProvenanceAdmissionError
):
    """A durable or replayed provenance identity drifted."""


class EconomicPredictionProvenanceAdmissionGovernanceError(
    EconomicPredictionProvenanceAdmissionError
):
    """The admission violates pre-D2K/no-outcome governance."""


class EconomicPredictionProvenanceAdmissionConflict(
    EconomicPredictionProvenanceAdmissionError
):
    """Append-only D2P state conflicts with a prior admission."""


@dataclass(frozen=True, slots=True)
class OSS3EconomicPredictionProvenanceAdmissionReceipt:
    receipt_version: str
    ordering_contract: str
    replay_policy: str
    admission_id: str
    economic_protocol_id: str
    economic_protocol_receipt_hash: str
    source_d2l_receipt_hash: str
    source_d2l_binding_hash: str
    source_strategy_semantic_hash: str
    source_d2j_protocol_id: str
    source_d2j_protocol_receipt_hash: str
    prediction_precommit_id: str
    prediction_precommit_receipt_hash: str
    prediction_artifact_hash: str
    prediction_payload_hash: str
    prediction_support_hash: str
    d2o_provenance_receipt_hash: str
    d2o_provenance_semantic_hash: str
    economic_feature_artifact_hash: str
    economic_feature_row_payload_hash: str
    economic_feature_support_hash: str
    feature_source_hash: str
    feature_producer_code_hash: str
    source_environment_attestation_hash: str
    observed_environment_attestation_hash: str
    source_runtime_environment_hash: str
    observed_runtime_environment_hash: str
    shared_model_runner_code_hash: str
    admission_semantic_hash: str
    registered_at: str
    prediction_precommit_proven: bool
    independent_d2o_replay_performed: bool
    replay_prediction_exact_match: bool
    provenance_receipt_recomputed: bool
    frozen_before_d2k_start: bool
    d2k_start_absent_at_commit: bool
    d2k_permit_unconsumed_at_commit: bool
    d2n_database_trigger_installed: bool
    market_derived_features_loaded: bool
    economic_labels_loaded: bool
    economic_outcomes_loaded: bool
    profitability_claim_authorized: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        if self.receipt_version != OSS3D2P_ADMISSION_VERSION:
            raise EconomicPredictionProvenanceAdmissionIntegrityError(
                "noncanonical D2P admission version"
            )
        if self.ordering_contract != OSS3D2P_ORDERING_CONTRACT:
            raise EconomicPredictionProvenanceAdmissionIntegrityError(
                "D2P ordering contract drifted"
            )
        if self.replay_policy != OSS3D2P_REPLAY_POLICY:
            raise EconomicPredictionProvenanceAdmissionIntegrityError(
                "D2P replay policy drifted"
            )
        for name in (
            "admission_id",
            "economic_protocol_id",
            "source_d2j_protocol_id",
            "prediction_precommit_id",
        ):
            _require_id(getattr(self, name), name)
        for name in (
            "economic_protocol_receipt_hash",
            "source_d2l_receipt_hash",
            "source_d2l_binding_hash",
            "source_strategy_semantic_hash",
            "source_d2j_protocol_receipt_hash",
            "prediction_precommit_receipt_hash",
            "prediction_artifact_hash",
            "prediction_payload_hash",
            "prediction_support_hash",
            "d2o_provenance_receipt_hash",
            "d2o_provenance_semantic_hash",
            "economic_feature_artifact_hash",
            "economic_feature_row_payload_hash",
            "economic_feature_support_hash",
            "feature_source_hash",
            "feature_producer_code_hash",
            "source_environment_attestation_hash",
            "observed_environment_attestation_hash",
            "source_runtime_environment_hash",
            "observed_runtime_environment_hash",
            "shared_model_runner_code_hash",
            "admission_semantic_hash",
            "receipt_hash",
        ):
            _require_hash(getattr(self, name), name)
        _parse_utc(self.registered_at, "registered_at")
        if self.d2o_provenance_semantic_hash != economic_prediction_provenance_semantic_hash():
            raise EconomicPredictionProvenanceAdmissionIntegrityError(
                "D2P references stale D2O provenance semantics"
            )
        if self.admission_semantic_hash != economic_prediction_provenance_admission_semantic_hash():
            raise EconomicPredictionProvenanceAdmissionIntegrityError(
                "D2P admission semantic identity drifted"
            )
        if self.source_environment_attestation_hash != self.observed_environment_attestation_hash:
            raise EconomicPredictionProvenanceAdmissionIntegrityError(
                "D2P source/observed environment attestation mismatch"
            )
        if self.source_runtime_environment_hash != self.observed_runtime_environment_hash:
            raise EconomicPredictionProvenanceAdmissionIntegrityError(
                "D2P source/observed runtime mismatch"
            )
        if self.economic_feature_support_hash != self.prediction_support_hash:
            raise EconomicPredictionProvenanceAdmissionIntegrityError(
                "D2P feature/prediction support hashes differ"
            )
        if not (
            self.prediction_precommit_proven
            and self.independent_d2o_replay_performed
            and self.replay_prediction_exact_match
            and self.provenance_receipt_recomputed
            and self.frozen_before_d2k_start
            and self.d2k_start_absent_at_commit
            and self.d2k_permit_unconsumed_at_commit
            and self.d2n_database_trigger_installed
            and self.market_derived_features_loaded
        ):
            raise EconomicPredictionProvenanceAdmissionGovernanceError(
                "D2P admission lacks mandatory replay/ordering evidence"
            )
        if self.economic_labels_loaded or self.economic_outcomes_loaded:
            raise EconomicPredictionProvenanceAdmissionGovernanceError(
                "D2P admission may not load economic labels/outcomes"
            )
        if (
            self.profitability_claim_authorized
            or self.promotion_authorized
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise EconomicPredictionProvenanceAdmissionGovernanceError(
                "D2P admission cannot grant research/promotion/execution authority"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise EconomicPredictionProvenanceAdmissionGovernanceError(
                "D2P admission cannot grant capital/LIVE"
            )
        if self.receipt_hash != _hash(self.to_dict(include_hash=False)):
            raise EconomicPredictionProvenanceAdmissionIntegrityError(
                "D2P admission receipt hash mismatch"
            )

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "receipt_version": self.receipt_version,
            "ordering_contract": self.ordering_contract,
            "replay_policy": self.replay_policy,
            "admission_id": self.admission_id,
            "economic_protocol_id": self.economic_protocol_id,
            "economic_protocol_receipt_hash": self.economic_protocol_receipt_hash,
            "source_d2l_receipt_hash": self.source_d2l_receipt_hash,
            "source_d2l_binding_hash": self.source_d2l_binding_hash,
            "source_strategy_semantic_hash": self.source_strategy_semantic_hash,
            "source_d2j_protocol_id": self.source_d2j_protocol_id,
            "source_d2j_protocol_receipt_hash": self.source_d2j_protocol_receipt_hash,
            "prediction_precommit_id": self.prediction_precommit_id,
            "prediction_precommit_receipt_hash": self.prediction_precommit_receipt_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "prediction_payload_hash": self.prediction_payload_hash,
            "prediction_support_hash": self.prediction_support_hash,
            "d2o_provenance_receipt_hash": self.d2o_provenance_receipt_hash,
            "d2o_provenance_semantic_hash": self.d2o_provenance_semantic_hash,
            "economic_feature_artifact_hash": self.economic_feature_artifact_hash,
            "economic_feature_row_payload_hash": self.economic_feature_row_payload_hash,
            "economic_feature_support_hash": self.economic_feature_support_hash,
            "feature_source_hash": self.feature_source_hash,
            "feature_producer_code_hash": self.feature_producer_code_hash,
            "source_environment_attestation_hash": self.source_environment_attestation_hash,
            "observed_environment_attestation_hash": self.observed_environment_attestation_hash,
            "source_runtime_environment_hash": self.source_runtime_environment_hash,
            "observed_runtime_environment_hash": self.observed_runtime_environment_hash,
            "shared_model_runner_code_hash": self.shared_model_runner_code_hash,
            "admission_semantic_hash": self.admission_semantic_hash,
            "registered_at": self.registered_at,
            "prediction_precommit_proven": self.prediction_precommit_proven,
            "independent_d2o_replay_performed": self.independent_d2o_replay_performed,
            "replay_prediction_exact_match": self.replay_prediction_exact_match,
            "provenance_receipt_recomputed": self.provenance_receipt_recomputed,
            "frozen_before_d2k_start": self.frozen_before_d2k_start,
            "d2k_start_absent_at_commit": self.d2k_start_absent_at_commit,
            "d2k_permit_unconsumed_at_commit": self.d2k_permit_unconsumed_at_commit,
            "d2n_database_trigger_installed": self.d2n_database_trigger_installed,
            "market_derived_features_loaded": self.market_derived_features_loaded,
            "economic_labels_loaded": self.economic_labels_loaded,
            "economic_outcomes_loaded": self.economic_outcomes_loaded,
            "profitability_claim_authorized": self.profitability_claim_authorized,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }
        if include_hash:
            payload["receipt_hash"] = self.receipt_hash
        return payload


class SQLiteOSS3EconomicPredictionProvenanceAdmissionRegistry:
    """Append-only D2P admission plus a DB-level mandatory D2N start gate."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # D2N's exact prediction precommit registry creates the canonical D2N
        # start table and its own exact-prediction trigger first.
        SQLiteOSS3EconomicPredictionPrecommitRegistry(self.path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oss3_economic_prediction_provenance_admissions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    admission_id TEXT NOT NULL UNIQUE,
                    economic_protocol_id TEXT NOT NULL UNIQUE,
                    economic_protocol_receipt_hash TEXT NOT NULL UNIQUE,
                    source_d2l_receipt_hash TEXT NOT NULL,
                    source_d2l_binding_hash TEXT NOT NULL,
                    source_strategy_semantic_hash TEXT NOT NULL UNIQUE,
                    source_d2j_protocol_id TEXT NOT NULL UNIQUE,
                    source_d2j_protocol_receipt_hash TEXT NOT NULL,
                    prediction_precommit_id TEXT NOT NULL UNIQUE,
                    prediction_precommit_receipt_hash TEXT NOT NULL UNIQUE,
                    prediction_artifact_hash TEXT NOT NULL UNIQUE,
                    prediction_payload_hash TEXT NOT NULL UNIQUE,
                    prediction_support_hash TEXT NOT NULL,
                    d2o_provenance_receipt_hash TEXT NOT NULL UNIQUE,
                    d2o_provenance_semantic_hash TEXT NOT NULL,
                    economic_feature_artifact_hash TEXT NOT NULL UNIQUE,
                    admission_semantic_hash TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS oss3_economic_prediction_provenance_admissions_no_update
                BEFORE UPDATE ON oss3_economic_prediction_provenance_admissions
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2P provenance admission registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_economic_prediction_provenance_admissions_no_delete
                BEFORE DELETE ON oss3_economic_prediction_provenance_admissions
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2P provenance admission registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_d2p_d2n_start_requires_provenance_admission
                BEFORE INSERT ON oss3_economic_holdout_evaluation_starts
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM oss3_economic_prediction_provenance_admissions a
                    WHERE a.economic_protocol_id = NEW.economic_protocol_id
                      AND a.economic_protocol_receipt_hash = NEW.economic_protocol_receipt_hash
                      AND a.source_d2l_receipt_hash = NEW.source_d2l_receipt_hash
                      AND a.source_strategy_semantic_hash = NEW.source_strategy_semantic_hash
                      AND a.source_d2j_protocol_id = NEW.source_d2j_protocol_id
                      AND a.source_d2j_protocol_receipt_hash = NEW.source_d2j_protocol_receipt_hash
                      AND a.prediction_artifact_hash = NEW.economic_prediction_artifact_hash
                )
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2P replay-verified prediction provenance admission required');
                END;
                """
            )
            conn.commit()
        finally:
            conn.close()

    def admit_by_replay(
        self,
        *,
        admission_id: str,
        economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
        d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
        d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
        source_request: DevelopmentInferenceRequest,
        training_bundle: TrainingBundleArtifact,
        train_features: FactorMatrixArtifact,
        train_labels: SupervisedLabelArtifact,
        economic_features: EconomicPredictionFeatureArtifact,
        now: datetime,
    ) -> OSS3EconomicPredictionProvenanceAdmissionReceipt:
        """Independently replay D2O and admit only an exact precommitted prediction."""
        _require_id(admission_id, "admission_id")
        _require_aware(now, "now")
        for value, expected_type, name in (
            (economic_protocol, OSS3PredictiveEconomicProtocolReceipt, "economic_protocol"),
            (d2l_receipt, OSS3PredictiveStrategyPreregistrationReceipt, "d2l_receipt"),
            (d2j_protocol, OSS3FinalHoldoutProtocolReceipt, "d2j_protocol"),
            (source_request, DevelopmentInferenceRequest, "source_request"),
            (training_bundle, TrainingBundleArtifact, "training_bundle"),
            (train_features, FactorMatrixArtifact, "train_features"),
            (train_labels, SupervisedLabelArtifact, "train_labels"),
            (economic_features, EconomicPredictionFeatureArtifact, "economic_features"),
        ):
            if not isinstance(value, expected_type):
                raise TypeError(f"{name} must be {expected_type.__name__}")

        precommit = read_oss3d2n_prediction_precommit_read_only(
            self.path,
            economic_protocol_id=economic_protocol.economic_protocol_id,
        )
        if precommit is None:
            raise EconomicPredictionProvenanceAdmissionGovernanceError(
                "D2P requires durable D2N prediction precommit before provenance admission"
            )
        _verify_precommit_lineage(
            precommit=precommit,
            economic_protocol=economic_protocol,
            d2l_receipt=d2l_receipt,
            d2j_protocol=d2j_protocol,
        )

        replay_prediction, replay_attestation, d2o_receipt = run_economic_prediction_provenance(
            economic_protocol=economic_protocol,
            d2l_receipt=d2l_receipt,
            d2j_protocol=d2j_protocol,
            source_request=source_request,
            training_bundle=training_bundle,
            train_features=train_features,
            train_labels=train_labels,
            economic_features=economic_features,
        )
        _verify_replay_matches_precommit(
            precommit=precommit,
            prediction_artifact_hash=replay_prediction.artifact_hash,
            prediction_payload_hash=replay_prediction.manifest.prediction_payload_hash,
            prediction_support_hash=economic_features.support_hash,
        )
        candidate = _build_admission_receipt(
            admission_id=admission_id,
            economic_protocol=economic_protocol,
            d2l_receipt=d2l_receipt,
            d2j_protocol=d2j_protocol,
            precommit=precommit,
            economic_features=economic_features,
            d2o_receipt=d2o_receipt,
            observed_attestation_hash=replay_attestation.artifact_hash,
            observed_runtime_hash=replay_attestation.runtime_environment.fingerprint,
            registered_at=now,
        )

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _require_exact_durable_precommit(conn=conn, precommit=precommit)
            _require_exact_durable_d2m_d2l(
                conn=conn,
                economic_protocol=economic_protocol,
                d2l_receipt=d2l_receipt,
            )
            _require_no_d2k_state(conn=conn, d2j_protocol=d2j_protocol)
            existing = conn.execute(
                "SELECT receipt_json FROM oss3_economic_prediction_provenance_admissions "
                "WHERE economic_protocol_id = ?",
                (economic_protocol.economic_protocol_id,),
            ).fetchone()
            if existing is not None:
                current = _receipt_from_row(existing)
                if current != candidate:
                    raise EconomicPredictionProvenanceAdmissionConflict(
                        "economic protocol already has a different provenance admission"
                    )
                conn.execute("COMMIT")
                return current
            conn.execute(
                """
                INSERT INTO oss3_economic_prediction_provenance_admissions(
                    admission_id, economic_protocol_id, economic_protocol_receipt_hash,
                    source_d2l_receipt_hash, source_d2l_binding_hash,
                    source_strategy_semantic_hash, source_d2j_protocol_id,
                    source_d2j_protocol_receipt_hash, prediction_precommit_id,
                    prediction_precommit_receipt_hash, prediction_artifact_hash,
                    prediction_payload_hash, prediction_support_hash,
                    d2o_provenance_receipt_hash, d2o_provenance_semantic_hash,
                    economic_feature_artifact_hash, admission_semantic_hash,
                    registered_at, receipt_hash, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.admission_id,
                    candidate.economic_protocol_id,
                    candidate.economic_protocol_receipt_hash,
                    candidate.source_d2l_receipt_hash,
                    candidate.source_d2l_binding_hash,
                    candidate.source_strategy_semantic_hash,
                    candidate.source_d2j_protocol_id,
                    candidate.source_d2j_protocol_receipt_hash,
                    candidate.prediction_precommit_id,
                    candidate.prediction_precommit_receipt_hash,
                    candidate.prediction_artifact_hash,
                    candidate.prediction_payload_hash,
                    candidate.prediction_support_hash,
                    candidate.d2o_provenance_receipt_hash,
                    candidate.d2o_provenance_semantic_hash,
                    candidate.economic_feature_artifact_hash,
                    candidate.admission_semantic_hash,
                    candidate.registered_at,
                    candidate.receipt_hash,
                    _canonical_json(candidate.to_dict()),
                ),
            )
            conn.execute("COMMIT")
            return candidate
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise EconomicPredictionProvenanceAdmissionConflict(
                "D2P durable provenance admission identity conflict"
            ) from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def read_oss3d2p_provenance_admission_read_only(
    path: str | Path,
    *,
    economic_protocol_id: str,
) -> OSS3EconomicPredictionProvenanceAdmissionReceipt | None:
    _require_id(economic_protocol_id, "economic_protocol_id")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise EconomicPredictionProvenanceAdmissionIntegrityError(
            "D2P provenance admission registry does not exist"
        )
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        if not _table_exists(conn, "oss3_economic_prediction_provenance_admissions"):
            return None
        row = conn.execute(
            "SELECT receipt_json FROM oss3_economic_prediction_provenance_admissions "
            "WHERE economic_protocol_id = ?",
            (economic_protocol_id,),
        ).fetchone()
        return _receipt_from_row(row) if row is not None else None
    finally:
        conn.close()


def economic_prediction_provenance_admission_semantic_hash() -> str:
    root = Path(__file__).resolve().parents[2]
    payload: list[dict[str, str]] = []
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise EconomicPredictionProvenanceAdmissionIntegrityError(
                f"D2P semantic file is missing: {relative}"
            )
        payload.append({"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()})
    return _hash(payload)


def _verify_precommit_lineage(
    *,
    precommit: OSS3EconomicPredictionPrecommitReceipt,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
) -> None:
    binding = d2l_receipt.binding
    expected = {
        "economic_protocol_id": economic_protocol.economic_protocol_id,
        "economic_protocol_receipt_hash": economic_protocol.receipt_hash,
        "source_d2l_receipt_hash": d2l_receipt.receipt_hash,
        "source_d2l_binding_hash": binding.binding_hash,
        "source_strategy_semantic_hash": binding.strategy_semantic_hash,
        "source_d2j_protocol_id": d2j_protocol.protocol_id,
        "source_d2j_protocol_receipt_hash": d2j_protocol.receipt_hash,
        "model_family": binding.model_family,
        "model_config_hash": binding.model_config_hash,
        "qlib_version": binding.qlib_version,
        "training_dataset_hash": binding.training_dataset_hash,
        "feature_schema_hash": binding.feature_schema_hash,
        "producer_code_hash": binding.shared_runner_code_hash,
    }
    for name, value in expected.items():
        if getattr(precommit, name) != value:
            raise EconomicPredictionProvenanceAdmissionIntegrityError(
                f"D2P precommit {name} mismatch"
            )
    if precommit.frozen_before_d2k_start is not True or precommit.prediction_values_frozen is not True:
        raise EconomicPredictionProvenanceAdmissionGovernanceError(
            "D2P requires a prediction precommit frozen before D2K"
        )


def _verify_replay_matches_precommit(
    *,
    precommit: OSS3EconomicPredictionPrecommitReceipt,
    prediction_artifact_hash: str,
    prediction_payload_hash: str,
    prediction_support_hash: str,
) -> None:
    if prediction_artifact_hash != precommit.prediction_artifact_hash:
        raise EconomicPredictionProvenanceAdmissionIntegrityError(
            "D2O replay prediction artifact differs from durable precommit"
        )
    if prediction_payload_hash != precommit.prediction_payload_hash:
        raise EconomicPredictionProvenanceAdmissionIntegrityError(
            "D2O replay prediction payload differs from durable precommit"
        )
    if prediction_support_hash != precommit.prediction_support_hash:
        raise EconomicPredictionProvenanceAdmissionIntegrityError(
            "D2O replay prediction support differs from durable precommit"
        )


def _build_admission_receipt(
    *,
    admission_id: str,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
    precommit: OSS3EconomicPredictionPrecommitReceipt,
    economic_features: EconomicPredictionFeatureArtifact,
    d2o_receipt: OSS3EconomicPredictionProvenanceReceipt,
    observed_attestation_hash: str,
    observed_runtime_hash: str,
    registered_at: datetime,
) -> OSS3EconomicPredictionProvenanceAdmissionReceipt:
    binding = d2l_receipt.binding
    values: dict[str, object] = {
        "receipt_version": OSS3D2P_ADMISSION_VERSION,
        "ordering_contract": OSS3D2P_ORDERING_CONTRACT,
        "replay_policy": OSS3D2P_REPLAY_POLICY,
        "admission_id": admission_id,
        "economic_protocol_id": economic_protocol.economic_protocol_id,
        "economic_protocol_receipt_hash": economic_protocol.receipt_hash,
        "source_d2l_receipt_hash": d2l_receipt.receipt_hash,
        "source_d2l_binding_hash": binding.binding_hash,
        "source_strategy_semantic_hash": binding.strategy_semantic_hash,
        "source_d2j_protocol_id": d2j_protocol.protocol_id,
        "source_d2j_protocol_receipt_hash": d2j_protocol.receipt_hash,
        "prediction_precommit_id": precommit.precommit_id,
        "prediction_precommit_receipt_hash": precommit.receipt_hash,
        "prediction_artifact_hash": precommit.prediction_artifact_hash,
        "prediction_payload_hash": precommit.prediction_payload_hash,
        "prediction_support_hash": precommit.prediction_support_hash,
        "d2o_provenance_receipt_hash": d2o_receipt.receipt_hash,
        "d2o_provenance_semantic_hash": d2o_receipt.provenance_runner_semantic_hash,
        "economic_feature_artifact_hash": economic_features.artifact_hash,
        "economic_feature_row_payload_hash": economic_features.row_payload_hash,
        "economic_feature_support_hash": economic_features.support_hash,
        "feature_source_hash": economic_features.feature_source_hash,
        "feature_producer_code_hash": economic_features.feature_producer_code_hash,
        "source_environment_attestation_hash": binding.environment_attestation_hash,
        "observed_environment_attestation_hash": observed_attestation_hash,
        "source_runtime_environment_hash": binding.runtime_environment_hash,
        "observed_runtime_environment_hash": observed_runtime_hash,
        "shared_model_runner_code_hash": binding.shared_runner_code_hash,
        "admission_semantic_hash": economic_prediction_provenance_admission_semantic_hash(),
        "registered_at": _utc_iso(registered_at),
        "prediction_precommit_proven": True,
        "independent_d2o_replay_performed": True,
        "replay_prediction_exact_match": True,
        "provenance_receipt_recomputed": True,
        "frozen_before_d2k_start": True,
        "d2k_start_absent_at_commit": True,
        "d2k_permit_unconsumed_at_commit": True,
        "d2n_database_trigger_installed": True,
        "market_derived_features_loaded": True,
        "economic_labels_loaded": False,
        "economic_outcomes_loaded": False,
        "profitability_claim_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS3EconomicPredictionProvenanceAdmissionReceipt(
        **values,
        receipt_hash=_hash(values),
    )


def _require_exact_durable_precommit(
    *,
    conn: sqlite3.Connection,
    precommit: OSS3EconomicPredictionPrecommitReceipt,
) -> None:
    if not _table_exists(conn, "oss3_economic_prediction_precommits"):
        raise EconomicPredictionProvenanceAdmissionGovernanceError(
            "D2P requires durable prediction precommit table"
        )
    row = conn.execute(
        "SELECT receipt_hash, prediction_artifact_hash, prediction_payload_hash, "
        "prediction_support_hash FROM oss3_economic_prediction_precommits "
        "WHERE economic_protocol_id = ?",
        (precommit.economic_protocol_id,),
    ).fetchone()
    if row is None:
        raise EconomicPredictionProvenanceAdmissionGovernanceError(
            "D2P durable prediction precommit is missing"
        )
    actual = (
        str(row["receipt_hash"]),
        str(row["prediction_artifact_hash"]),
        str(row["prediction_payload_hash"]),
        str(row["prediction_support_hash"]),
    )
    expected = (
        precommit.receipt_hash,
        precommit.prediction_artifact_hash,
        precommit.prediction_payload_hash,
        precommit.prediction_support_hash,
    )
    if actual != expected:
        raise EconomicPredictionProvenanceAdmissionIntegrityError(
            "D2P durable prediction precommit drifted"
        )


def _require_exact_durable_d2m_d2l(
    *,
    conn: sqlite3.Connection,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
) -> None:
    if not _table_exists(conn, "oss3_predictive_economic_protocols"):
        raise EconomicPredictionProvenanceAdmissionGovernanceError("D2M durable table missing")
    d2m = conn.execute(
        "SELECT receipt_hash FROM oss3_predictive_economic_protocols WHERE economic_protocol_id = ?",
        (economic_protocol.economic_protocol_id,),
    ).fetchone()
    if d2m is None or str(d2m["receipt_hash"]) != economic_protocol.receipt_hash:
        raise EconomicPredictionProvenanceAdmissionIntegrityError("exact durable D2M receipt not proven")
    if not _table_exists(conn, "oss3_predictive_strategy_preregistrations"):
        raise EconomicPredictionProvenanceAdmissionGovernanceError("D2L durable table missing")
    d2l = conn.execute(
        "SELECT receipt_hash FROM oss3_predictive_strategy_preregistrations WHERE protocol_id = ?",
        (d2l_receipt.protocol_id,),
    ).fetchone()
    if d2l is None or str(d2l["receipt_hash"]) != d2l_receipt.receipt_hash:
        raise EconomicPredictionProvenanceAdmissionIntegrityError("exact durable D2L receipt not proven")


def _require_no_d2k_state(
    *,
    conn: sqlite3.Connection,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
) -> None:
    if _table_exists(conn, "oss3_final_holdout_evaluation_starts"):
        row = conn.execute(
            "SELECT start_hash FROM oss3_final_holdout_evaluation_starts WHERE protocol_id = ?",
            (d2j_protocol.protocol_id,),
        ).fetchone()
        if row is not None:
            raise EconomicPredictionProvenanceAdmissionGovernanceError(
                "D2P provenance admission must commit before D2K start"
            )
    if _table_exists(conn, "holdout_permits"):
        row = conn.execute(
            "SELECT used_at FROM holdout_permits WHERE permit_id = ?",
            (d2j_protocol.expected_holdout_authorization_id,),
        ).fetchone()
        if row is not None and row["used_at"] is not None:
            raise EconomicPredictionProvenanceAdmissionGovernanceError(
                "D2P provenance admission cannot follow D2K permit consumption"
            )


def _receipt_from_row(row: sqlite3.Row) -> OSS3EconomicPredictionProvenanceAdmissionReceipt:
    try:
        payload = json.loads(str(row["receipt_json"]))
        return OSS3EconomicPredictionProvenanceAdmissionReceipt(
            receipt_version=str(payload["receipt_version"]),
            ordering_contract=str(payload["ordering_contract"]),
            replay_policy=str(payload["replay_policy"]),
            admission_id=str(payload["admission_id"]),
            economic_protocol_id=str(payload["economic_protocol_id"]),
            economic_protocol_receipt_hash=str(payload["economic_protocol_receipt_hash"]),
            source_d2l_receipt_hash=str(payload["source_d2l_receipt_hash"]),
            source_d2l_binding_hash=str(payload["source_d2l_binding_hash"]),
            source_strategy_semantic_hash=str(payload["source_strategy_semantic_hash"]),
            source_d2j_protocol_id=str(payload["source_d2j_protocol_id"]),
            source_d2j_protocol_receipt_hash=str(payload["source_d2j_protocol_receipt_hash"]),
            prediction_precommit_id=str(payload["prediction_precommit_id"]),
            prediction_precommit_receipt_hash=str(payload["prediction_precommit_receipt_hash"]),
            prediction_artifact_hash=str(payload["prediction_artifact_hash"]),
            prediction_payload_hash=str(payload["prediction_payload_hash"]),
            prediction_support_hash=str(payload["prediction_support_hash"]),
            d2o_provenance_receipt_hash=str(payload["d2o_provenance_receipt_hash"]),
            d2o_provenance_semantic_hash=str(payload["d2o_provenance_semantic_hash"]),
            economic_feature_artifact_hash=str(payload["economic_feature_artifact_hash"]),
            economic_feature_row_payload_hash=str(payload["economic_feature_row_payload_hash"]),
            economic_feature_support_hash=str(payload["economic_feature_support_hash"]),
            feature_source_hash=str(payload["feature_source_hash"]),
            feature_producer_code_hash=str(payload["feature_producer_code_hash"]),
            source_environment_attestation_hash=str(payload["source_environment_attestation_hash"]),
            observed_environment_attestation_hash=str(payload["observed_environment_attestation_hash"]),
            source_runtime_environment_hash=str(payload["source_runtime_environment_hash"]),
            observed_runtime_environment_hash=str(payload["observed_runtime_environment_hash"]),
            shared_model_runner_code_hash=str(payload["shared_model_runner_code_hash"]),
            admission_semantic_hash=str(payload["admission_semantic_hash"]),
            registered_at=str(payload["registered_at"]),
            prediction_precommit_proven=bool(payload["prediction_precommit_proven"]),
            independent_d2o_replay_performed=bool(payload["independent_d2o_replay_performed"]),
            replay_prediction_exact_match=bool(payload["replay_prediction_exact_match"]),
            provenance_receipt_recomputed=bool(payload["provenance_receipt_recomputed"]),
            frozen_before_d2k_start=bool(payload["frozen_before_d2k_start"]),
            d2k_start_absent_at_commit=bool(payload["d2k_start_absent_at_commit"]),
            d2k_permit_unconsumed_at_commit=bool(payload["d2k_permit_unconsumed_at_commit"]),
            d2n_database_trigger_installed=bool(payload["d2n_database_trigger_installed"]),
            market_derived_features_loaded=bool(payload["market_derived_features_loaded"]),
            economic_labels_loaded=bool(payload["economic_labels_loaded"]),
            economic_outcomes_loaded=bool(payload["economic_outcomes_loaded"]),
            profitability_claim_authorized=bool(payload["profitability_claim_authorized"]),
            promotion_authorized=bool(payload["promotion_authorized"]),
            execution_authorized=bool(payload["execution_authorized"]),
            paper_execution_authorized=bool(payload["paper_execution_authorized"]),
            capital_authority=str(payload["capital_authority"]),
            live_trading=str(payload["live_trading"]),
            receipt_hash=str(payload["receipt_hash"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EconomicPredictionProvenanceAdmissionIntegrityError(
            "invalid durable D2P provenance admission receipt"
        ) from exc


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _require_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _parse_utc(value: str, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc
    _require_aware(parsed, name)
    canonical = parsed.astimezone(timezone.utc)
    if canonical.isoformat() != value:
        raise ValueError(f"{name} must be canonical UTC ISO-8601")
    return canonical


def _utc_iso(value: datetime) -> str:
    _require_aware(value, "timestamp")
    return value.astimezone(timezone.utc).isoformat()


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
