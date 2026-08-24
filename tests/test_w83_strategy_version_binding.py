from dataclasses import fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import autotrade.promotion_fee_accounting as w82_module
import autotrade.strategy_lab_promotion as promotion
from autotrade.domain import OrderIntent, OrderType, Side
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.strategy import StrategyContext
from autotrade.research.trials import TrialPhase, TrialRecord, TrialSpec, TrialStatus
from autotrade.strategy_version_binding import (
    BINDING_SCOPE,
    SHADOW_FORWARD_BLOCKER,
    STRATEGY_VERSION_BLOCKER,
    StrategyVersionBindingStatus,
    build_execution_strategy_version_binding,
    resolve_execution_strategy_version_binding,
    safe_dsl_runtime_code_version,
    strategy_spec_from_preregistered_trial,
)
from test_w82_promotion_fee_accounting import _candidate as _w82_candidate_base
from test_w82_promotion_fee_accounting import _resolve as _w82_resolve_base


def _dataset() -> MarketDataset:
    starts = [
        datetime(2026, 8, 10, hour, 0, tzinfo=timezone.utc)
        for hour in (18, 19, 20, 21)
    ]
    closes = (Decimal("10"), Decimal("10"), Decimal("9"), Decimal("12"))
    bars = tuple(
        Bar(
            symbol="TEST-USD",
            started_at=started_at,
            timeframe_seconds=3600,
            open=close,
            high=close + Decimal("0.5"),
            low=close - Decimal("0.5"),
            close=close,
            volume=Decimal("100"),
        )
        for started_at, close in zip(starts, closes, strict=True)
    )
    return MarketDataset(
        instrument=InstrumentMetadata(
            symbol="TEST-USD",
            venue="alpaca-paper-model",
            quote_currency="USD",
            price_tick=Decimal("0.01"),
            quantity_step=Decimal("0.000001"),
        ),
        bars=bars,
        source="w83-frozen-development-dataset",
    )


def _trial(dataset: MarketDataset, *, code_version: str | None = None, params=None) -> TrialRecord:
    parameters = params or {
        "dsl_kind": "moving_average_cross",
        "short_window": 2,
        "long_window": 3,
        "order_quantity": "10",
        "position_mode": "long_flat",
        "initial_stop_pct": "0.05",
    }
    spec = TrialSpec(
        trial_id="w83-development-winner",
        campaign_id="w83-development",
        hypothesis_id="w83-ma-cross",
        strategy_id="strategy-a",
        strategy_version="v1",
        dataset_hash=dataset.dataset_hash,
        split_name="development",
        phase=TrialPhase.DEVELOPMENT,
        parameters=parameters,
        code_version=code_version or safe_dsl_runtime_code_version(),
    )
    return TrialRecord(
        spec=spec,
        status=TrialStatus.COMPLETED,
        preregistered_at=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
        terminal_at=datetime(2026, 8, 10, 22, 5, tzinfo=timezone.utc),
        metrics={"net_return": "0.01"},
        p_value=Decimal("0.01"),
        failure_code="",
        result_hash="d" * 64,
    )


