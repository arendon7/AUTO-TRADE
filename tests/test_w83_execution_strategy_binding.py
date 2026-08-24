from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

import autotrade.strategy_lab_promotion as promotion
import autotrade.strategy_promotion_assessment as assessment_module
from autotrade.execution_cost_continuity import build_execution_cost_continuity_evidence
from autotrade.fee_accounting import (
    build_simulated_fee_accounting_contract,
    build_simulated_fee_accounting_evidence,
)
from autotrade.fee_product_economics import (
    FeeChargeConvention,
    FeeLiquidityRole,
    build_fee_product_economics_evidence,
    build_fee_product_policy,
)
from autotrade.fee_schedule_attestation import (
    build_alpaca_crypto_worst_case_fee_attestation,
)
from autotrade.paper_execution_lab import run_paper_execution_sensitivity
from autotrade.paper_execution_qualification import bind_research_costs_to_paper_execution
from autotrade.promotion_cost_continuity import resolve_promotion_cost_continuity
from autotrade.promotion_fee_accounting import resolve_promotion_fee_accounting
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.dsl import StrategySpec
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.trials import TrialPhase, TrialSpec
from autotrade.strategy_execution_binding import (
    ExecutionStrategyBindingIntegrityError,
    ExecutionStrategyBindingStatus,
    build_execution_strategy_binding_evidence,
)
from autotrade.strategy_promotion_assessment import ZERO_ASSESSMENT_HASH
from test_w82_promotion_fee_accounting import _matrix


def _strategy_spec(*, order_quantity=10, initial_stop_pct="0.05"):
    return StrategySpec(
        strategy_id="strategy-a",
        strategy_version="v1",
        kind="moving_average_cross",
        parameters={
            "short_window": 1,
            "long_window": 2,
            "order_quantity": order_quantity,
            "position_mode": "long_flat",
        },
        initial_stop_pct=Decimal(initial_stop_pct),
    )


def _dataset(now, *, closes=("10", "9", "11"), source="w83-dataset"):
    bars = []
    for offset, close_text in enumerate(closes, start=3):
        close = Decimal(close_text)
        bars.append(
            Bar(
                symbol="TEST-USD",
                started_at=now - timedelta(minutes=offset),
                timeframe_seconds=60,
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("100"),
            )
        )
    bars = tuple(sorted(bars, key=lambda item: item.started_at))
    return MarketDataset(
        instrument=InstrumentMetadata(
            symbol="TEST-USD",
            venue="alpaca-paper-model",
            quote_currency="USD",
            price_tick=Decimal("0.01"),
            quantity_step=Decimal("0.0001"),
        ),
        bars=bars,
        source=source,
    )


def _trial(spec, dataset, *, code_version="1" * 40):
    return TrialSpec(
        trial_id="trial-w83-selected",
        campaign_id="campaign-dev-w83",
        hypothesis_id="hypothesis-w83",
        strategy_id=spec.strategy_id,
        strategy_version=spec.strategy_version,
        dataset_hash=dataset.dataset_hash,
        split_name="development",
        phase=TrialPhase.DEVELOPMENT,
        parameters=dict(spec.build().parameters),
        code_version=code_version,
    )


