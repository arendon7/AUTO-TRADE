from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .market import MarketDataset
from .registry import HoldoutPermit, SQLiteExperimentRegistry


class InvalidTemporalSplit(ValueError):
    pass


class ProtectedHoldout:
    __slots__ = ("__dataset",)

    def __init__(self, dataset: MarketDataset) -> None:
        self.__dataset = dataset

    @property
    def dataset_hash(self) -> str:
        return self.__dataset.dataset_hash

    @property
    def bar_count(self) -> int:
        return len(self.__dataset.bars)

    @property
    def started_at(self) -> datetime:
        return self.__dataset.started_at

    @property
    def ended_at(self) -> datetime:
        return self.__dataset.ended_at

    def checkout(
        self,
        *,
        permit: HoldoutPermit,
        registry: SQLiteExperimentRegistry,
        now: datetime,
    ) -> MarketDataset:
        registry.consume_holdout_permit(permit=permit, now=now)
        return self.__dataset


@dataclass(frozen=True, slots=True)
class TemporalResearchSplit:
    train: MarketDataset
    development: MarketDataset
    protected_holdout: ProtectedHoldout


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_index: int
    train: MarketDataset
    evaluation: MarketDataset


def create_temporal_split(
    dataset: MarketDataset,
    *,
    train_bars: int,
    development_bars: int,
) -> TemporalResearchSplit:
    if train_bars <= 0:
        raise InvalidTemporalSplit("train_bars must be > 0")
    if development_bars <= 0:
        raise InvalidTemporalSplit("development_bars must be > 0")
    holdout_start = train_bars + development_bars
    if holdout_start >= len(dataset.bars):
        raise InvalidTemporalSplit("split must leave at least one protected holdout bar")

    train = dataset.slice(0, train_bars)
    development = dataset.slice(train_bars, holdout_start)
    holdout = dataset.slice(holdout_start, len(dataset.bars))
    if not (train.ended_at <= development.started_at <= holdout.started_at):
        raise InvalidTemporalSplit("temporal split ordering invariant failed")
    return TemporalResearchSplit(
        train=train,
        development=development,
        protected_holdout=ProtectedHoldout(holdout),
    )


def generate_walk_forward_folds(
    dataset: MarketDataset,
    *,
    train_bars: int,
    evaluation_bars: int,
    step_bars: int | None = None,
    expanding: bool = False,
) -> tuple[WalkForwardFold, ...]:
    if train_bars <= 0:
        raise InvalidTemporalSplit("train_bars must be > 0")
    if evaluation_bars <= 0:
        raise InvalidTemporalSplit("evaluation_bars must be > 0")
    step = evaluation_bars if step_bars is None else step_bars
    if step <= 0:
        raise InvalidTemporalSplit("step_bars must be > 0")
    if train_bars + evaluation_bars > len(dataset.bars):
        raise InvalidTemporalSplit("dataset is too short for one walk-forward fold")

    folds: list[WalkForwardFold] = []
    evaluation_start = train_bars
    fold_index = 0
    while evaluation_start + evaluation_bars <= len(dataset.bars):
        train_start = 0 if expanding else evaluation_start - train_bars
        train = dataset.slice(train_start, evaluation_start)
        evaluation = dataset.slice(
            evaluation_start,
            evaluation_start + evaluation_bars,
        )
        if train.ended_at > evaluation.started_at:
            raise InvalidTemporalSplit("walk-forward train/evaluation overlap")
        folds.append(
            WalkForwardFold(
                fold_index=fold_index,
                train=train,
                evaluation=evaluation,
            )
        )
        fold_index += 1
        evaluation_start += step

    return tuple(folds)
