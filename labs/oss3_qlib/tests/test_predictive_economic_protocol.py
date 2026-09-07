from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import sqlite3

import pytest

from labs.oss3_qlib.predictive_economic_protocol import (
    ANNUALIZATION_POLICY,
    CASH_POLICY,
    ECONOMIC_HOLDOUT_PURPOSE,
    EXECUTION_PRICE_POLICY,
    OSS3D2M_COST_POLICY_VERSION,
    OSS3D2M_DECISION_POLICY_VERSION,
    OSS3D2M_HOLDOUT_COMMITMENT_VERSION,
    EconomicCostPolicy,
    EconomicDecisionPolicy,
    EconomicHoldoutCommitment,
    PredictiveEconomicProtocolConflict,
    PredictiveEconomicProtocolGovernanceError,
    SQLiteOSS3PredictiveEconomicProtocolRegistry,
    canonical_oss3d2m_cost_policy,
    canonical_oss3d2m_decision_policy,
    predictive_economic_protocol_code_hash,
    read_oss3d2m_protocol_read_only,
)
from labs.oss3_qlib.tests.d2m_fixture import (
    D2M_REGISTERED_AT,
    build_d2m_source,
)


UTC = timezone.utc


def _registry_and_receipt(tmp_path):
    source = build_d2m_source(tmp_path)
    registry = SQLiteOSS3PredictiveEconomicProtocolRegistry(source.shared_sqlite_path)
    receipt = registry.preregister(
        economic_protocol_id="oss3d2m-economic-protocol-001",
        d2l_receipt=source.d2l_receipt,
        d2j_protocol=source.d2l_source.protocol,
        economic_holdout_commitment=source.economic_holdout_commitment,
        now=D2M_REGISTERED_AT,
    )
    return source, registry, receipt


def test_canonical_cost_policy_is_explicit_nonzero_and_fail_closed():
    policy = canonical_oss3d2m_cost_policy()
    assert policy.fee_bps == Decimal("10")
    assert policy.half_spread_bps == Decimal("5")
    assert policy.slippage_bps == Decimal("5")
    assert policy.total_cost_bps == Decimal("20")
    assert policy.max_volume_participation == Decimal("0.10")
    assert policy.min_trade_notional == Decimal("10")
    assert policy.allow_zero_total_costs is False
    assert policy.allow_short is False
    assert policy.allow_leverage is False
    assert policy.allow_margin is False
    assert policy.requires_w81_w82_continuity is True
    assert policy.broker_authoritative_costs_claimed is False
    assert policy.execution_cost_model.total_bps == Decimal("20")


def test_canonical_decision_policy_freezes_robust_economic_gates():
    policy = canonical_oss3d2m_decision_policy()
    assert policy.annualization_policy == ANNUALIZATION_POLICY
    assert policy.min_net_return == Decimal("0")
    assert policy.min_sharpe == Decimal("1.5")
    assert policy.min_profit_factor == Decimal("1.3")
    assert policy.max_drawdown == Decimal("0.15")
    assert policy.min_fills == 10
    assert policy.min_rebalances == 10
    assert policy.require_positive_net_return is True
    assert policy.require_all_gates is True
    assert policy.max_evaluations == 1
    assert policy.retuning_allowed is False
    assert policy.reselection_allowed is False
    assert policy.fallback_policy_allowed is False
    assert policy.second_attempt_allowed is False
    assert policy.failure_is_terminal is True


def test_zero_cost_short_leverage_or_margin_policies_are_rejected():
    base = canonical_oss3d2m_cost_policy()
    with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="zero-cost"):
        EconomicCostPolicy(
            policy_version=OSS3D2M_COST_POLICY_VERSION,
            policy_id="zero-costs",
            fee_bps=Decimal("0"),
            half_spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            execution_price_policy=EXECUTION_PRICE_POLICY,
            volume_policy=base.volume_policy,
            rounding_policy=base.rounding_policy,
            cash_policy=CASH_POLICY,
            max_volume_participation=Decimal("0.10"),
            min_trade_notional=Decimal("10"),
            allow_zero_total_costs=False,
            allow_short=False,
            allow_leverage=False,
            allow_margin=False,
            requires_w81_w82_continuity=True,
            broker_authoritative_costs_claimed=False,
        )
    for flag in ("allow_short", "allow_leverage", "allow_margin"):
        with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="long-only"):
            replace(base, **{flag: True})


def test_decision_threshold_drift_is_rejected():
    base = canonical_oss3d2m_decision_policy()
    with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="Sharpe"):
        replace(base, min_sharpe=Decimal("1.0"))
    with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="profit-factor"):
        replace(base, min_profit_factor=Decimal("1.1"))
    with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="max-drawdown"):
        replace(base, max_drawdown=Decimal("0.25"))
    with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="one-shot"):
        replace(base, max_evaluations=2)


def test_economic_holdout_commitment_is_value_opaque_and_has_sample_floor(tmp_path):
    source = build_d2m_source(tmp_path)
    commitment = source.economic_holdout_commitment
    assert commitment.purpose == ECONOMIC_HOLDOUT_PURPOSE
    assert commitment.bar_count == 90
    assert commitment.symbol_count == 3
    assert commitment.market_values_exposed is False
    assert commitment.economic_outcomes_observed is False
    with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="bar_count"):
        replace(commitment, bar_count=59)
    with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="value-opaque"):
        replace(commitment, market_values_exposed=True)
    with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="value-opaque"):
        replace(commitment, economic_outcomes_observed=True)


