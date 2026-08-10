from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import random
from statistics import fmean


class InvalidBootstrapConfig(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MovingBlockBootstrapConfig:
    iterations: int
    block_size: int
    seed: int
    confidence_level: float = 0.95

    def __post_init__(self) -> None:
        if self.iterations <= 0:
            raise InvalidBootstrapConfig("iterations must be > 0")
        if self.block_size <= 0:
            raise InvalidBootstrapConfig("block_size must be > 0")
        if not 0 < self.confidence_level < 1:
            raise InvalidBootstrapConfig("confidence_level must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class MovingBlockBootstrapResult:
    observations: int
    iterations: int
    block_size: int
    seed: int
    mean_compounded_return: float
    median_compounded_return: float
    lower_compounded_return: float
    upper_compounded_return: float
    probability_positive: float
    distribution: tuple[float, ...]


def moving_block_bootstrap(
    returns: tuple[float, ...],
    *,
    config: MovingBlockBootstrapConfig,
) -> MovingBlockBootstrapResult:
    if not returns:
        raise InvalidBootstrapConfig("returns cannot be empty")
    if config.block_size > len(returns):
        raise InvalidBootstrapConfig("block_size cannot exceed observation count")
    for value in returns:
        if not isfinite(value):
            raise InvalidBootstrapConfig("returns must be finite")
        if value <= -1:
            raise InvalidBootstrapConfig("period returns must be greater than -1")

    rng = random.Random(config.seed)
    max_start = len(returns) - config.block_size
    distribution: list[float] = []

    for _ in range(config.iterations):
        sample: list[float] = []
        while len(sample) < len(returns):
            start = rng.randint(0, max_start)
            sample.extend(returns[start : start + config.block_size])
        sample = sample[: len(returns)]

        compounded = 1.0
        for value in sample:
            compounded *= 1.0 + value
        distribution.append(compounded - 1.0)

    ordered = sorted(distribution)
    alpha = 1.0 - config.confidence_level
    lower_index = int((alpha / 2.0) * (len(ordered) - 1))
    upper_index = int((1.0 - alpha / 2.0) * (len(ordered) - 1))
    median_index = (len(ordered) - 1) // 2

    return MovingBlockBootstrapResult(
        observations=len(returns),
        iterations=config.iterations,
        block_size=config.block_size,
        seed=config.seed,
        mean_compounded_return=fmean(distribution),
        median_compounded_return=ordered[median_index],
        lower_compounded_return=ordered[lower_index],
        upper_compounded_return=ordered[upper_index],
        probability_positive=sum(value > 0 for value in distribution) / len(distribution),
        distribution=tuple(distribution),
    )
