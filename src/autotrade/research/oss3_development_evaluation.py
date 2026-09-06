"""OSS-3D2D deterministic DEVELOPMENT prediction evaluation.

This module is deliberately downstream of inference. It accepts only an
already-bound OSS-3D2A prediction receipt, the exact OSS-3A prediction artifact
referenced by that receipt, and an OSS-3C DEVELOPMENT label artifact.

Scientific boundary:
- DEVELOPMENT labels first enter the OSS-3 inference chain here, after the
  prediction artifact is immutable and hash-bound;
- FINAL_HOLDOUT is structurally unavailable;
- prediction and label keysets must match exactly by (timestamp, symbol);
- campaign, research split, universe, label definition and time window are
  rebound before metrics are computed;
- metrics measure predictive quality, not executable PnL or profitability;
- no Qlib runtime, network, subprocess, broker, OMS, Safety, OrderIntent,
  PAPER, capital or LIVE authority exists here.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite, sqrt
import re
from statistics import median
from typing import Iterable, Mapping, Sequence

from .oss3_development_inference import DevelopmentPredictionReceipt
from .oss3_qlib_artifact import QlibPredictionArtifact
from .oss3_supervised_label_artifact import SupervisedLabelArtifact


OSS3D2D_EVALUATION_VERSION = "OSS3D2D_DEVELOPMENT_EVALUATION_V1"
OSS3D2D_PRODUCER_ID = "AUTO-TRADE/OSS3D2D_DEVELOPMENT_EVALUATION"
METRIC_POLICY_ID = "PREDICTIVE_QUALITY_NO_PNL_V1"
KEY_POLICY_ID = "EXACT_TIMESTAMP_SYMBOL_LABEL_KEYSET_V1"
MIN_OBSERVATIONS = 3
MIN_CROSS_SECTIONAL_OBSERVATIONS = 3
MAX_OBSERVATIONS = 2_000_000

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")


class DevelopmentEvaluationError(RuntimeError):
    """Base OSS-3D2D failure."""


class DevelopmentEvaluationIntegrityError(DevelopmentEvaluationError):
    """Input or derived evidence does not match its immutable identity."""


class DevelopmentEvaluationGovernanceError(DevelopmentEvaluationError):
    """Evaluation violates DEVELOPMENT-only research governance."""


class DevelopmentEvaluationCompatibilityError(DevelopmentEvaluationError):
    """Prediction and label artifacts cannot be safely evaluated together."""


@dataclass(frozen=True, slots=True)
class CrossSectionalIC:
    timestamp: str
    observation_count: int
    pearson_ic: float
    spearman_ic: float

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, str) or not self.timestamp:
            raise ValueError("timestamp must be non-empty")
        if (
            not isinstance(self.observation_count, int)
            or isinstance(self.observation_count, bool)
            or self.observation_count < MIN_CROSS_SECTIONAL_OBSERVATIONS
        ):
            raise ValueError("cross-sectional observation_count is too small")
        _require_finite(self.pearson_ic, "pearson_ic")
        _require_finite(self.spearman_ic, "spearman_ic")
        if not -1.0 <= self.pearson_ic <= 1.0:
            raise ValueError("pearson_ic outside [-1,1]")
        if not -1.0 <= self.spearman_ic <= 1.0:
            raise ValueError("spearman_ic outside [-1,1]")

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "observation_count": self.observation_count,
            "pearson_ic": self.pearson_ic,
            "spearman_ic": self.spearman_ic,
        }


@dataclass(frozen=True, slots=True)
class DevelopmentPredictionMetrics:
    observation_count: int
    pearson_ic: float
    spearman_ic: float
    mae: float
    rmse: float
    sign_accuracy: float
    cross_section_count: int
    mean_cross_sectional_ic: float
    median_cross_sectional_ic: float
    positive_cross_sectional_ic_ratio: float
    mean_cross_sectional_rank_ic: float
    median_cross_sectional_rank_ic: float
    positive_cross_sectional_rank_ic_ratio: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.observation_count, int)
            or isinstance(self.observation_count, bool)
            or not MIN_OBSERVATIONS <= self.observation_count <= MAX_OBSERVATIONS
        ):
            raise ValueError("observation_count outside OSS-3D2D bound")
        if (
            not isinstance(self.cross_section_count, int)
            or isinstance(self.cross_section_count, bool)
            or self.cross_section_count < 1
        ):
            raise ValueError("cross_section_count must be positive")
        for name, value in self.to_dict().items():
            if name in {"observation_count", "cross_section_count"}:
                continue
            _require_finite(value, name)
        for name in (
            "pearson_ic",
            "spearman_ic",
            "mean_cross_sectional_ic",
            "median_cross_sectional_ic",
            "mean_cross_sectional_rank_ic",
            "median_cross_sectional_rank_ic",
        ):
            value = float(getattr(self, name))
            if not -1.0 <= value <= 1.0:
                raise ValueError(f"{name} outside [-1,1]")
        if self.mae < 0.0 or self.rmse < 0.0:
            raise ValueError("error metrics cannot be negative")
        for name in (
            "sign_accuracy",
            "positive_cross_sectional_ic_ratio",
            "positive_cross_sectional_rank_ic_ratio",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} outside [0,1]")

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_count": self.observation_count,
            "pearson_ic": self.pearson_ic,
            "spearman_ic": self.spearman_ic,
            "mae": self.mae,
            "rmse": self.rmse,
            "sign_accuracy": self.sign_accuracy,
            "cross_section_count": self.cross_section_count,
            "mean_cross_sectional_ic": self.mean_cross_sectional_ic,
            "median_cross_sectional_ic": self.median_cross_sectional_ic,
            "positive_cross_sectional_ic_ratio": self.positive_cross_sectional_ic_ratio,
            "mean_cross_sectional_rank_ic": self.mean_cross_sectional_rank_ic,
            "median_cross_sectional_rank_ic": self.median_cross_sectional_rank_ic,
            "positive_cross_sectional_rank_ic_ratio": self.positive_cross_sectional_rank_ic_ratio,
        }


@dataclass(frozen=True, slots=True)
class DevelopmentEvaluationManifest:
    producer_id: str
    metric_policy_id: str
    key_policy_id: str
    campaign_id: str
    research_split_hash: str
    source_universe_hash: str
    label_definition_hash: str
    prediction_receipt_hash: str
    prediction_artifact_hash: str
    prediction_manifest_hash: str
    prediction_payload_hash: str
    development_label_artifact_hash: str
    development_label_manifest_hash: str
    development_label_payload_hash: str
    evaluation_keyset_hash: str
    environment_attestation_hash: str
    model_family: str
    model_config_hash: str
    qlib_version: str
    producer_code_hash: str
    evaluation_start: str
    evaluation_end: str
    observation_count: int
    cross_section_count: int
    research_only: bool = True
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.producer_id != OSS3D2D_PRODUCER_ID:
            raise DevelopmentEvaluationGovernanceError("noncanonical OSS-3D2D producer")
        if self.metric_policy_id != METRIC_POLICY_ID or self.key_policy_id != KEY_POLICY_ID:
            raise DevelopmentEvaluationGovernanceError("noncanonical evaluation policy")
        if not _ID_RE.fullmatch(self.campaign_id):
            raise ValueError("invalid campaign_id")
        for name in (
            "research_split_hash",
            "source_universe_hash",
            "label_definition_hash",
            "prediction_receipt_hash",
            "prediction_artifact_hash",
            "prediction_manifest_hash",
            "prediction_payload_hash",
            "development_label_artifact_hash",
            "development_label_manifest_hash",
            "development_label_payload_hash",
            "evaluation_keyset_hash",
            "environment_attestation_hash",
            "model_config_hash",
            "producer_code_hash",
        ):
            _require_hash(str(getattr(self, name)), name)
        if not _ID_RE.fullmatch(self.model_family):
            raise ValueError("invalid model_family")
        if not self.qlib_version:
            raise ValueError("qlib_version must be non-empty")
        if not self.evaluation_start or not self.evaluation_end:
            raise ValueError("evaluation window must be non-empty")
        if self.observation_count < MIN_OBSERVATIONS or self.cross_section_count < 1:
            raise ValueError("evaluation counts are below policy minimums")
        if not self.research_only:
            raise DevelopmentEvaluationGovernanceError("evaluation must remain research-only")
        if self.execution_authorized or self.paper_execution_authorized:
            raise DevelopmentEvaluationGovernanceError("evaluation cannot authorize execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise DevelopmentEvaluationGovernanceError("evaluation cannot grant capital or LIVE")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "producer_id": self.producer_id,
            "metric_policy_id": self.metric_policy_id,
            "key_policy_id": self.key_policy_id,
            "campaign_id": self.campaign_id,
            "research_split_hash": self.research_split_hash,
            "source_universe_hash": self.source_universe_hash,
            "label_definition_hash": self.label_definition_hash,
            "prediction_receipt_hash": self.prediction_receipt_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "prediction_manifest_hash": self.prediction_manifest_hash,
            "prediction_payload_hash": self.prediction_payload_hash,
            "development_label_artifact_hash": self.development_label_artifact_hash,
            "development_label_manifest_hash": self.development_label_manifest_hash,
            "development_label_payload_hash": self.development_label_payload_hash,
            "evaluation_keyset_hash": self.evaluation_keyset_hash,
            "environment_attestation_hash": self.environment_attestation_hash,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "qlib_version": self.qlib_version,
            "producer_code_hash": self.producer_code_hash,
            "evaluation_start": self.evaluation_start,
            "evaluation_end": self.evaluation_end,
            "observation_count": self.observation_count,
            "cross_section_count": self.cross_section_count,
            "research_only": self.research_only,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class DevelopmentEvaluationArtifact:
    evaluation_version: str
    manifest: DevelopmentEvaluationManifest
    metrics: DevelopmentPredictionMetrics
    cross_sections: tuple[CrossSectionalIC, ...]
    artifact_hash: str

    def __post_init__(self) -> None:
        if self.evaluation_version != OSS3D2D_EVALUATION_VERSION:
            raise DevelopmentEvaluationIntegrityError("unsupported OSS-3D2D version")
        _require_hash(self.artifact_hash, "artifact_hash")
        if self.metrics.observation_count != self.manifest.observation_count:
            raise DevelopmentEvaluationIntegrityError("observation_count mismatch")
        if len(self.cross_sections) != self.manifest.cross_section_count:
            raise DevelopmentEvaluationIntegrityError("cross_section_count mismatch")
        if self.metrics.cross_section_count != len(self.cross_sections):
            raise DevelopmentEvaluationIntegrityError("metric cross_section_count mismatch")
        if self.cross_sections != tuple(sorted(self.cross_sections, key=lambda item: item.timestamp)):
            raise DevelopmentEvaluationGovernanceError("cross sections must be sorted")
        expected = _hash(
            {
                "evaluation_version": self.evaluation_version,
                "manifest": self.manifest.to_dict(),
                "metrics": self.metrics.to_dict(),
                "cross_sections": [item.to_dict() for item in self.cross_sections],
            }
        )
        if expected != self.artifact_hash:
            raise DevelopmentEvaluationIntegrityError("evaluation artifact hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return {
            "evaluation_version": self.evaluation_version,
            "manifest": self.manifest.to_dict(),
            "metrics": self.metrics.to_dict(),
            "cross_sections": [item.to_dict() for item in self.cross_sections],
            "artifact_hash": self.artifact_hash,
        }


def evaluate_development_predictions(
    *,
    receipt: DevelopmentPredictionReceipt,
    prediction: QlibPredictionArtifact,
    labels: SupervisedLabelArtifact,
    environment_attestation_hash: str,
) -> DevelopmentEvaluationArtifact:
    """Evaluate immutable DEVELOPMENT predictions against exact DEVELOPMENT labels."""
    if not isinstance(receipt, DevelopmentPredictionReceipt):
        raise TypeError("receipt must be DevelopmentPredictionReceipt")
    if not isinstance(prediction, QlibPredictionArtifact):
        raise TypeError("prediction must be QlibPredictionArtifact")
    if not isinstance(labels, SupervisedLabelArtifact):
        raise TypeError("labels must be SupervisedLabelArtifact")
    _require_hash(environment_attestation_hash, "environment_attestation_hash")

    pm = prediction.manifest
    lm = labels.manifest

    if lm.partition != "DEVELOPMENT":
        raise DevelopmentEvaluationGovernanceError("OSS-3D2D accepts DEVELOPMENT labels only")

    for name, expected, actual in (
        ("prediction_artifact_hash", receipt.prediction_artifact_hash, prediction.artifact_hash),
        ("prediction_manifest_hash", receipt.prediction_manifest_hash, pm.fingerprint),
        ("prediction_count", receipt.prediction_count, pm.prediction_count),
        ("campaign_id", receipt.campaign_id, lm.campaign_id),
        ("research_split_hash", receipt.research_split_hash, lm.research_split_hash),
        ("source_universe_hash", receipt.source_universe_hash, lm.source_universe_hash),
        ("label_definition_hash", receipt.label_definition_hash, lm.label_definition_hash),
        ("model_family", receipt.model_family, pm.model_family),
        ("model_config_hash", receipt.model_config_hash, pm.model_config_hash),
        ("qlib_version", receipt.qlib_version, pm.qlib_version),
        ("producer_code_hash", receipt.producer_code_hash, pm.producer_code_hash),
        ("train_start", receipt.train_start, pm.train_start),
        ("train_end", receipt.train_end, pm.train_end),
        ("inference_start", receipt.inference_start, pm.inference_start),
        ("inference_end", receipt.inference_end, pm.inference_end),
        ("label partition_start", receipt.inference_start, lm.partition_start),
        ("label partition_end", receipt.inference_end, lm.partition_end),
    ):
        _require_equal(name, expected, actual)

    prediction_keys = tuple((row.timestamp, row.symbol) for row in prediction.rows)
    label_keys = tuple((row.label_as_of, row.symbol) for row in labels.rows)
    if prediction_keys != label_keys:
        raise DevelopmentEvaluationCompatibilityError(
            "prediction and DEVELOPMENT label keysets must match exactly"
        )
    if len(prediction_keys) != receipt.prediction_count or len(label_keys) != lm.row_count:
        raise DevelopmentEvaluationIntegrityError("evaluation row count mismatch")
    if len(prediction_keys) < MIN_OBSERVATIONS:
        raise DevelopmentEvaluationGovernanceError("too few DEVELOPMENT observations")
    keyset_hash = _hash([{"timestamp": ts, "symbol": symbol} for ts, symbol in prediction_keys])
    if keyset_hash != receipt.inference_keyset_hash:
        raise DevelopmentEvaluationIntegrityError("evaluation keyset does not match D2A receipt")

    scores = tuple(float(row.score) for row in prediction.rows)
    targets = tuple(float(row.value) for row in labels.rows)
    pearson_ic = _pearson(scores, targets)
    spearman_ic = _pearson(_average_ranks(scores), _average_ranks(targets))
    errors = tuple(score - target for score, target in zip(scores, targets, strict=True))
    mae = _mean(tuple(abs(value) for value in errors))
    rmse = sqrt(_mean(tuple(value * value for value in errors)))
    sign_accuracy = _mean(
        tuple(
            1.0 if _sign(score) == _sign(target) else 0.0
            for score, target in zip(scores, targets, strict=True)
        )
    )

    cross_sections = _cross_sectional_metrics(prediction, labels)
    if not cross_sections:
        raise DevelopmentEvaluationGovernanceError(
            "no timestamp has enough non-degenerate cross-sectional observations"
        )
    cs_ic = tuple(item.pearson_ic for item in cross_sections)
    cs_rank_ic = tuple(item.spearman_ic for item in cross_sections)
    metrics = DevelopmentPredictionMetrics(
        observation_count=len(scores),
        pearson_ic=pearson_ic,
        spearman_ic=spearman_ic,
        mae=mae,
        rmse=rmse,
        sign_accuracy=sign_accuracy,
        cross_section_count=len(cross_sections),
        mean_cross_sectional_ic=_mean(cs_ic),
        median_cross_sectional_ic=float(median(cs_ic)),
        positive_cross_sectional_ic_ratio=_mean(tuple(1.0 if value > 0.0 else 0.0 for value in cs_ic)),
        mean_cross_sectional_rank_ic=_mean(cs_rank_ic),
        median_cross_sectional_rank_ic=float(median(cs_rank_ic)),
        positive_cross_sectional_rank_ic_ratio=_mean(
            tuple(1.0 if value > 0.0 else 0.0 for value in cs_rank_ic)
        ),
    )
    manifest = DevelopmentEvaluationManifest(
        producer_id=OSS3D2D_PRODUCER_ID,
        metric_policy_id=METRIC_POLICY_ID,
        key_policy_id=KEY_POLICY_ID,
        campaign_id=receipt.campaign_id,
        research_split_hash=receipt.research_split_hash,
        source_universe_hash=receipt.source_universe_hash,
        label_definition_hash=receipt.label_definition_hash,
        prediction_receipt_hash=receipt.fingerprint,
        prediction_artifact_hash=prediction.artifact_hash,
        prediction_manifest_hash=pm.fingerprint,
        prediction_payload_hash=pm.prediction_payload_hash,
        development_label_artifact_hash=labels.artifact_hash,
        development_label_manifest_hash=lm.fingerprint,
        development_label_payload_hash=lm.row_payload_hash,
        evaluation_keyset_hash=keyset_hash,
        environment_attestation_hash=environment_attestation_hash,
        model_family=receipt.model_family,
        model_config_hash=receipt.model_config_hash,
        qlib_version=receipt.qlib_version,
        producer_code_hash=receipt.producer_code_hash,
        evaluation_start=receipt.inference_start,
        evaluation_end=receipt.inference_end,
        observation_count=len(scores),
        cross_section_count=len(cross_sections),
    )
    payload = {
        "evaluation_version": OSS3D2D_EVALUATION_VERSION,
        "manifest": manifest.to_dict(),
        "metrics": metrics.to_dict(),
        "cross_sections": [item.to_dict() for item in cross_sections],
    }
    return DevelopmentEvaluationArtifact(
        evaluation_version=OSS3D2D_EVALUATION_VERSION,
        manifest=manifest,
        metrics=metrics,
        cross_sections=cross_sections,
        artifact_hash=_hash(payload),
    )


def _cross_sectional_metrics(
    prediction: QlibPredictionArtifact,
    labels: SupervisedLabelArtifact,
) -> tuple[CrossSectionalIC, ...]:
    grouped: dict[str, list[tuple[float, float]]] = {}
    for pred, label in zip(prediction.rows, labels.rows, strict=True):
        grouped.setdefault(pred.timestamp, []).append((float(pred.score), float(label.value)))
    result: list[CrossSectionalIC] = []
    for timestamp in sorted(grouped):
        pairs = grouped[timestamp]
        if len(pairs) < MIN_CROSS_SECTIONAL_OBSERVATIONS:
            continue
        scores = tuple(pair[0] for pair in pairs)
        targets = tuple(pair[1] for pair in pairs)
        if _is_constant(scores) or _is_constant(targets):
            continue
        result.append(
            CrossSectionalIC(
                timestamp=timestamp,
                observation_count=len(pairs),
                pearson_ic=_pearson(scores, targets),
                spearman_ic=_pearson(_average_ranks(scores), _average_ranks(targets)),
            )
        )
    return tuple(result)


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < MIN_OBSERVATIONS:
        raise DevelopmentEvaluationGovernanceError("correlation requires at least three paired values")
    left_mean = _mean(tuple(float(value) for value in left))
    right_mean = _mean(tuple(float(value) for value in right))
    left_centered = tuple(float(value) - left_mean for value in left)
    right_centered = tuple(float(value) - right_mean for value in right)
    left_ss = sum(value * value for value in left_centered)
    right_ss = sum(value * value for value in right_centered)
    if left_ss <= 0.0 or right_ss <= 0.0:
        raise DevelopmentEvaluationGovernanceError("correlation is undefined for constant values")
    covariance = sum(a * b for a, b in zip(left_centered, right_centered, strict=True))
    value = covariance / sqrt(left_ss * right_ss)
    if not isfinite(value):
        raise DevelopmentEvaluationIntegrityError("correlation produced non-finite value")
    return max(-1.0, min(1.0, float(value)))


def _average_ranks(values: Sequence[float]) -> tuple[float, ...]:
    indexed = sorted(enumerate(float(value) for value in values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(indexed)
    cursor = 0
    while cursor < len(indexed):
        stop = cursor + 1
        while stop < len(indexed) and indexed[stop][1] == indexed[cursor][1]:
            stop += 1
        average_rank = ((cursor + 1) + stop) / 2.0
        for position in range(cursor, stop):
            ranks[indexed[position][0]] = average_rank
        cursor = stop
    return tuple(ranks)


def _is_constant(values: Sequence[float]) -> bool:
    first = float(values[0])
    return all(float(value) == first for value in values[1:])


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise DevelopmentEvaluationIntegrityError("cannot average empty values")
    value = sum(float(item) for item in values) / len(values)
    if not isfinite(value):
        raise DevelopmentEvaluationIntegrityError("metric produced non-finite value")
    return float(value)


def _sign(value: float) -> int:
    if value > 0.0:
        return 1
    if value < 0.0:
        return -1
    return 0


def _require_equal(name: str, expected: object, actual: object) -> None:
    if expected != actual:
        raise DevelopmentEvaluationCompatibilityError(
            f"{name} mismatch: expected {expected!r}, got {actual!r}"
        )


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase sha256")


def _require_finite(value: object, name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(float(value)):
        raise ValueError(f"{name} must be finite")


def _canonical_json(payload: object) -> str:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DevelopmentEvaluationIntegrityError("value is not canonical JSON") from exc


def _hash(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
