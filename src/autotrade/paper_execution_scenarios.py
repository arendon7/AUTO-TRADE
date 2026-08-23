from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Iterable

from autotrade.brokers.paper_execution import (
    DeterministicPaperExecutionBroker,
    PaperExecutionConfig,
)


_SCENARIO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperExecutionScenarioError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PaperExecutionScenario:
    """Versionable execution assumptions for one deterministic PAPER stress case."""

    scenario_id: str
    purpose: str
    config: PaperExecutionConfig
    scenario_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not _SCENARIO_ID_RE.fullmatch(self.scenario_id):
            raise PaperExecutionScenarioError("scenario_id must be canonical lowercase identifier")
        if (
            not isinstance(self.purpose, str)
            or not self.purpose.strip()
            or self.purpose != self.purpose.strip()
            or len(self.purpose) > 240
        ):
            raise PaperExecutionScenarioError("purpose must be canonical non-empty text <=240 chars")
        if not isinstance(self.config, PaperExecutionConfig):
            raise TypeError("scenario config must be PaperExecutionConfig")
        if not isinstance(self.scenario_hash, str) or not _HASH_RE.fullmatch(self.scenario_hash):
            raise PaperExecutionScenarioError("scenario_hash must be lowercase sha256")
        if self.scenario_hash != _hash(_scenario_payload(self, include_hash=False)):
            raise PaperExecutionScenarioError("scenario_hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _scenario_payload(self, include_hash=True)

    def build_broker(self) -> DeterministicPaperExecutionBroker:
        return DeterministicPaperExecutionBroker(config=self.config)


@dataclass(frozen=True, slots=True)
class PaperExecutionScenarioMatrix:
    """Frozen preregistrable set of execution stress assumptions.

    A matrix requires at least two distinct scenarios so Strategy Lab cannot call
    one optimistic fill assumption an execution-sensitivity analysis.
    """

    scenarios: tuple[PaperExecutionScenario, ...]
    matrix_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.scenarios, tuple) or len(self.scenarios) < 2:
            raise PaperExecutionScenarioError("execution scenario matrix requires at least two scenarios")
        if len(self.scenarios) > 32:
            raise PaperExecutionScenarioError("execution scenario matrix supports at most 32 scenarios")
        if any(not isinstance(item, PaperExecutionScenario) for item in self.scenarios):
            raise TypeError("matrix contains non-scenario value")
        ordered = tuple(sorted(self.scenarios, key=lambda item: item.scenario_id))
        if ordered != self.scenarios:
            raise PaperExecutionScenarioError("matrix scenarios must be sorted by scenario_id")
        ids = tuple(item.scenario_id for item in self.scenarios)
        if len(set(ids)) != len(ids):
            raise PaperExecutionScenarioError("matrix scenario_id values must be unique")
        hashes = tuple(item.scenario_hash for item in self.scenarios)
        if len(set(hashes)) != len(hashes):
            raise PaperExecutionScenarioError("matrix contains duplicate execution assumptions")
        if not isinstance(self.matrix_hash, str) or not _HASH_RE.fullmatch(self.matrix_hash):
            raise PaperExecutionScenarioError("matrix_hash must be lowercase sha256")
        if self.matrix_hash != _hash(_matrix_payload(self.scenarios)):
            raise PaperExecutionScenarioError("matrix_hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return {
            "scenarios": [item.to_dict() for item in self.scenarios],
            "matrix_hash": self.matrix_hash,
        }


def build_paper_execution_scenario(
    *,
    scenario_id: str,
    purpose: str,
    slippage_bps: Decimal,
    max_fill_fraction: Decimal,
    max_market_age: timedelta,
    max_spread_bps: Decimal,
) -> PaperExecutionScenario:
    config = PaperExecutionConfig(
        slippage_bps=slippage_bps,
        max_fill_fraction=max_fill_fraction,
        max_market_age=max_market_age,
        max_spread_bps=max_spread_bps,
    )
    values = {
        "scenario_id": scenario_id,
        "purpose": purpose,
        "config": config,
    }
    provisional = PaperExecutionScenario.__new__(PaperExecutionScenario)
    object.__setattr__(provisional, "scenario_id", scenario_id)
    object.__setattr__(provisional, "purpose", purpose)
    object.__setattr__(provisional, "config", config)
    object.__setattr__(provisional, "scenario_hash", "0" * 64)
    digest = _hash(_scenario_payload(provisional, include_hash=False))
    return PaperExecutionScenario(**values, scenario_hash=digest)


def build_paper_execution_scenario_matrix(
    scenarios: Iterable[PaperExecutionScenario],
) -> PaperExecutionScenarioMatrix:
    values = tuple(sorted(tuple(scenarios), key=lambda item: item.scenario_id))
    return PaperExecutionScenarioMatrix(
        scenarios=values,
        matrix_hash=_hash(_matrix_payload(values)),
    )


def _scenario_payload(
    scenario: PaperExecutionScenario,
    *,
    include_hash: bool,
) -> dict[str, object]:
    config = scenario.config
    payload: dict[str, object] = {
        "scenario_id": scenario.scenario_id,
        "purpose": scenario.purpose,
        "config": {
            "slippage_bps": _decimal(config.slippage_bps),
            "max_fill_fraction": _decimal(config.max_fill_fraction),
            "max_market_age_us": _timedelta_microseconds(config.max_market_age),
            "max_spread_bps": _decimal(config.max_spread_bps),
        },
    }
    if include_hash:
        payload["scenario_hash"] = scenario.scenario_hash
    return payload


def _matrix_payload(scenarios: tuple[PaperExecutionScenario, ...]) -> dict[str, object]:
    return {
        "scenario_hashes": [item.scenario_hash for item in scenarios],
        "scenario_ids": [item.scenario_id for item in scenarios],
    }


def _timedelta_microseconds(value: timedelta) -> int:
    return ((value.days * 24 * 60 * 60 + value.seconds) * 1_000_000) + value.microseconds


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "PaperExecutionScenario",
    "PaperExecutionScenarioError",
    "PaperExecutionScenarioMatrix",
    "build_paper_execution_scenario",
    "build_paper_execution_scenario_matrix",
]
