from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from autotrade.shadow_forward_candidate_provenance import (
    ShadowForwardCandidateProvenanceIntegrityError,
    build_candidate_forward_policy,
    build_candidate_isolated_shadow_config,
    build_candidate_shadow_forward_identity,
    build_candidate_shadow_observation,
)
from autotrade.research.forward import SQLiteForwardEvidenceRegistry
from autotrade.research.shadow import (
    SQLitePortfolioShadowRegistry,
    StrategyShadowObservation,
)
from test_w83_promotion_strategy_version_resolution import _resolve, _runtime_chain


def _w83(limits, market, empty_portfolio, market_buy_intent):
    chain, binding = _runtime_chain(
        limits,
        market,
        empty_portfolio,
        market_buy_intent,
    )
    resolution = _resolve(chain, binding, market_buy_intent)
    identity = build_candidate_shadow_forward_identity(
        w83_resolution=resolution,
        binding_evidence=binding,
    )
    return chain, binding, resolution, identity


def _config(identity, resolution):
    return build_candidate_isolated_shadow_config(
        identity=identity,
        config_id="w84-candidate-shadow",
        activated_at=resolution.resolved_at + timedelta(seconds=1),
        initial_nav=Decimal("100000"),
        w83_resolved_at=resolution.resolved_at,
    )