def test_d2m_preregistration_requires_exact_d2l_durable_state_and_round_trips(tmp_path):
    source, registry, receipt = _registry_and_receipt(tmp_path)
    assert receipt.source_d2l_receipt_hash == source.d2l_receipt.receipt_hash
    assert receipt.source_d2l_binding_hash == source.binding.binding_hash
    assert receipt.source_strategy_semantic_hash == source.binding.strategy_semantic_hash
    assert receipt.source_d2j_protocol_receipt_hash == source.d2l_source.protocol.receipt_hash
    assert receipt.d2l_preregistration_proven is True
    assert receipt.d2k_start_absent_at_commit is True
    assert receipt.d2k_permit_absent_at_commit is True
    assert receipt.predictive_final_holdout_observed is False
    assert receipt.economic_holdout_observed is False
    assert receipt.economic_holdout_consumed is False
    assert receipt.profitability_claim_authorized is False
    assert receipt.promotion_authorized is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert receipt.protocol_code_hash == predictive_economic_protocol_code_hash()

    reconstructed = read_oss3d2m_protocol_read_only(
        source.shared_sqlite_path,
        economic_protocol_id=receipt.economic_protocol_id,
    )
    assert reconstructed == receipt
    assert registry.preregister(
        economic_protocol_id=receipt.economic_protocol_id,
        d2l_receipt=source.d2l_receipt,
        d2j_protocol=source.d2l_source.protocol,
        economic_holdout_commitment=source.economic_holdout_commitment,
        now=D2M_REGISTERED_AT,
    ) == receipt


def test_d2m_rejects_missing_d2l_state_in_shared_sqlite(tmp_path):
    source = build_d2m_source(tmp_path)
    wrong_path = tmp_path / "wrong-shared.sqlite3"
    registry = SQLiteOSS3PredictiveEconomicProtocolRegistry(wrong_path)
    with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="D2L preregistration"):
        registry.preregister(
            economic_protocol_id="oss3d2m-missing-d2l",
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2l_source.protocol,
            economic_holdout_commitment=source.economic_holdout_commitment,
            now=D2M_REGISTERED_AT,
        )


def test_d2m_rejects_registration_after_d2k_start(tmp_path):
    source = build_d2m_source(tmp_path)
    path = source.shared_sqlite_path
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE oss3_final_holdout_evaluation_starts (
                evaluation_id TEXT,
                protocol_id TEXT,
                holdout_authorization_id TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO oss3_final_holdout_evaluation_starts VALUES (?, ?, ?)",
            (
                "d2k-before-d2m",
                source.d2l_source.protocol.protocol_id,
                source.d2l_source.protocol.expected_holdout_authorization_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    registry = SQLiteOSS3PredictiveEconomicProtocolRegistry(path)
    with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="after D2K start"):
        registry.preregister(
            economic_protocol_id="oss3d2m-after-d2k",
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2l_source.protocol,
            economic_holdout_commitment=source.economic_holdout_commitment,
            now=D2M_REGISTERED_AT,
        )


def test_d2m_rejects_registration_after_holdout_permit_consumption(tmp_path):
    source = build_d2m_source(tmp_path)
    path = source.shared_sqlite_path
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE holdout_permits (
                permit_id TEXT,
                issued_by TEXT,
                purpose TEXT,
                used_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO holdout_permits VALUES (?, ?, ?, ?)",
            (
                source.d2l_source.protocol.expected_holdout_authorization_id,
                "OSS3D2K_FINAL_HOLDOUT_EVALUATOR",
                "final_validation",
                D2M_REGISTERED_AT.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    registry = SQLiteOSS3PredictiveEconomicProtocolRegistry(path)
    with pytest.raises(PredictiveEconomicProtocolGovernanceError, match="permit consumption"):
        registry.preregister(
            economic_protocol_id="oss3d2m-after-permit",
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2l_source.protocol,
            economic_holdout_commitment=source.economic_holdout_commitment,
            now=D2M_REGISTERED_AT,
        )


def test_d2m_conflicting_cost_policy_cannot_rebind_same_strategy(tmp_path):
    source, registry, receipt = _registry_and_receipt(tmp_path)
    different_cost = replace(
        canonical_oss3d2m_cost_policy(),
        policy_id="oss3d2m-more-conservative-costs-v1",
        fee_bps=Decimal("15"),
    )
    assert different_cost.fingerprint != receipt.cost_policy.fingerprint
    with pytest.raises(PredictiveEconomicProtocolConflict, match="another D2M"):
        registry.preregister(
            economic_protocol_id="oss3d2m-economic-protocol-002",
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2l_source.protocol,
            economic_holdout_commitment=source.economic_holdout_commitment,
            now=D2M_REGISTERED_AT,
            cost_policy=different_cost,
        )


def test_d2m_registry_is_append_only(tmp_path):
    source, registry, receipt = _registry_and_receipt(tmp_path)
    conn = sqlite3.connect(registry.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE oss3_predictive_economic_protocols SET cost_policy_hash = ? WHERE economic_protocol_id = ?",
                ("f" * 64, receipt.economic_protocol_id),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM oss3_predictive_economic_protocols WHERE economic_protocol_id = ?",
                (receipt.economic_protocol_id,),
            )
        conn.rollback()
    finally:
        conn.close()


def test_d2m_semantic_code_hash_is_stable_sha256():
    first = predictive_economic_protocol_code_hash()
    second = predictive_economic_protocol_code_hash()
    assert first == second
    assert len(first) == 64
    assert first == first.lower()
    int(first, 16)