def _promotion_policy(trial: TrialRecord) -> promotion.StrategyPromotionPolicy:
    values = {
        "policy_id": "promotion-a",
        "threshold_policy_id": "w83-thresholds",
        "threshold_policy_hash": "b" * 64,
        "development_campaign_id": "w83-development",
        "holdout_campaign_id": "w83-holdout",
        "holdout_trial_id": "w83-holdout-trial",
        "selected_trial_id": trial.spec.trial_id,
        "selected_trial_fingerprint": trial.spec.fingerprint,
        "selected_strategy_id": trial.spec.strategy_id,
        "selected_strategy_version": trial.spec.strategy_version,
        "tournament_fingerprint": "c" * 64,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    return promotion.StrategyPromotionPolicy(
        **values,
        policy_hash=promotion._hash(promotion._policy_payload_from_values(values)),
    )


def _rehash_w82(w82, **changes):
    values = {
        field.name: getattr(w82, field.name)
        for field in fields(w82)
        if field.name != "resolution_hash"
    }
    values.update(changes)
    return w82_module.PromotionFeeAccountingResolution(
        **values,
        resolution_hash=w82_module._hash(w82_module._payload_from_values(values)),
    )


def _w83_candidate(limits, market, empty_portfolio, market_buy_intent, *, context_index=3):
    w81, fee, product, attestation = _w82_candidate_base(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    w82 = _w82_resolve_base(
        resolution_id="w82-before-w83",
        w81=w81,
        fee=fee,
        product=product,
        attestation=attestation,
        intent=market_buy_intent,
    )
    dataset = _dataset()
    trial = _trial(dataset)
    policy = _promotion_policy(trial)
    w82 = _rehash_w82(w82, promotion_policy_hash=policy.policy_hash)
    history = dataset.bars[: context_index + 1]
    context = StrategyContext(
        symbol=dataset.instrument.symbol,
        index=context_index,
        history=history,
        current_position_quantity=Decimal("0"),
        current_equity=Decimal("100000"),
    )
    evidence = build_execution_strategy_version_binding(
        evidence_id="w83-binding",
        promotion_policy=policy,
        selected_trial=trial,
        w82_resolution=w82,
        execution_intent=market_buy_intent,
        market_dataset=dataset,
        strategy_context=context,
        assessed_at=w82.resolved_at + timedelta(seconds=1),
    )
    return policy, trial, w82, dataset, context, evidence


def test_w83_reconstructs_exact_preregistered_strategy_artifact():
    dataset = _dataset()
    trial = _trial(dataset)

    first = strategy_spec_from_preregistered_trial(trial.spec)
    second = strategy_spec_from_preregistered_trial(trial.spec)

    assert first.canonical_hash == second.canonical_hash
    assert first.strategy_id == "strategy-a"
    assert first.strategy_version == "v1"
    assert first.parameters == {
        "short_window": 2,
        "long_window": 3,
        "order_quantity": "10",
        "position_mode": "long_flat",
    }
    assert first.initial_stop_pct == Decimal("0.05")
    assert trial.spec.code_version == safe_dsl_runtime_code_version()


def test_w83_pass_binds_frozen_candidate_artifact_signal_and_existing_intent(
    limits, market, empty_portfolio, market_buy_intent
):
    policy, trial, w82, dataset, context, evidence = _w83_candidate(
        limits, market, empty_portfolio, market_buy_intent
    )

    assert evidence.status is StrategyVersionBindingStatus.PASS
    assert evidence.binding_scope == BINDING_SCOPE
    assert evidence.promotion_policy_hash == policy.policy_hash
    assert evidence.selected_trial_fingerprint == trial.spec.fingerprint
    assert evidence.dataset_hash == dataset.dataset_hash
    assert evidence.context_hash
    assert evidence.signal_id is not None
    assert evidence.signal_hash is not None
    assert evidence.signal_generated_at == dataset.bars[-1].ended_at
    assert evidence.derived_side is Side.BUY
    assert evidence.derived_quantity == market_buy_intent.quantity
    assert evidence.strategy_version_execution_bound is True
    assert evidence.shadow_forward_promotion_bound is False
    assert evidence.paper_candidate_authorized is False
    assert evidence.external_execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"
    assert evidence.runtime_code_version == trial.spec.code_version
    assert context.history == dataset.bars
    assert w82.strategy_version_execution_bound is False


def test_w83_resolution_removes_only_strategy_version_blocker(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, w82, _, _, evidence = _w83_candidate(
        limits, market, empty_portfolio, market_buy_intent
    )
    result = resolve_execution_strategy_version_binding(
        resolution_id="w83-resolution",
        w82_resolution=w82,
        binding_evidence=evidence,
        execution_intent=market_buy_intent,
        resolved_at=evidence.assessed_at + timedelta(seconds=1),
    )

    assert result.status is StrategyVersionBindingStatus.PASS
    assert result.resolved_promotion_blockers == (STRATEGY_VERSION_BLOCKER,)
    assert STRATEGY_VERSION_BLOCKER not in result.remaining_promotion_blockers
    assert SHADOW_FORWARD_BLOCKER in result.remaining_promotion_blockers
    assert result.strategy_version_execution_bound is True
    assert result.shadow_forward_promotion_bound is False
    assert result.broker_authoritative_fee_proven is False
    assert result.realized_profitability_authorized is False
    assert result.paper_candidate_authorized is False
    assert result.external_execution_authorized is False
    assert result.capital_authority == "NONE"
    assert result.live_trading == "BLOCKED"


def test_w83_no_signal_stays_blocked_and_resolver_keeps_strategy_blocker(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, w82, _, _, evidence = _w83_candidate(
        limits,
        market,
        empty_portfolio,
        market_buy_intent,
        context_index=2,
    )
    assert evidence.status is StrategyVersionBindingStatus.BLOCKED
    assert evidence.reason_codes == ("NO_DETERMINISTIC_SIGNAL_FOR_EXECUTION_INTENT",)
    assert evidence.strategy_version_execution_bound is False

    result = resolve_execution_strategy_version_binding(
        resolution_id="w83-resolution-blocked",
        w82_resolution=w82,
        binding_evidence=evidence,
        execution_intent=market_buy_intent,
        resolved_at=evidence.assessed_at + timedelta(seconds=1),
    )
    assert result.status is StrategyVersionBindingStatus.BLOCKED
    assert result.resolved_promotion_blockers == ()
    assert STRATEGY_VERSION_BLOCKER in result.remaining_promotion_blockers
    assert SHADOW_FORWARD_BLOCKER in result.remaining_promotion_blockers
