from dataclasses import fields, replace
from datetime import timedelta

import pytest

import autotrade.promotion_fee_accounting as w82_module
from autotrade.promotion_fee_accounting import (
    SHADOW_FORWARD_BLOCKER,
    STRATEGY_VERSION_BLOCKER,
)
from autotrade.promotion_strategy_version_binding import (
    PROMOTION_STRATEGY_VERSION_RESOLUTION_VERSION,
    RUNTIME_CODE_IDENTITY_VERSION,
    PromotionStrategyVersionResolutionIntegrityError,
    resolve_promotion_strategy_version_binding,
    safe_dsl_runtime_code_hash,
)
from test_w83_execution_strategy_binding import _bind, _chain


def _runtime_chain(limits, market, empty_portfolio, market_buy_intent):
    chain = _chain(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        code_version=safe_dsl_runtime_code_hash(),
    )
    return chain, _bind(chain, market_buy_intent)


def _resolve(chain, evidence, intent, *, seconds=1):
    return resolve_promotion_strategy_version_binding(
        resolution_id="w83-strategy-version-resolution",
        binding_evidence=evidence,
        selected_trial=chain["trial"],
        w82_resolution=chain["w82"],
        execution_intent=intent,
        resolved_at=evidence.assessed_at + timedelta(seconds=seconds),
    )


def _rehash_w82(value, **changes):
    values = {
        field.name: getattr(value, field.name)
        for field in fields(value)
        if field.name != "resolution_hash"
    }
    values.update(changes)
    return w82_module.PromotionFeeAccountingResolution(
        **values,
        resolution_hash=w82_module._hash(w82_module._payload_from_values(values)),
    )


def test_w83_runtime_identity_is_reproducible_64_hex():
    first = safe_dsl_runtime_code_hash()
    second = safe_dsl_runtime_code_hash()
    assert first == second
    assert len(first) == 64
    assert set(first) <= set("0123456789abcdef")