def test_w84_candidate_identity_is_exact_and_reproducible(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution, first = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    second = build_candidate_shadow_forward_identity(
        w83_resolution=resolution,
        binding_evidence=binding,
    )

    assert first == second
    assert first.w83_resolution_hash == resolution.resolution_hash
    assert first.w83_binding_evidence_hash == binding.evidence_hash
    assert first.selected_trial_fingerprint == chain["trial"].fingerprint
    assert first.strategy_spec_hash == chain["spec"].canonical_hash
    assert first.runtime_code_hash == resolution.loaded_runtime_code_hash
    assert first.trial_dataset_hash == binding.trial_dataset_hash
    assert first.intent_fingerprint == resolution.intent_fingerprint
    assert first.to_dict()["identity_hash"] == first.identity_hash


def test_w84_candidate_identity_tamper_is_rejected(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, _, identity = _w83(limits, market, empty_portfolio, market_buy_intent)
    with pytest.raises(
        ShadowForwardCandidateProvenanceIntegrityError,
        match="candidate identity hash mismatch",
    ):
        replace(identity, strategy_spec_hash="0" * 64)


def test_w84_shadow_config_is_candidate_isolated_and_hash_bound(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, resolution, identity = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    config, binding = _config(identity, resolution)

    assert config.strategy_weights == {identity.selected_strategy_id: Decimal("1")}
    assert binding.selected_strategy_weight == Decimal("1")
    assert binding.source_config_hash == config.source_config_hash
    assert binding.shadow_config_fingerprint == config.fingerprint
    assert binding.candidate_identity_hash == identity.identity_hash
    assert config.source_config_hash != sha256(b"cfg").hexdigest()


def test_w84_shadow_config_cannot_activate_before_w83(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, resolution, identity = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(
        ShadowForwardCandidateProvenanceIntegrityError,
        match="may not activate before W83 resolution",
    ):
        build_candidate_isolated_shadow_config(
            identity=identity,
            config_id="w84-too-early",
            activated_at=resolution.resolved_at - timedelta(microseconds=1),
            initial_nav=Decimal("100000"),
            w83_resolved_at=resolution.resolved_at,
        )


def test_w84_observation_binds_external_measurement_without_claiming_verification(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, resolution, identity = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    config, config_binding = _config(identity, resolution)
    start = config.activated_at + timedelta(seconds=1)
    measurement_hash = sha256(b"independent-forward-measurement").hexdigest()

    observation, binding = build_candidate_shadow_observation(
        identity=identity,
        config_binding=config_binding,
        period_started_at=start,
        period_ended_at=start + timedelta(minutes=1),
        return_fraction=Decimal("0.01"),
        measurement_contract="W84_TEST_EXTERNAL_MEASUREMENT_V1",
        measurement_hash=measurement_hash,
    )

    assert observation.strategy_id == identity.selected_strategy_id
    assert observation.source_fingerprint == binding.source_fingerprint
    assert binding.measurement_hash == measurement_hash
    assert binding.measurement_verified_by_w84 is False
    assert binding.to_dict()["binding_hash"] == binding.binding_hash


def test_w84_measurement_change_changes_shadow_source_fingerprint(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, resolution, identity = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    config, config_binding = _config(identity, resolution)
    start = config.activated_at + timedelta(seconds=1)
    kwargs = dict(
        identity=identity,
        config_binding=config_binding,
        period_started_at=start,
        period_ended_at=start + timedelta(minutes=1),
        return_fraction=Decimal("0.01"),
        measurement_contract="W84_TEST_EXTERNAL_MEASUREMENT_V1",
    )
    first, _ = build_candidate_shadow_observation(
        **kwargs,
        measurement_hash=sha256(b"measurement-a").hexdigest(),
    )
    second, _ = build_candidate_shadow_observation(
        **kwargs,
        measurement_hash=sha256(b"measurement-b").hexdigest(),
    )
    assert first.source_fingerprint != second.source_fingerprint


def test_w84_legacy_opaque_observation_does_not_match_candidate_provenance(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, resolution, identity = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    config, config_binding = _config(identity, resolution)
    start = config.activated_at + timedelta(seconds=1)
    canonical, _ = build_candidate_shadow_observation(
        identity=identity,
        config_binding=config_binding,
        period_started_at=start,
        period_ended_at=start + timedelta(minutes=1),
        return_fraction=Decimal("0.01"),
        measurement_contract="W84_TEST_EXTERNAL_MEASUREMENT_V1",
        measurement_hash=sha256(b"measurement").hexdigest(),
    )
    legacy = StrategyShadowObservation(
        strategy_id=identity.selected_strategy_id,
        period_started_at=start,
        period_ended_at=start + timedelta(minutes=1),
        return_fraction=Decimal("0.01"),
        source_fingerprint=sha256(
            f"{identity.selected_strategy_id}:{start.isoformat()}:0.01".encode()
        ).hexdigest(),
    )
    assert legacy.source_fingerprint != canonical.source_fingerprint


def test_w84_forward_policy_freezes_candidate_identity_and_runtime_code(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, resolution, identity = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    config, config_binding = _config(identity, resolution)
    frozen_at = config.activated_at
    activated_at = frozen_at + timedelta(seconds=1)

    policy, binding = build_candidate_forward_policy(
        identity=identity,
        config_binding=config_binding,
        campaign_id="w84-forward-candidate",
        frozen_at=frozen_at,
        activated_at=activated_at,
        w83_resolved_at=resolution.resolved_at,
    )

    assert policy.shadow_config_fingerprint == config.fingerprint
    assert policy.source_code_hash == identity.runtime_code_hash
    assert policy.frozen_parameters_hash == binding.frozen_parameters_hash
    assert binding.forward_policy_fingerprint == policy.fingerprint
    assert binding.performance_qualification_deferred is True
    assert binding.paper_candidate_authorized is False
    assert binding.external_execution_authorized is False
    assert binding.capital_authority == "NONE"
    assert binding.live_trading == "BLOCKED"


def test_w84_forward_policy_rejects_temporal_regression(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, resolution, identity = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    config, config_binding = _config(identity, resolution)

    with pytest.raises(
        ShadowForwardCandidateProvenanceIntegrityError,
        match="may not freeze before W83 resolution",
    ):
        build_candidate_forward_policy(
            identity=identity,
            config_binding=config_binding,
            campaign_id="w84-forward-early-freeze",
            frozen_at=resolution.resolved_at - timedelta(microseconds=1),
            activated_at=config.activated_at + timedelta(seconds=1),
            w83_resolved_at=resolution.resolved_at,
        )

    with pytest.raises(
        ShadowForwardCandidateProvenanceIntegrityError,
        match="may not activate before shadow config",
    ):
        build_candidate_forward_policy(
            identity=identity,
            config_binding=config_binding,
            campaign_id="w84-forward-early-activation",
            frozen_at=resolution.resolved_at,
            activated_at=config.activated_at - timedelta(microseconds=1),
            w83_resolved_at=resolution.resolved_at,
        )


def test_w84_provenance_is_compatible_with_r5_shadow_forward_chain(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, _, resolution, identity = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    config, config_binding = _config(identity, resolution)
    forward_activation = config.activated_at + timedelta(seconds=1)
    policy, policy_binding = build_candidate_forward_policy(
        identity=identity,
        config_binding=config_binding,
        campaign_id="w84-forward-chain",
        frozen_at=config.activated_at,
        activated_at=forward_activation,
        w83_resolved_at=resolution.resolved_at,
    )
    observation, observation_binding = build_candidate_shadow_observation(
        identity=identity,
        config_binding=config_binding,
        period_started_at=forward_activation,
        period_ended_at=forward_activation + timedelta(minutes=1),
        return_fraction=Decimal("0.01"),
        measurement_contract="W84_TEST_EXTERNAL_MEASUREMENT_V1",
        measurement_hash=sha256(b"forward-measurement").hexdigest(),
    )

    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(config)
    shadow_record = shadow.append_period((observation,))
    assert shadow_record.weighted_return == observation.return_fraction
    assert shadow_record.observation_fingerprints[identity.selected_strategy_id] != (
        observation_binding.source_fingerprint
    )
    # R5 observation_fingerprints hash the canonical observation payload; the
    # embedded source_fingerprint remains the W84 provenance anchor.
    assert observation.source_fingerprint == observation_binding.source_fingerprint

    forward = SQLiteForwardEvidenceRegistry(tmp_path / "forward.sqlite")
    forward.register_policy(policy)
    evidence = forward.append_shadow_record(
        shadow_registry=shadow,
        shadow_record_hash=shadow_record.record_hash,
    )
    assert evidence.shadow_config_fingerprint == config.fingerprint
    assert evidence.portfolio_return == Decimal("0.01")
    assert forward.control_state().head_hash == evidence.evidence_hash
    assert policy_binding.performance_qualification_deferred is True


def test_w84_forward_binding_cannot_mint_authority(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, resolution, identity = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    config, config_binding = _config(identity, resolution)
    _, binding = build_candidate_forward_policy(
        identity=identity,
        config_binding=config_binding,
        campaign_id="w84-forward-authority",
        frozen_at=config.activated_at,
        activated_at=config.activated_at + timedelta(seconds=1),
        w83_resolved_at=resolution.resolved_at,
    )

    with pytest.raises(
        ShadowForwardCandidateProvenanceIntegrityError,
        match="may not grant PAPER, execution, capital, or LIVE authority",
    ):
        replace(binding, paper_candidate_authorized=True)
    with pytest.raises(
        ShadowForwardCandidateProvenanceIntegrityError,
        match="may not grant PAPER, execution, capital, or LIVE authority",
    ):
        replace(binding, capital_authority="TRADING")
    with pytest.raises(
        ShadowForwardCandidateProvenanceIntegrityError,
        match="may not grant PAPER, execution, capital, or LIVE authority",
    ):
        replace(binding, live_trading="ENABLED")
