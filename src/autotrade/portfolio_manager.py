from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Mapping, Protocol

from .health_bridge import HealthBridgeControlProvider, HealthBridgeError
from .instrument_master import (
    AuthoritativeInstrumentRules,
    InstrumentMasterError,
)
from .research.allocation_robustness import (
    AllocationRobustnessError,
    AllocationRobustnessPolicy,
    AllocationRobustnessSpec,
    FragileAllocation,
    require_robust_allocation,
)
from .research.portfolio_dependence import (
    AllocationBudgetEvidence,
    DependenceEvidence,
    DiversificationBudgetPolicy,
    validate_allocation_budget,
)


_ZERO = Decimal("0")
_ONE = Decimal("1")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PortfolioSizingError(RuntimeError):
    pass


class PortfolioSizingBlocked(PortfolioSizingError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


class InstrumentRulesReader(Protocol):
    def require_tradable(
        self,
        *,
        venue: str,
        symbol: str,
        now: datetime,
        max_age: timedelta,
    ) -> AuthoritativeInstrumentRules: ...


@dataclass(frozen=True, slots=True)
class PortfolioSizingPolicy:
    max_quote_age_seconds: int = 5
    max_instrument_rule_age_seconds: int = 3600

    def __post_init__(self) -> None:
        for name, value in (
            ("max_quote_age_seconds", self.max_quote_age_seconds),
            ("max_instrument_rule_age_seconds", self.max_instrument_rule_age_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be integer > 0")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "max_quote_age_seconds": self.max_quote_age_seconds,
                "max_instrument_rule_age_seconds": self.max_instrument_rule_age_seconds,
            }
        )


@dataclass(frozen=True, slots=True)
class SizingCandidate:
    strategy_key: str
    health_entity_id: str
    venue: str
    symbol: str
    reference_price: Decimal
    quote_observed_at: datetime
    quote_source_sha256: str

    def __post_init__(self) -> None:
        for name, value in (
            ("strategy_key", self.strategy_key),
            ("health_entity_id", self.health_entity_id),
            ("venue", self.venue),
            ("symbol", self.symbol),
        ):
            _identity(value, name)
        if "@" not in self.strategy_key:
            raise ValueError("strategy_key must be versioned as strategy_id@version")
        if not _positive_decimal(self.reference_price):
            raise ValueError("reference_price must be finite Decimal > 0")
        if not _aware(self.quote_observed_at):
            raise ValueError("quote_observed_at must be timezone-aware")
        _hash_value(self.quote_source_sha256, "quote_source_sha256")

    @property
    def instrument_key(self) -> str:
        return f"{self.venue}:{self.symbol}"

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "strategy_key": self.strategy_key,
                "health_entity_id": self.health_entity_id,
                "venue": self.venue,
                "symbol": self.symbol,
                "reference_price": str(self.reference_price),
                "quote_observed_at": self.quote_observed_at.isoformat(),
                "quote_source_sha256": self.quote_source_sha256,
            }
        )


class AllocationDisposition(StrEnum):
    SIZED = "SIZED"
    ZERO_WEIGHT = "ZERO_WEIGHT"
    BELOW_VENUE_MINIMUM = "BELOW_VENUE_MINIMUM"


