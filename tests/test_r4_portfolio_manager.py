from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from autotrade.health_bridge import (
    EffectiveHealthControl,
    HealthBridgeError,
    HealthRiskMode,
)
from autotrade.instrument_master import (
    AuthoritativeInstrumentRules,
    InstrumentTradingStatus,
    SQLiteInstrumentMaster,
)
from autotrade.persistence import SQLiteRuntime
from autotrade.portfolio_manager import (
    AllocationDisposition,
    DeterministicPortfolioManager,
    PortfolioSizingBlocked,
    PortfolioSizingPolicy,
    SizingCandidate,
)
from autotrade.research.allocation_robustness import (
    AllocationRobustnessPolicy,
    AllocationRobustnessSpec,
)
from autotrade.research.portfolio_dependence import (
    CalibrationPhase,
    DependenceSpec,
    DiversificationBudgetPolicy,
    ReturnObservation,
    StrategyReturnSeries,
    build_dependence_evidence,
)


D = Decimal


def _sha(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _series(now, strategy: str, values: tuple[str, ...]):
    return StrategyReturnSeries(
        strategy_id=strategy,
        strategy_version="1",
        phase=CalibrationPhase.TRAIN,
        source_hash=_sha(f"returns:{strategy}"),
        observations=tuple(
            ReturnObservation(
                occurred_at=now + timedelta(minutes=index),
                value=D(value),
            )
            for index, value in enumerate(values)
        ),
    )


def _dependence(now):
    return build_dependence_evidence(
        (
            _series(now, "alpha", ("0.010", "0.020", "0.015", "0.025", "0.018", "0.022")),
            _series(now, "beta", ("0.012", "0.018", "0.017", "0.023", "0.019", "0.021")),
            _series(now, "gamma", ("0.009", "0.021", "0.014", "0.026", "0.017", "0.023")),
        ),
        DependenceSpec(
            phase=CalibrationPhase.TRAIN,
            min_common_observations=6,
            cluster_abs_correlation=D("0.95"),
        ),
    )


def _diversification_policy():
    return DiversificationBudgetPolicy(
        max_strategy_weight=D("0.40"),
        max_cluster_weight=D("0.90"),
        max_total_weight=D("0.90"),
    )


def _weights():
    return {
        "alpha@1": D("0.30"),
        "beta@1": D("0.30"),
        "gamma@1": D("0.30"),
    }


def _robustness_spec():
    return AllocationRobustnessSpec(perturbation_weight=D("0.05"))


def _robustness_policy():
    return AllocationRobustnessPolicy(
        max_mean_degradation_fraction=D("1"),
        max_volatility_increase_fraction=D("1"),
    )


class HealthStub:
    def __init__(self, multipliers=None, blocked=None, fail_for=None):
        self.multipliers = multipliers or {}
        self.blocked = set(blocked or ())
        self.fail_for = set(fail_for or ())

    def effective_control(self, *, strategy_id, portfolio_entity_id, now):
        if strategy_id in self.fail_for:
            raise HealthBridgeError(f"health unavailable for {strategy_id}")
        if strategy_id in self.blocked:
            return EffectiveHealthControl(
                mode=HealthRiskMode.NO_NEW_RISK,
                order_multiplier=D("0"),
                strategy_multiplier=D("0"),
                portfolio_multiplier=D("1"),
                reason="TEST_NO_NEW_RISK",
                strategy_state_fingerprint=_sha(f"health:{strategy_id}:blocked"),
                portfolio_state_fingerprint=(
                    _sha(f"health:{portfolio_entity_id}:normal") if portfolio_entity_id else ""
                ),
            )
        multiplier = D(str(self.multipliers.get(strategy_id, "1")))
        mode = HealthRiskMode.NORMAL if multiplier == D("1") else HealthRiskMode.REDUCED
        return EffectiveHealthControl(
            mode=mode,
            order_multiplier=multiplier,
            strategy_multiplier=multiplier,
            portfolio_multiplier=D("1"),
            reason=f"TEST_{mode.value}",
            strategy_state_fingerprint=_sha(f"health:{strategy_id}:{multiplier}"),
            portfolio_state_fingerprint=(
                _sha(f"health:{portfolio_entity_id}:normal") if portfolio_entity_id else ""
            ),
        )


def _rules(
    now,
    symbol,
    *,
    status=InstrumentTradingStatus.TRADING,
    observed_at=None,
    price_tick=D("0.01"),
    quantity_step=D("0.1"),
    min_quantity=D("0.1"),
    max_quantity=None,
    min_notional=D("10"),
    max_notional=None,
):
    return AuthoritativeInstrumentRules(
        venue="TESTX",
        symbol=symbol,
        base_currency=symbol.split("-")[0],
        quote_currency="USD",
        version=1,
        price_tick=price_tick,
        quantity_step=quantity_step,
        min_quantity=min_quantity,
        max_quantity=max_quantity,
        min_notional=min_notional,
        max_notional=max_notional,
        trading_status=status,
        source="test-venue-rules",
        source_version="v1",
        source_payload_sha256=_sha(f"rules:{symbol}:{status.value}:{max_notional}:{min_notional}"),
        observed_at=observed_at or now,
        valid_until=(observed_at or now) + timedelta(hours=2),
    )


def _master(tmp_path, now, rule_overrides=None):
    runtime = SQLiteRuntime(tmp_path / "instrument-master.db")
    master = SQLiteInstrumentMaster(runtime)
    overrides = rule_overrides or {}
    for strategy in ("alpha", "beta", "gamma"):
        symbol = f"{strategy.upper()}-USD"
        rules = _rules(now, symbol, **overrides.get(strategy, {}))
        master.publish(rules, now=now)
    return master


def _candidates(now, *, prices=None, observed_at=None):
    prices = prices or {}
    quote_time = observed_at or now
    return tuple(
        SizingCandidate(
            strategy_key=f"{strategy}@1",
            health_entity_id=strategy,
            venue="TESTX",
            symbol=f"{strategy.upper()}-USD",
            reference_price=D(str(prices.get(strategy, "100.00"))),
            quote_observed_at=quote_time,
            quote_source_sha256=_sha(f"quote:{strategy}:{prices.get(strategy, '100.00')}:{quote_time.isoformat()}"),
        )
        for strategy in ("alpha", "beta", "gamma")
    )


def _manager(tmp_path, now, *, health=None, rule_overrides=None, policy=None):
    return DeterministicPortfolioManager(
        instrument_rules=_master(tmp_path, now, rule_overrides),
        health_controls=health or HealthStub(),
        policy=policy,
    )


def _size(manager, now, **overrides):
    params = dict(
        equity=D("100000"),
        dependence=_dependence(now),
        diversification_policy=_diversification_policy(),
        strategy_weights=_weights(),
        robustness_spec=_robustness_spec(),
        robustness_policy=_robustness_policy(),
        candidates=_candidates(now),
        portfolio_health_entity_id="portfolio-main",
        now=now,
    )
    params.update(overrides)
    return manager.size(**params)


def test_policy_and_candidate_contracts_are_strict(now):
    assert len(PortfolioSizingPolicy().fingerprint) == 64
    with pytest.raises(ValueError, match="max_quote_age_seconds"):
        PortfolioSizingPolicy(max_quote_age_seconds=0)
    with pytest.raises(ValueError, match="versioned"):
        replace(_candidates(now)[0], strategy_key="alpha")
    with pytest.raises(ValueError, match="reference_price"):
        replace(_candidates(now)[0], reference_price=D("NaN"))


def test_normal_sizing_is_deterministic_bounded_and_advisory(tmp_path, now):
    manager = _manager(tmp_path, now)
    first = _size(manager, now)
    second = _size(manager, now, candidates=tuple(reversed(_candidates(now))))

    assert first.fingerprint == second.fingerprint
    assert first.total_notional == D("90000.000")
    assert [item.strategy_key for item in first.allocations] == ["alpha@1", "beta@1", "gamma@1"]
    assert all(item.disposition is AllocationDisposition.SIZED for item in first.allocations)
    assert all(item.final_weight == D("0.30") for item in first.allocations)
    assert all(item.quantity == D("300.0") for item in first.allocations)
    assert first.to_payload()["fingerprint"] == first.fingerprint


def test_health_reduction_can_only_shrink_candidate_weight(tmp_path, now):
    manager = _manager(tmp_path, now, health=HealthStub(multipliers={"alpha": "0.5"}))
    decision = _size(manager, now)
    alpha = next(item for item in decision.allocations if item.strategy_key == "alpha@1")
    assert alpha.base_weight == D("0.30")
    assert alpha.health_multiplier == D("0.5")
    assert alpha.health_adjusted_weight == D("0.150")
    assert alpha.final_weight == D("0.15")
    assert decision.total_notional == D("75000.000")


def test_one_health_blocked_strategy_can_size_only_if_remaining_portfolio_is_robust(tmp_path, now):
    manager = _manager(tmp_path, now, health=HealthStub(blocked={"alpha"}))
    decision = _size(manager, now)
    alpha = next(item for item in decision.allocations if item.strategy_key == "alpha@1")
    assert alpha.disposition is AllocationDisposition.ZERO_WEIGHT
    assert alpha.quantity == 0
    assert alpha.notional == 0
    assert decision.total_notional == D("60000.000")


def test_two_health_blocked_strategies_fail_closed_on_diversification(tmp_path, now):
    manager = _manager(tmp_path, now, health=HealthStub(blocked={"alpha", "beta"}))
    with pytest.raises(PortfolioSizingBlocked) as exc:
        _size(manager, now)
    assert exc.value.reason_code == "INSUFFICIENT_DIVERSIFICATION_AFTER_HEALTH"


def test_health_provider_failure_blocks_sizing(tmp_path, now):
    manager = _manager(tmp_path, now, health=HealthStub(fail_for={"beta"}))
    with pytest.raises(PortfolioSizingBlocked) as exc:
        _size(manager, now)
    assert exc.value.reason_code == "HEALTH_CONTROL_UNAVAILABLE"


def test_candidate_universe_must_exactly_match_dependence_universe(tmp_path, now):
    manager = _manager(tmp_path, now)
    with pytest.raises(PortfolioSizingBlocked) as exc:
        _size(manager, now, candidates=_candidates(now)[:2])
    assert exc.value.reason_code == "CANDIDATE_UNIVERSE_MISMATCH"


def test_quote_from_future_or_stale_blocks_before_sizing(tmp_path, now):
    manager = _manager(tmp_path, now, policy=PortfolioSizingPolicy(max_quote_age_seconds=5))
    with pytest.raises(PortfolioSizingBlocked) as future:
        _size(manager, now, candidates=_candidates(now, observed_at=now + timedelta(milliseconds=1)))
    assert future.value.reason_code == "QUOTE_FROM_FUTURE"

    with pytest.raises(PortfolioSizingBlocked) as stale:
        _size(manager, now, candidates=_candidates(now, observed_at=now - timedelta(seconds=6)))
    assert stale.value.reason_code == "STALE_QUOTE"


def test_missing_or_halted_instrument_rules_fail_closed(tmp_path, now):
    runtime = SQLiteRuntime(tmp_path / "empty-master.db")
    empty_master = SQLiteInstrumentMaster(runtime)
    manager = DeterministicPortfolioManager(
        instrument_rules=empty_master,
        health_controls=HealthStub(),
    )
    with pytest.raises(PortfolioSizingBlocked) as missing:
        _size(manager, now)
    assert missing.value.reason_code == "INSTRUMENT_RULES_UNAVAILABLE"

    halted = _manager(
        tmp_path / "halted",
        now,
        rule_overrides={"beta": {"status": InstrumentTradingStatus.HALTED}},
    )
    with pytest.raises(PortfolioSizingBlocked) as blocked:
        _size(halted, now)
    assert blocked.value.reason_code == "INSTRUMENT_RULES_UNAVAILABLE"


def test_stale_authoritative_instrument_rules_fail_closed(tmp_path, now):
    manager = _manager(
        tmp_path,
        now,
        rule_overrides={
            "alpha": {"observed_at": now - timedelta(hours=2)},
            "beta": {"observed_at": now - timedelta(hours=2)},
            "gamma": {"observed_at": now - timedelta(hours=2)},
        },
        policy=PortfolioSizingPolicy(max_instrument_rule_age_seconds=3600),
    )
    with pytest.raises(PortfolioSizingBlocked) as exc:
        _size(manager, now)
    assert exc.value.reason_code == "INSTRUMENT_RULES_UNAVAILABLE"


def test_quote_must_align_to_authoritative_price_tick(tmp_path, now):
    manager = _manager(tmp_path, now)
    with pytest.raises(PortfolioSizingBlocked) as exc:
        _size(manager, now, candidates=_candidates(now, prices={"alpha": "100.001"}))
    assert exc.value.reason_code == "QUOTE_NOT_TICK_ALIGNED"


def test_quantity_rounding_is_always_downward(tmp_path, now):
    manager = _manager(tmp_path, now)
    decision = _size(
        manager,
        now,
        candidates=_candidates(now, prices={"alpha": "101.00", "beta": "103.00", "gamma": "107.00"}),
    )
    for item in decision.allocations:
        assert item.notional <= D("30000")
        assert item.final_weight <= item.health_adjusted_weight
        assert item.quantity % D("0.1") == 0


def test_venue_max_notional_only_reduces_and_final_allocation_is_revalidated(tmp_path, now):
    manager = _manager(
        tmp_path,
        now,
        rule_overrides={"alpha": {"max_notional": D("10000")}},
    )
    decision = _size(manager, now)
    alpha = next(item for item in decision.allocations if item.strategy_key == "alpha@1")
    assert alpha.notional == D("10000.00")
    assert alpha.final_weight == D("0.10")
    assert decision.total_notional == D("70000.000")


def test_venue_minimum_never_causes_upsizing(tmp_path, now):
    manager = _manager(
        tmp_path,
        now,
        rule_overrides={"alpha": {"min_notional": D("40000")}},
    )
    decision = _size(manager, now)
    alpha = next(item for item in decision.allocations if item.strategy_key == "alpha@1")
    assert alpha.disposition is AllocationDisposition.BELOW_VENUE_MINIMUM
    assert alpha.quantity == D("0")
    assert alpha.notional == D("0")
    assert decision.total_notional == D("60000.000")


def test_venue_minimum_can_make_whole_portfolio_fail_closed(tmp_path, now):
    manager = _manager(
        tmp_path,
        now,
        rule_overrides={
            "alpha": {"min_notional": D("40000")},
            "beta": {"min_notional": D("40000")},
        },
    )
    with pytest.raises(PortfolioSizingBlocked) as exc:
        _size(manager, now)
    assert exc.value.reason_code == "INSUFFICIENT_DIVERSIFICATION_AFTER_VENUE_RULES"


def test_invalid_equity_fails_closed(tmp_path, now):
    manager = _manager(tmp_path, now)
    for equity in (D("0"), D("-1"), D("NaN")):
        with pytest.raises(PortfolioSizingBlocked) as exc:
            _size(manager, now, equity=equity)
        assert exc.value.reason_code == "INVALID_EQUITY"


def test_input_mutation_changes_decision_fingerprint(tmp_path, now):
    manager = _manager(tmp_path, now)
    baseline = _size(manager, now)
    changed = _size(
        manager,
        now,
        strategy_weights={"alpha@1": D("0.29"), "beta@1": D("0.30"), "gamma@1": D("0.30")},
    )
    assert changed.fingerprint != baseline.fingerprint


def test_sizing_api_exposes_capacity_not_order_semantics(tmp_path, now):
    decision = _size(_manager(tmp_path, now), now)
    payload = decision.to_payload()
    serialized = str(payload).lower()
    assert "side" not in serialized
    assert "order_type" not in serialized
    assert "broker" not in serialized