def _policy(trial):
    values = {
        "policy_id": "promotion-w83",
        "threshold_policy_id": "threshold-w83",
        "threshold_policy_hash": "b" * 64,
        "development_campaign_id": trial.campaign_id,
        "holdout_campaign_id": "campaign-holdout-w83",
        "holdout_trial_id": "trial-holdout-w83",
        "selected_trial_id": trial.trial_id,
        "selected_trial_fingerprint": trial.fingerprint,
        "selected_strategy_id": trial.strategy_id,
        "selected_strategy_version": trial.strategy_version,
        "tournament_fingerprint": "c" * 64,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    return promotion.StrategyPromotionPolicy(
        **values,
        policy_hash=promotion._hash(promotion._policy_payload_from_values(values)),
    )


def _assessment(*, policy, measurement_hash):
    gates = []
    for index, gate_id in enumerate(promotion.REQUIRED_W79_GATE_IDS, start=1):
        hashes = (
            tuple(sorted((measurement_hash, "f" * 64)))
            if gate_id == "EXECUTION_SENSITIVITY"
            else (str(index) * 64,)
        )
        gates.append(
            promotion.PromotionGateEvidence(
                gate_id=gate_id,
                status=promotion.PromotionGateStatus.PASS,
                reason_codes=(),
                evidence_hashes=hashes,
            )
        )
    view_values = {
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "threshold_policy_hash": policy.threshold_policy_hash,
        "selected_strategy_id": policy.selected_strategy_id,
        "selected_strategy_version": policy.selected_strategy_version,
        "gates": tuple(gates),
        "evidence_complete": True,
        "assessment_state": promotion.PromotionAssessmentState.EVIDENCE_QUALIFIED,
        "promotion_blockers": tuple(sorted(promotion.PERMANENT_W79_PROMOTION_BLOCKERS)),
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    view = promotion.StrategyPromotionEvidenceView(
        **view_values,
        view_hash=promotion._hash(
            promotion._view_payload_from_values(view_values)
        ),
    )
    return assessment_module._build_receipt(
        assessment_id="assessment-w83",
        view=view,
        ordinal=1,
        previous_assessment_hash=ZERO_ASSESSMENT_HASH,
        assessed_at=policy_time(),
    )


def policy_time():
    from datetime import datetime, timezone

    return datetime(2026, 8, 24, 2, 0, tzinfo=timezone.utc)


def _chain(
    *,
    limits,
    market,
    empty_portfolio,
    intent,
    dataset=None,
    spec=None,
    code_version="1" * 40,
):
    spec = spec or _strategy_spec()
    dataset = dataset or _dataset(market.observed_at)
    trial = _trial(spec, dataset, code_version=code_version)
    policy = _policy(trial)

    cost = ExecutionCostModel(
        fee_bps=Decimal("25"),
        half_spread_bps=Decimal("2"),
        slippage_bps=Decimal("2"),
    )
    matrix = _matrix()
    qualification = bind_research_costs_to_paper_execution(
        cost_model=cost,
        matrix=matrix,
    )
    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    continuity = build_execution_cost_continuity_evidence(
        evidence_id="w83-continuity",
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        intent=intent,
        market=market,
        assessed_at=market.observed_at,
    )
    assessment = _assessment(
        policy=policy,
        measurement_hash=report.measurement_report_hash,
    )
    w81 = resolve_promotion_cost_continuity(
        resolution_id="w81-before-w83",
        assessment=assessment,
        continuity=continuity,
        execution_intent=intent,
        resolved_at=assessment.assessed_at + timedelta(seconds=1),
    )
    contract = build_simulated_fee_accounting_contract(
        contract_id="w83-fee-contract",
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        product_id="test-product",
        asset_class="crypto",
        venue=dataset.instrument.venue,
        settlement_currency=dataset.instrument.quote_currency,
        created_at=market.observed_at,
    )
    fee = build_simulated_fee_accounting_evidence(
        evidence_id="w83-fee-evidence",
        contract=contract,
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        continuity=continuity,
        intent=intent,
        market=market,
        assessed_at=w81.resolved_at + timedelta(seconds=1),
    )
    product_policy = build_fee_product_policy(
        policy_id="w83-product-policy",
        product_id="test-product",
        asset_class="crypto",
        venue=dataset.instrument.venue,
        symbol=intent.symbol,
        base_currency="TEST",
        quote_currency=dataset.instrument.quote_currency,
        charge_convention=FeeChargeConvention.RECEIVED_ASSET_PERCENT,
        liquidity_role=FeeLiquidityRole.WORST_CASE,
        minimum_fee_bps=Decimal("25"),
        source_reference="w83-product-policy",
        effective_at=market.observed_at - timedelta(seconds=1),
    )
    product = build_fee_product_economics_evidence(
        evidence_id="w83-product-economics",
        policy=product_policy,
        fee_evidence=fee,
        cost_model=cost,
        execution_intent=intent,
        assessed_at=fee.assessed_at + timedelta(seconds=1),
    )
    attestation = build_alpaca_crypto_worst_case_fee_attestation(
        attestation_id="w83-alpaca-schedule",
        product_id=product.product_id,
        venue=product.venue,
        symbol=product.symbol,
    )
    w82 = resolve_promotion_fee_accounting(
        resolution_id="w82-before-w83",
        w81_resolution=w81,
        fee_evidence=fee,
        product_economics=product,
        fee_schedule_attestation=attestation,
        execution_intent=intent,
        resolved_at=product.assessed_at + timedelta(seconds=1),
    )
    return {
        "spec": spec,
        "dataset": dataset,
        "trial": trial,
        "policy": policy,
        "product": product,
        "w82": w82,
    }


def _bind(chain, intent):
    return build_execution_strategy_binding_evidence(
        binding_id="w83-binding",
        promotion_policy=chain["policy"],
        selected_trial=chain["trial"],
        strategy_spec=chain["spec"],
        dataset=chain["dataset"],
        fee_product_economics=chain["product"],
        w82_resolution=chain["w82"],
        execution_intent=intent,
        context_index=len(chain["dataset"].bars) - 1,
        current_position_quantity=Decimal("0"),
        current_equity=Decimal("100000"),
        assessed_at=chain["w82"].resolved_at + timedelta(seconds=1),
    )


def test_w83_binds_frozen_trial_spec_dataset_and_existing_intent(
    limits, market, empty_portfolio, market_buy_intent
):
    chain = _chain(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    evidence = _bind(chain, market_buy_intent)
    assert evidence.status is ExecutionStrategyBindingStatus.PASS
    assert evidence.selected_trial_fingerprint == chain["trial"].fingerprint
    assert evidence.strategy_spec_hash == chain["spec"].canonical_hash
    assert evidence.dataset_hash == chain["dataset"].dataset_hash
    assert evidence.w82_resolution_hash == chain["w82"].resolution_hash
    assert evidence.derived_side.value == "BUY"
    assert evidence.derived_quantity == Decimal("10")
    assert evidence.strategy_version_binding_proven is True
    assert evidence.shadow_forward_promotion_bound is False
    assert evidence.paper_candidate_authorized is False
    assert evidence.external_execution_authorized is False
    assert evidence.runtime_execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"


def test_w83_binding_hash_is_reproducible(
    limits, market, empty_portfolio, market_buy_intent
):
    chain = _chain(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    first = _bind(chain, market_buy_intent)
    second = _bind(chain, market_buy_intent)
    assert first.evidence_hash == second.evidence_hash
    assert first.runtime_fingerprint == second.runtime_fingerprint


def test_w83_rejects_same_id_version_with_different_strategy_artifact(
    limits, market, empty_portfolio, market_buy_intent
):
    chain = _chain(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    drifted = _strategy_spec(initial_stop_pct="0.06")
    with pytest.raises(
        ExecutionStrategyBindingIntegrityError,
        match="exactly freeze StrategySpec runtime parameters",
    ):
        build_execution_strategy_binding_evidence(
            binding_id="w83-spec-drift",
            promotion_policy=chain["policy"],
            selected_trial=chain["trial"],
            strategy_spec=drifted,
            dataset=chain["dataset"],
            fee_product_economics=chain["product"],
            w82_resolution=chain["w82"],
            execution_intent=market_buy_intent,
            context_index=2,
            current_position_quantity=Decimal("0"),
            current_equity=Decimal("100000"),
            assessed_at=chain["w82"].resolved_at + timedelta(seconds=1),
        )


def test_w83_rejects_non_immutable_trial_code_version(
    limits, market, empty_portfolio, market_buy_intent
):
    chain = _chain(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        code_version="code-v1",
    )
    with pytest.raises(
        ExecutionStrategyBindingIntegrityError,
        match="code_version is not immutable",
    ):
        _bind(chain, market_buy_intent)


def test_w83_rejects_dataset_drift_after_candidate_freeze(
    limits, market, empty_portfolio, market_buy_intent
):
    chain = _chain(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    drifted_dataset = replace(chain["dataset"], source="different-source")
    drifted_chain = dict(chain)
    drifted_chain["dataset"] = drifted_dataset
    with pytest.raises(
        ExecutionStrategyBindingIntegrityError,
        match="dataset hash mismatch",
    ):
        _bind(drifted_chain, market_buy_intent)


def test_w83_rejects_w82_qualified_intent_with_wrong_strategy_quantity(
    limits, market, empty_portfolio, market_buy_intent
):
    wrong_intent = replace(market_buy_intent, quantity=Decimal("9"))
    chain = _chain(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=wrong_intent,
    )
    with pytest.raises(
        ExecutionStrategyBindingIntegrityError,
        match="quantity differs from deterministic signal delta",
    ):
        _bind(chain, wrong_intent)


def test_w83_rejects_context_without_deterministic_signal(
    limits, market, empty_portfolio, market_buy_intent
):
    flat_dataset = _dataset(
        market.observed_at,
        closes=("10", "10", "10"),
        source="w83-no-signal",
    )
    chain = _chain(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        dataset=flat_dataset,
    )
    with pytest.raises(
        ExecutionStrategyBindingIntegrityError,
        match="does not emit a signal",
    ):
        _bind(chain, market_buy_intent)


def test_w83_receipt_cannot_mint_authority(
    limits, market, empty_portfolio, market_buy_intent
):
    chain = _chain(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    evidence = _bind(chain, market_buy_intent)
    with pytest.raises(ExecutionStrategyBindingIntegrityError, match="PAPER candidate"):
        replace(evidence, paper_candidate_authorized=True)
    with pytest.raises(ExecutionStrategyBindingIntegrityError, match="capital or LIVE"):
        replace(evidence, live_trading="ENABLED")
    with pytest.raises(ExecutionStrategyBindingIntegrityError, match="requires strategy_version_binding_proven"):
        replace(evidence, strategy_version_binding_proven=False)