@dataclass(frozen=True, slots=True)
class StrategySizingAllocation:
    strategy_key: str
    health_entity_id: str
    instrument_key: str
    base_weight: Decimal
    health_multiplier: Decimal
    health_adjusted_weight: Decimal
    final_weight: Decimal
    notional: Decimal
    quantity: Decimal
    reference_price: Decimal
    disposition: AllocationDisposition
    quote_fingerprint: str
    instrument_rules_fingerprint: str
    health_strategy_state_fingerprint: str
    health_portfolio_state_fingerprint: str

    def __post_init__(self) -> None:
        for name, value in (
            ("strategy_key", self.strategy_key),
            ("health_entity_id", self.health_entity_id),
            ("instrument_key", self.instrument_key),
        ):
            _identity(value, name)
        for name, value in (
            ("base_weight", self.base_weight),
            ("health_multiplier", self.health_multiplier),
            ("health_adjusted_weight", self.health_adjusted_weight),
            ("final_weight", self.final_weight),
            ("notional", self.notional),
            ("quantity", self.quantity),
        ):
            if not _nonnegative_decimal(value):
                raise ValueError(f"{name} must be finite Decimal >= 0")
        if self.base_weight > _ONE or self.health_multiplier > _ONE:
            raise ValueError("base_weight and health_multiplier cannot exceed 1")
        if self.health_adjusted_weight > self.base_weight:
            raise ValueError("health adjustment cannot increase weight")
        if self.final_weight > self.health_adjusted_weight:
            raise ValueError("venue sizing cannot increase health-adjusted weight")
        if self.notional > _ZERO and not _positive_decimal(self.reference_price):
            raise ValueError("positive notional requires positive reference_price")
        if not isinstance(self.disposition, AllocationDisposition):
            raise ValueError("disposition must be AllocationDisposition")
        for name, value in (
            ("quote_fingerprint", self.quote_fingerprint),
            ("instrument_rules_fingerprint", self.instrument_rules_fingerprint),
            ("health_strategy_state_fingerprint", self.health_strategy_state_fingerprint),
            ("health_portfolio_state_fingerprint", self.health_portfolio_state_fingerprint),
        ):
            if value:
                _hash_value(value, name)
        if self.disposition is AllocationDisposition.SIZED:
            if self.quantity <= _ZERO or self.notional <= _ZERO:
                raise ValueError("SIZED allocation requires positive quantity and notional")
            _hash_value(self.quote_fingerprint, "quote_fingerprint")
            _hash_value(self.instrument_rules_fingerprint, "instrument_rules_fingerprint")
        elif self.quantity != _ZERO or self.notional != _ZERO or self.final_weight != _ZERO:
            raise ValueError("non-SIZED allocations must publish zero executable capacity")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "strategy_key": self.strategy_key,
            "health_entity_id": self.health_entity_id,
            "instrument_key": self.instrument_key,
            "base_weight": str(self.base_weight),
            "health_multiplier": str(self.health_multiplier),
            "health_adjusted_weight": str(self.health_adjusted_weight),
            "final_weight": str(self.final_weight),
            "notional": str(self.notional),
            "quantity": str(self.quantity),
            "reference_price": str(self.reference_price),
            "disposition": self.disposition.value,
            "quote_fingerprint": self.quote_fingerprint,
            "instrument_rules_fingerprint": self.instrument_rules_fingerprint,
            "health_strategy_state_fingerprint": self.health_strategy_state_fingerprint,
            "health_portfolio_state_fingerprint": self.health_portfolio_state_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class PortfolioSizingDecision:
    equity: Decimal
    dependence_fingerprint: str
    sizing_policy_fingerprint: str
    diversification_policy_fingerprint: str
    robustness_spec_fingerprint: str
    robustness_policy_fingerprint: str
    base_budget_fingerprint: str
    base_robustness_fingerprint: str
    health_budget_fingerprint: str
    health_robustness_fingerprint: str
    final_budget_fingerprint: str
    final_robustness_fingerprint: str
    total_notional: Decimal
    allocations: tuple[StrategySizingAllocation, ...]
    sized_at: datetime

    def __post_init__(self) -> None:
        if not _positive_decimal(self.equity):
            raise ValueError("equity must be finite Decimal > 0")
        for name, value in (
            ("dependence_fingerprint", self.dependence_fingerprint),
            ("sizing_policy_fingerprint", self.sizing_policy_fingerprint),
            ("diversification_policy_fingerprint", self.diversification_policy_fingerprint),
            ("robustness_spec_fingerprint", self.robustness_spec_fingerprint),
            ("robustness_policy_fingerprint", self.robustness_policy_fingerprint),
            ("base_budget_fingerprint", self.base_budget_fingerprint),
            ("base_robustness_fingerprint", self.base_robustness_fingerprint),
            ("health_budget_fingerprint", self.health_budget_fingerprint),
            ("health_robustness_fingerprint", self.health_robustness_fingerprint),
            ("final_budget_fingerprint", self.final_budget_fingerprint),
            ("final_robustness_fingerprint", self.final_robustness_fingerprint),
        ):
            _hash_value(value, name)
        if not _nonnegative_decimal(self.total_notional):
            raise ValueError("total_notional must be finite Decimal >= 0")
        keys = tuple(item.strategy_key for item in self.allocations)
        if not keys or keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("allocations must be non-empty unique canonical sorted strategy keys")
        if sum((item.notional for item in self.allocations), _ZERO) != self.total_notional:
            raise ValueError("total_notional must equal allocation notionals")
        if self.total_notional > self.equity:
            raise ValueError("sizing decision cannot exceed equity")
        if not _aware(self.sized_at):
            raise ValueError("sized_at must be timezone-aware")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_payload(include_fingerprint=False))

    def to_payload(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "equity": str(self.equity),
            "dependence_fingerprint": self.dependence_fingerprint,
            "sizing_policy_fingerprint": self.sizing_policy_fingerprint,
            "diversification_policy_fingerprint": self.diversification_policy_fingerprint,
            "robustness_spec_fingerprint": self.robustness_spec_fingerprint,
            "robustness_policy_fingerprint": self.robustness_policy_fingerprint,
            "base_budget_fingerprint": self.base_budget_fingerprint,
            "base_robustness_fingerprint": self.base_robustness_fingerprint,
            "health_budget_fingerprint": self.health_budget_fingerprint,
            "health_robustness_fingerprint": self.health_robustness_fingerprint,
            "final_budget_fingerprint": self.final_budget_fingerprint,
            "final_robustness_fingerprint": self.final_robustness_fingerprint,
            "total_notional": str(self.total_notional),
            "allocations": [item.to_payload() for item in self.allocations],
            "sized_at": self.sized_at.isoformat(),
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload


class DeterministicPortfolioManager:
    """Compute bounded advisory capacity; never creates or submits orders.

    The manager recomputes diversification and robustness gates from source
    evidence. Health and venue metadata can only shrink candidate capacity.
    Any loss of evidence, diversification or robustness fails closed before a
    PortfolioSizingDecision is emitted. The result is still advisory and must
    pass CapitalSafetyKernel and OMS if a separate execution path later creates
    an order intent.
    """

    def __init__(
        self,
        *,
        instrument_rules: InstrumentRulesReader,
        health_controls: HealthBridgeControlProvider,
        policy: PortfolioSizingPolicy | None = None,
    ) -> None:
        self._instrument_rules = instrument_rules
        self._health_controls = health_controls
        self._policy = policy or PortfolioSizingPolicy()

    @property
    def policy(self) -> PortfolioSizingPolicy:
        return self._policy

    def size(
        self,
        *,
        equity: Decimal,
        dependence: DependenceEvidence,
        diversification_policy: DiversificationBudgetPolicy,
        strategy_weights: Mapping[str, Decimal],
        robustness_spec: AllocationRobustnessSpec,
        robustness_policy: AllocationRobustnessPolicy,
        candidates: tuple[SizingCandidate, ...],
        portfolio_health_entity_id: str,
        now: datetime,
    ) -> PortfolioSizingDecision:
        if not _positive_decimal(equity):
            raise PortfolioSizingBlocked("INVALID_EQUITY", "equity must be finite Decimal > 0")
        if not _aware(now):
            raise ValueError("now must be timezone-aware")
        if portfolio_health_entity_id:
            _identity(portfolio_health_entity_id, "portfolio_health_entity_id")
        candidate_map = self._validate_candidate_universe(dependence, candidates)

        base_budget = validate_allocation_budget(
            dependence,
            diversification_policy,
            strategy_weights,
        )
        base_robustness = self._require_robust(
            dependence=dependence,
            diversification_policy=diversification_policy,
            weights=dict(base_budget.strategy_weights),
            robustness_spec=robustness_spec,
            robustness_policy=robustness_policy,
            reason_code="BASE_ALLOCATION_NOT_ROBUST",
        )

        health_weights: dict[str, Decimal] = {}
        health_metadata: dict[str, tuple[Decimal, str, str]] = {}
        for strategy_key, base_weight in base_budget.strategy_weights:
            candidate = candidate_map[strategy_key]
            try:
                control = self._health_controls.effective_control(
                    strategy_id=candidate.health_entity_id,
                    portfolio_entity_id=portfolio_health_entity_id,
                    now=now,
                )
            except HealthBridgeError as exc:
                raise PortfolioSizingBlocked("HEALTH_CONTROL_UNAVAILABLE", str(exc)) from exc
            multiplier = min(
                control.order_multiplier,
                control.strategy_multiplier,
                control.portfolio_multiplier,
            )
            if control.blocks_new_risk:
                multiplier = _ZERO
            adjusted = base_weight * multiplier
            if adjusted > base_weight:
                raise PortfolioSizingError("health control attempted to increase allocation")
            health_weights[strategy_key] = adjusted
            health_metadata[strategy_key] = (
                multiplier,
                control.strategy_state_fingerprint,
                control.portfolio_state_fingerprint,
            )

        health_budget = validate_allocation_budget(
            dependence,
            diversification_policy,
            health_weights,
        )
        self._require_two_positive(health_budget, "INSUFFICIENT_DIVERSIFICATION_AFTER_HEALTH")
        health_robustness = self._require_robust(
            dependence=dependence,
            diversification_policy=diversification_policy,
            weights=health_weights,
            robustness_spec=robustness_spec,
            robustness_policy=robustness_policy,
            reason_code="ALLOCATION_NOT_ROBUST_AFTER_HEALTH",
        )

        preliminary: list[StrategySizingAllocation] = []
        final_weights: dict[str, Decimal] = {}
        for strategy_key, base_weight in base_budget.strategy_weights:
            candidate = candidate_map[strategy_key]
            adjusted_weight = health_weights[strategy_key]
            multiplier, strategy_health_fp, portfolio_health_fp = health_metadata[strategy_key]
            if adjusted_weight == _ZERO:
                preliminary.append(
                    self._zero_allocation(
                        candidate=candidate,
                        base_weight=base_weight,
                        multiplier=multiplier,
                        adjusted_weight=adjusted_weight,
                        disposition=AllocationDisposition.ZERO_WEIGHT,
                        strategy_health_fp=strategy_health_fp,
                        portfolio_health_fp=portfolio_health_fp,
                    )
                )
                final_weights[strategy_key] = _ZERO
                continue

            self._validate_quote(candidate, now=now)
            try:
                rules = self._instrument_rules.require_tradable(
                    venue=candidate.venue,
                    symbol=candidate.symbol,
                    now=now,
                    max_age=timedelta(seconds=self._policy.max_instrument_rule_age_seconds),
                )
            except InstrumentMasterError as exc:
                raise PortfolioSizingBlocked("INSTRUMENT_RULES_UNAVAILABLE", str(exc)) from exc
            if rules.instrument_key != candidate.instrument_key:
                raise PortfolioSizingBlocked(
                    "INSTRUMENT_IDENTITY_MISMATCH",
                    f"{rules.instrument_key}!={candidate.instrument_key}",
                )
            if candidate.reference_price % rules.price_tick != _ZERO:
                raise PortfolioSizingBlocked(
                    "QUOTE_NOT_TICK_ALIGNED",
                    f"{candidate.reference_price} not aligned to {rules.price_tick}",
                )

            notional_cap = equity * adjusted_weight
            if rules.max_notional is not None:
                notional_cap = min(notional_cap, rules.max_notional)
            raw_quantity = notional_cap / candidate.reference_price
            quantity = _floor_step(raw_quantity, rules.quantity_step)
            if rules.max_quantity is not None:
                quantity = min(quantity, rules.max_quantity)
            notional = quantity * candidate.reference_price

            below_minimum = (
                quantity <= _ZERO
                or (rules.min_quantity is not None and quantity < rules.min_quantity)
                or (rules.min_notional is not None and notional < rules.min_notional)
            )
            if below_minimum:
                preliminary.append(
                    self._zero_allocation(
                        candidate=candidate,
                        base_weight=base_weight,
                        multiplier=multiplier,
                        adjusted_weight=adjusted_weight,
                        disposition=AllocationDisposition.BELOW_VENUE_MINIMUM,
                        strategy_health_fp=strategy_health_fp,
                        portfolio_health_fp=portfolio_health_fp,
                        rules_fingerprint=rules.fingerprint,
                    )
                )
                final_weights[strategy_key] = _ZERO
                continue

            try:
                validated_notional = rules.validate_candidate(
                    quantity=quantity,
                    price=candidate.reference_price,
                )
            except InstrumentMasterError as exc:
                raise PortfolioSizingBlocked("INSTRUMENT_CONSTRAINT_VIOLATION", str(exc)) from exc
            if validated_notional != notional or notional > notional_cap:
                raise PortfolioSizingError("venue sizing exceeded conservative notional cap")
            final_weight = notional / equity
            if final_weight > adjusted_weight:
                raise PortfolioSizingError("venue rounding increased allocation weight")
            preliminary.append(
                StrategySizingAllocation(
                    strategy_key=strategy_key,
                    health_entity_id=candidate.health_entity_id,
                    instrument_key=candidate.instrument_key,
                    base_weight=base_weight,
                    health_multiplier=multiplier,
                    health_adjusted_weight=adjusted_weight,
                    final_weight=final_weight,
                    notional=notional,
                    quantity=quantity,
                    reference_price=candidate.reference_price,
                    disposition=AllocationDisposition.SIZED,
                    quote_fingerprint=candidate.fingerprint,
                    instrument_rules_fingerprint=rules.fingerprint,
                    health_strategy_state_fingerprint=strategy_health_fp,
                    health_portfolio_state_fingerprint=portfolio_health_fp,
                )
            )
            final_weights[strategy_key] = final_weight

        final_budget = validate_allocation_budget(
            dependence,
            diversification_policy,
            final_weights,
        )
        self._require_two_positive(
            final_budget,
            "INSUFFICIENT_DIVERSIFICATION_AFTER_VENUE_RULES",
        )
        final_robustness = self._require_robust(
            dependence=dependence,
            diversification_policy=diversification_policy,
            weights=final_weights,
            robustness_spec=robustness_spec,
            robustness_policy=robustness_policy,
            reason_code="ALLOCATION_NOT_ROBUST_AFTER_VENUE_RULES",
        )

        allocations = tuple(sorted(preliminary, key=lambda item: item.strategy_key))
        total_notional = sum((item.notional for item in allocations), _ZERO)
        if total_notional > equity * diversification_policy.max_total_weight:
            raise PortfolioSizingError("final notional exceeds certified total-weight budget")
        return PortfolioSizingDecision(
            equity=equity,
            dependence_fingerprint=dependence.fingerprint,
            sizing_policy_fingerprint=self._policy.fingerprint,
            diversification_policy_fingerprint=diversification_policy.fingerprint,
            robustness_spec_fingerprint=robustness_spec.fingerprint,
            robustness_policy_fingerprint=robustness_policy.fingerprint,
            base_budget_fingerprint=base_budget.fingerprint,
            base_robustness_fingerprint=base_robustness.fingerprint,
            health_budget_fingerprint=health_budget.fingerprint,
            health_robustness_fingerprint=health_robustness.fingerprint,
            final_budget_fingerprint=final_budget.fingerprint,
            final_robustness_fingerprint=final_robustness.fingerprint,
            total_notional=total_notional,
            allocations=allocations,
            sized_at=now,
        )

    def _validate_candidate_universe(
        self,
        dependence: DependenceEvidence,
        candidates: tuple[SizingCandidate, ...],
    ) -> dict[str, SizingCandidate]:
        if not candidates:
            raise PortfolioSizingBlocked("EMPTY_CANDIDATE_UNIVERSE", "no sizing candidates supplied")
        by_key: dict[str, SizingCandidate] = {}
        for candidate in candidates:
            if not isinstance(candidate, SizingCandidate):
                raise TypeError("candidates must contain SizingCandidate")
            if candidate.strategy_key in by_key:
                raise PortfolioSizingBlocked(
                    "DUPLICATE_CANDIDATE",
                    candidate.strategy_key,
                )
            strategy_id, separator, strategy_version = candidate.strategy_key.rpartition("@")
            if not separator or not strategy_id or not strategy_version:
                raise PortfolioSizingBlocked(
                    "INVALID_STRATEGY_KEY",
                    candidate.strategy_key,
                )
            if candidate.health_entity_id != strategy_id:
                raise PortfolioSizingBlocked(
                    "HEALTH_ENTITY_STRATEGY_MISMATCH",
                    f"{candidate.health_entity_id}!={strategy_id}",
                )
            by_key[candidate.strategy_key] = candidate
        expected = set(dependence.strategy_keys)
        actual = set(by_key)
        if actual != expected:
            raise PortfolioSizingBlocked(
                "CANDIDATE_UNIVERSE_MISMATCH",
                f"missing={sorted(expected-actual)} extra={sorted(actual-expected)}",
            )
        return by_key

    def _validate_quote(self, candidate: SizingCandidate, *, now: datetime) -> None:
        if candidate.quote_observed_at > now:
            raise PortfolioSizingBlocked("QUOTE_FROM_FUTURE", candidate.strategy_key)
        if now - candidate.quote_observed_at > timedelta(seconds=self._policy.max_quote_age_seconds):
            raise PortfolioSizingBlocked("STALE_QUOTE", candidate.strategy_key)

    @staticmethod
    def _require_two_positive(
        budget: AllocationBudgetEvidence,
        reason_code: str,
    ) -> None:
        positive = tuple(value for _, value in budget.strategy_weights if value > _ZERO)
        if len(positive) < 2:
            raise PortfolioSizingBlocked(
                reason_code,
                "at least two positive strategy allocations are required",
            )

    @staticmethod
    def _require_robust(
        *,
        dependence: DependenceEvidence,
        diversification_policy: DiversificationBudgetPolicy,
        weights: Mapping[str, Decimal],
        robustness_spec: AllocationRobustnessSpec,
        robustness_policy: AllocationRobustnessPolicy,
        reason_code: str,
    ):
        try:
            return require_robust_allocation(
                dependence,
                diversification_policy,
                weights,
                robustness_spec,
                robustness_policy,
            )
        except (FragileAllocation, AllocationRobustnessError) as exc:
            raise PortfolioSizingBlocked(reason_code, str(exc)) from exc

    @staticmethod
    def _zero_allocation(
        *,
        candidate: SizingCandidate,
        base_weight: Decimal,
        multiplier: Decimal,
        adjusted_weight: Decimal,
        disposition: AllocationDisposition,
        strategy_health_fp: str,
        portfolio_health_fp: str,
        rules_fingerprint: str = "",
    ) -> StrategySizingAllocation:
        return StrategySizingAllocation(
            strategy_key=candidate.strategy_key,
            health_entity_id=candidate.health_entity_id,
            instrument_key=candidate.instrument_key,
            base_weight=base_weight,
            health_multiplier=multiplier,
            health_adjusted_weight=adjusted_weight,
            final_weight=_ZERO,
            notional=_ZERO,
            quantity=_ZERO,
            reference_price=candidate.reference_price,
            disposition=disposition,
            quote_fingerprint=candidate.fingerprint if rules_fingerprint else "",
            instrument_rules_fingerprint=rules_fingerprint,
            health_strategy_state_fingerprint=strategy_health_fp,
            health_portfolio_state_fingerprint=portfolio_health_fp,
        )


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    if not _nonnegative_decimal(value) or not _positive_decimal(step):
        raise ValueError("floor-step inputs are invalid")
    units = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    result = units * step
    return result if result >= _ZERO else _ZERO


def _identity(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be non-empty canonical text")


def _hash_value(value: object, name: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase SHA-256 hex")


def _positive_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > _ZERO


def _nonnegative_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= _ZERO


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()
