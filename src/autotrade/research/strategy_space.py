from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from itertools import product
import json
from math import prod
from typing import Mapping, Sequence

from .strategy_catalog import InvalidLibraryStrategySpec, LibraryStrategySpec


Primitive = str | int | float | bool


class StrategySpaceError(ValueError):
    pass


def _canonical_value(value: Primitive) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise StrategySpaceError("search-space values must be finite JSON primitives") from exc


@dataclass(frozen=True, slots=True)
class StrategySearchSpace:
    """Finite, deterministic parameter search space for one audited strategy kind.

    The object deliberately forbids random samplers, callbacks and arbitrary code.
    Every generated candidate is validated by `LibraryStrategySpec` before it is
    returned. `max_candidates` creates an explicit anti-grid-explosion boundary so
    the resulting family can be preregistered and corrected for multiple testing.
    """

    family_id: str
    strategy_version: str
    kind: str
    dimensions: Mapping[str, Sequence[Primitive]]
    max_candidates: int = 256

    def __post_init__(self) -> None:
        if not self.family_id.strip():
            raise StrategySpaceError("family_id is required")
        if not self.strategy_version.strip():
            raise StrategySpaceError("strategy_version is required")
        if self.max_candidates <= 0:
            raise StrategySpaceError("max_candidates must be > 0")
        if not self.dimensions:
            raise StrategySpaceError("dimensions cannot be empty")

        for name, values in self.dimensions.items():
            if not name.strip():
                raise StrategySpaceError("dimension names cannot be blank")
            if isinstance(values, (str, bytes)) or not values:
                raise StrategySpaceError(f"dimension {name} must contain values")
            encoded = tuple(_canonical_value(value) for value in values)
            if len(encoded) != len(set(encoded)):
                raise StrategySpaceError(f"dimension {name} contains duplicate values")

        if self.candidate_count > self.max_candidates:
            raise StrategySpaceError(
                f"candidate count {self.candidate_count} exceeds max_candidates "
                f"{self.max_candidates}"
            )

        # Force catalog/parameter validation at construction time rather than after
        # a large research campaign has already been created.
        self.candidates()

    @property
    def candidate_count(self) -> int:
        return prod(len(tuple(values)) for values in self.dimensions.values())

    @property
    def canonical_hash(self) -> str:
        payload = {
            "family_id": self.family_id,
            "strategy_version": self.strategy_version,
            "kind": self.kind,
            "dimensions": {
                name: list(self.dimensions[name]) for name in sorted(self.dimensions)
            },
            "max_candidates": self.max_candidates,
        }
        raw = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(raw).hexdigest()

    def candidates(self) -> tuple[LibraryStrategySpec, ...]:
        names = tuple(sorted(self.dimensions))
        ordered_values = tuple(
            tuple(sorted(self.dimensions[name], key=_canonical_value)) for name in names
        )
        result: list[LibraryStrategySpec] = []
        for values in product(*ordered_values):
            parameters = dict(zip(names, values, strict=True))
            parameter_hash = sha256(
                json.dumps(
                    parameters,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()[:16]
            strategy_id = f"{self.family_id}-{parameter_hash}"
            try:
                candidate = LibraryStrategySpec(
                    strategy_id=strategy_id,
                    strategy_version=self.strategy_version,
                    kind=self.kind,
                    parameters=parameters,
                )
            except InvalidLibraryStrategySpec as exc:
                raise StrategySpaceError(
                    f"invalid candidate generated for {self.family_id}: {exc}"
                ) from exc
            result.append(candidate)

        result.sort(key=lambda item: item.strategy_id)
        if len(result) != self.candidate_count:
            raise StrategySpaceError("candidate accounting mismatch")
        if len({item.strategy_id for item in result}) != len(result):
            raise StrategySpaceError("candidate strategy identifiers are not unique")
        return tuple(result)


__all__ = ["StrategySearchSpace", "StrategySpaceError"]
