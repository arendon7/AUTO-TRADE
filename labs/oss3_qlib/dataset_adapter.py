"""Minimal in-memory dataset adapter for Qlib LinearModel.

The adapter intentionally implements only the two prepare calls required by
Qlib's LinearModel:

- prepare("train", col_set=["feature", "label"], data_key="learn")
- prepare("test", col_set="feature", data_key="infer")

It does not initialize Qlib data providers or read external market data.
"""

from __future__ import annotations

from math import isfinite
from typing import Sequence

import pandas as pd

from autotrade.research.oss3_factor_matrix_artifact import FactorMatrixArtifact
from autotrade.research.oss3_supervised_label_artifact import SupervisedLabelArtifact


class QlibDatasetAdapterError(RuntimeError):
    """The isolated in-memory dataset contract is inconsistent."""


class QlibArtifactDatasetAdapter:
    """Exact TRAIN + DEVELOPMENT adapter for the frozen LinearModel canary."""

    def __init__(
        self,
        *,
        train_features: FactorMatrixArtifact,
        train_labels: SupervisedLabelArtifact,
        development_features: FactorMatrixArtifact,
    ) -> None:
        if train_features.manifest.partition != "TRAIN":
            raise QlibDatasetAdapterError("train_features must be TRAIN")
        if train_labels.manifest.partition != "TRAIN":
            raise QlibDatasetAdapterError("train_labels must be TRAIN")
        if development_features.manifest.partition != "DEVELOPMENT":
            raise QlibDatasetAdapterError("development_features must be DEVELOPMENT")
        if train_features.manifest.feature_schema_hash != development_features.manifest.feature_schema_hash:
            raise QlibDatasetAdapterError("TRAIN/DEVELOPMENT feature schema mismatch")

        train_keys = tuple((row.as_of, row.symbol) for row in train_features.rows)
        label_keys = tuple((row.label_as_of, row.symbol) for row in train_labels.rows)
        if train_keys != label_keys:
            raise QlibDatasetAdapterError("TRAIN feature/label keyset mismatch")

        self._feature_names = tuple(feature.name for feature in train_features.features)
        self._label_name = train_labels.label.name
        self._train = _build_train_frame(
            train_features=train_features,
            train_labels=train_labels,
            feature_names=self._feature_names,
            label_name=self._label_name,
        )
        self._test = _build_feature_frame(
            artifact=development_features,
            feature_names=self._feature_names,
        )

    @property
    def train_frame(self) -> pd.DataFrame:
        return self._train.copy(deep=True)

    @property
    def test_frame(self) -> pd.DataFrame:
        return self._test.copy(deep=True)

    def prepare(self, segment: object, col_set: object, data_key: object = "infer") -> pd.DataFrame:
        """Implement only the exact Qlib LinearModel dataset requests."""
        if segment == "train":
            if data_key != "learn":
                raise QlibDatasetAdapterError("TRAIN requires Qlib learn data_key")
            if not isinstance(col_set, (list, tuple)) or tuple(col_set) != ("feature", "label"):
                raise QlibDatasetAdapterError("TRAIN col_set must be ['feature','label']")
            return self._train.copy(deep=True)
        if segment == "test":
            if data_key != "infer":
                raise QlibDatasetAdapterError("TEST requires Qlib infer data_key")
            if col_set != "feature":
                raise QlibDatasetAdapterError("TEST col_set must be 'feature'")
            return self._test["feature"].copy(deep=True)
        raise QlibDatasetAdapterError("OSS-3D2B supports TRAIN and TEST segments only")


def _build_train_frame(
    *,
    train_features: FactorMatrixArtifact,
    train_labels: SupervisedLabelArtifact,
    feature_names: Sequence[str],
    label_name: str,
) -> pd.DataFrame:
    index = _index_from_factor_rows(train_features)
    values = [list(map(float, row.values)) + [float(label.value)] for row, label in zip(
        train_features.rows,
        train_labels.rows,
        strict=True,
    )]
    columns = pd.MultiIndex.from_tuples(
        [("feature", name) for name in feature_names] + [("label", label_name)]
    )
    frame = pd.DataFrame(values, index=index, columns=columns, dtype="float64")
    _assert_finite_frame(frame, "TRAIN")
    return frame


def _build_feature_frame(
    *,
    artifact: FactorMatrixArtifact,
    feature_names: Sequence[str],
) -> pd.DataFrame:
    index = _index_from_factor_rows(artifact)
    columns = pd.MultiIndex.from_tuples([("feature", name) for name in feature_names])
    frame = pd.DataFrame(
        [list(map(float, row.values)) for row in artifact.rows],
        index=index,
        columns=columns,
        dtype="float64",
    )
    _assert_finite_frame(frame, "DEVELOPMENT")
    return frame


def _index_from_factor_rows(artifact: FactorMatrixArtifact) -> pd.MultiIndex:
    return pd.MultiIndex.from_tuples(
        [(pd.Timestamp(row.as_of), row.symbol) for row in artifact.rows],
        names=("datetime", "instrument"),
    )


def _assert_finite_frame(frame: pd.DataFrame, context: str) -> None:
    if frame.empty:
        raise QlibDatasetAdapterError(f"{context} frame cannot be empty")
    for value in frame.to_numpy().reshape(-1):
        if not isfinite(float(value)):
            raise QlibDatasetAdapterError(f"{context} frame contains non-finite data")