def test_w83_resolution_removes_only_strategy_version_blocker(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, evidence = _runtime_chain(
        limits, market, empty_portfolio, market_buy_intent
    )
    result = _resolve(chain, evidence, market_buy_intent)

    assert result.contract_version == PROMOTION_STRATEGY_VERSION_RESOLUTION_VERSION
    assert result.runtime_identity_version == RUNTIME_CODE_IDENTITY_VERSION
    assert result.loaded_runtime_code_hash == safe_dsl_runtime_code_hash()
    assert result.trial_code_version == result.loaded_runtime_code_hash
    assert result.binding_evidence_hash == evidence.evidence_hash
    assert result.w82_resolution_hash == chain["w82"].resolution_hash
    assert result.strategy_spec_hash == chain["spec"].canonical_hash
    assert result.fee_product_economics_hash == chain["product"].evidence_hash
    assert result.resolved_promotion_blockers == (STRATEGY_VERSION_BLOCKER,)
    assert STRATEGY_VERSION_BLOCKER not in result.remaining_promotion_blockers
    assert SHADOW_FORWARD_BLOCKER in result.remaining_promotion_blockers
    assert result.strategy_version_execution_bound is True
    assert result.shadow_forward_promotion_bound is False
    assert result.paper_candidate_authorized is False
    assert result.external_execution_authorized is False
    assert result.runtime_execution_authorized is False
    assert result.capital_authority == "NONE"
    assert result.live_trading == "BLOCKED"
    assert result.to_dict()["resolution_hash"] == result.resolution_hash


def test_w83_resolution_hash_is_reproducible(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, evidence = _runtime_chain(
        limits, market, empty_portfolio, market_buy_intent
    )
    first = _resolve(chain, evidence, market_buy_intent)
    second = _resolve(chain, evidence, market_buy_intent)
    assert first == second
    assert first.resolution_hash == second.resolution_hash


def test_w83_rejects_preregistered_code_version_not_loaded_runtime(
    limits, market, empty_portfolio, market_buy_intent
):
    chain = _chain(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        code_version="a" * 64,
    )
    evidence = _bind(chain, market_buy_intent)
    with pytest.raises(
        PromotionStrategyVersionResolutionIntegrityError,
        match="code_version differs from loaded safe DSL runtime",
    ):
        _resolve(chain, evidence, market_buy_intent)


def test_w83_rejects_trial_drift_after_binding(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, evidence = _runtime_chain(
        limits, market, empty_portfolio, market_buy_intent
    )
    drifted = replace(chain["trial"], code_version="b" * 64)
    with pytest.raises(
        PromotionStrategyVersionResolutionIntegrityError,
        match="selected trial no longer matches frozen W83 binding evidence",
    ):
        resolve_promotion_strategy_version_binding(
            resolution_id="w83-trial-drift",
            binding_evidence=evidence,
            selected_trial=drifted,
            w82_resolution=chain["w82"],
            execution_intent=market_buy_intent,
            resolved_at=evidence.assessed_at + timedelta(seconds=1),
        )


def test_w83_rejects_intent_drift_after_w82_and_binding(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, evidence = _runtime_chain(
        limits, market, empty_portfolio, market_buy_intent
    )
    drifted = replace(
        market_buy_intent,
        idempotency_key="w83-drifted-idempotency",
    )
    with pytest.raises(
        PromotionStrategyVersionResolutionIntegrityError,
        match="does not match exact W82 candidate/intent",
    ):
        _resolve(chain, evidence, drifted)


def test_w83_rejects_w82_that_already_claims_strategy_bound(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, evidence = _runtime_chain(
        limits, market, empty_portfolio, market_buy_intent
    )
    tampered = _rehash_w82(
        chain["w82"],
        strategy_version_execution_bound=True,
    )
    with pytest.raises(
        PromotionStrategyVersionResolutionIntegrityError,
        match="W82 prerequisite is not exact",
    ):
        resolve_promotion_strategy_version_binding(
            resolution_id="w83-pre-resolved",
            binding_evidence=evidence,
            selected_trial=chain["trial"],
            w82_resolution=tampered,
            execution_intent=market_buy_intent,
            resolved_at=evidence.assessed_at + timedelta(seconds=1),
        )


def test_w83_rejects_temporal_regression(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, evidence = _runtime_chain(
        limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(
        PromotionStrategyVersionResolutionIntegrityError,
        match="temporal causality",
    ):
        resolve_promotion_strategy_version_binding(
            resolution_id="w83-time-regression",
            binding_evidence=evidence,
            selected_trial=chain["trial"],
            w82_resolution=chain["w82"],
            execution_intent=market_buy_intent,
            resolved_at=evidence.assessed_at - timedelta(microseconds=1),
        )


def test_w83_resolution_receipt_cannot_mint_authority(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, evidence = _runtime_chain(
        limits, market, empty_portfolio, market_buy_intent
    )
    result = _resolve(chain, evidence, market_buy_intent)
    with pytest.raises(
        PromotionStrategyVersionResolutionIntegrityError,
        match="may not grant PAPER",
    ):
        replace(result, paper_candidate_authorized=True)
    with pytest.raises(
        PromotionStrategyVersionResolutionIntegrityError,
        match="must resolve strategy-version and retain Shadow/Forward",
    ):
        replace(result, remaining_promotion_blockers=())
    with pytest.raises(
        PromotionStrategyVersionResolutionIntegrityError,
        match="may resolve only",
    ):
        replace(result, resolved_promotion_blockers=(SHADOW_FORWARD_BLOCKER,))
    with pytest.raises(
        PromotionStrategyVersionResolutionIntegrityError,
        match="resolution hash mismatch",
    ):
        replace(result, resolution_hash="0" * 64)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    (
        ("binding_evidence", object(), "binding_evidence"),
        ("selected_trial", object(), "selected_trial"),
        ("w82_resolution", object(), "w82_resolution"),
        ("execution_intent", object(), "execution_intent"),
    ),
)
def test_w83_resolver_rejects_wrong_input_types(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    field_name,
    value,
    message,
):
    chain, evidence = _runtime_chain(
        limits, market, empty_portfolio, market_buy_intent
    )
    kwargs = {
        "resolution_id": "w83-type-check",
        "binding_evidence": evidence,
        "selected_trial": chain["trial"],
        "w82_resolution": chain["w82"],
        "execution_intent": market_buy_intent,
        "resolved_at": evidence.assessed_at + timedelta(seconds=1),
    }
    kwargs[field_name] = value
    with pytest.raises(TypeError, match=message):
        resolve_promotion_strategy_version_binding(**kwargs)
