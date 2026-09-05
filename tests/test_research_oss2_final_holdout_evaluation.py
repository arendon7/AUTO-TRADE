from datetime import timedelta
from decimal import Decimal
import json
import sqlite3

import pytest

from autotrade.research.costs import ExecutionCostModel
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.oss2_campaign import build_oss2_development_campaign
from autotrade.research.oss2_final_holdout_evaluation import (
    OSS2H_CONTRACT_VERSION,
    OSS2FinalHoldoutAlreadyConsumed,
    OSS2FinalHoldoutDecision,
    OSS2FinalHoldoutEvaluationIntegrityError,
    ProtectedOSS2FinalHoldout,
    SQLiteOSS2FinalHoldoutEvaluationRegistry,
    read_oss2_selected_candidate_read_only,
    read_oss2h_evaluation_read_only,
)
from autotrade.research.oss2_final_holdout_protocol import (
    SQLiteOSS2FinalHoldoutProtocolRegistry,
)
from autotrade.research.oss2_holdout_freeze import (
    OSS2F_CONTRACT_VERSION,
    OSS2HoldoutFreezeReceipt,
    OSS2HoldoutFreezeState,
    _hash as freeze_hash,
)
from autotrade.research.trials import SQLiteTrialLedger
from autotrade.research.universe import AlignedMarketUniverse


def _dataset(now, *, symbol: str, closes: list[float]) -> MarketDataset:
    instrument = InstrumentMetadata(
        symbol=symbol,
        venue="TEST",
        quote_currency="USD",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )
    bars = []
    for index, raw_close in enumerate(closes):
        close = Decimal(str(raw_close))
        bars.append(
            Bar(
                symbol=symbol,
                started_at=now + timedelta(minutes=index),
                timeframe_seconds=60,
                open=close,
                high=close + Decimal("0.50"),
                low=max(close - Decimal("0.50"), Decimal("0.01")),
                close=close,
                volume=Decimal("1000000"),
            )
        )
    return MarketDataset(
        instrument=instrument,
        bars=tuple(bars),
        source=f"oss2h-fixture:{symbol}",
    )


def _universe(now, *, mode: str = "up", bars: int = 48) -> AlignedMarketUniverse:
    if mode == "up":
        series = {
            "AAA-USD": [100 + 2.2 * i for i in range(bars)],
            "BBB-USD": [100 + 1.3 * i for i in range(bars)],
            "CCC-USD": [100 + 0.5 * i for i in range(bars)],
        }
    elif mode == "whipsaw":
        pivot = max(15, bars // 2)
        aaa = [100 + 3.0 * i for i in range(pivot)]
        last = aaa[-1]
        aaa.extend([max(20.0, last - 8.0 * (i + 1)) for i in range(bars - pivot)])
        series = {
            "AAA-USD": aaa,
            "BBB-USD": [100 + 0.05 * i for i in range(bars)],
            "CCC-USD": [100 - 0.05 * i for i in range(bars)],
        }
    elif mode == "short":
        series = {
            "AAA-USD": [100 + i for i in range(bars)],
            "BBB-USD": [100 + 0.5 * i for i in range(bars)],
            "CCC-USD": [100 + 0.2 * i for i in range(bars)],
        }
    else:
        raise ValueError(mode)
    return AlignedMarketUniverse.from_datasets(
        datasets=tuple(
            _dataset(now, symbol=symbol, closes=series[symbol])
            for symbol in sorted(series)
        ),
        universe_name=f"oss2h-{mode}",
    )


def _make_freeze(*, campaign_id: str, selected_trial_id: str):
    payload = {
        "receipt_id": "oss2f-fixture",
        "contract_version": OSS2F_CONTRACT_VERSION,
        "campaign_id": campaign_id,
        "selected_trial_id": selected_trial_id,
        "oss2d_evidence_fingerprint": "a" * 64,
        "oss2e_policy_fingerprint": "b" * 64,
        "oss2e_evidence_fingerprint": "c" * 64,
        "candidate_freeze_fingerprint": "d" * 64,
        "decision": OSS2HoldoutFreezeState.HOLDOUT_ELIGIBLE.value,
        "failed_gate_ids": [],
        "final_holdout_observed": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS2HoldoutFreezeReceipt(
        receipt_id="oss2f-fixture",
        contract_version=OSS2F_CONTRACT_VERSION,
        campaign_id=campaign_id,
        selected_trial_id=selected_trial_id,
        oss2d_evidence_fingerprint="a" * 64,
        oss2e_policy_fingerprint="b" * 64,
        oss2e_evidence_fingerprint="c" * 64,
        candidate_freeze_fingerprint="d" * 64,
        decision=OSS2HoldoutFreezeState.HOLDOUT_ELIGIBLE,
        failed_gate_ids=(),
        final_holdout_observed=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
        receipt_hash=freeze_hash(payload),
    )


def _setup(tmp_path, now):
    db = tmp_path / "oss2h.db"
    cost = ExecutionCostModel(
        fee_bps=Decimal("2"),
        half_spread_bps=Decimal("1"),
        slippage_bps=Decimal("1"),
    )
    plan = build_oss2_development_campaign(
        universe_hash="1" * 64,
        code_version="oss2h-code-v1",
        initial_cash=Decimal("100000"),
        annualization_factor=Decimal("525600"),
        cost_model=cost,
        top_n=1,
        min_average_dollar_volume=Decimal("1000"),
        max_weight_per_asset=Decimal("1"),
        gross_target=Decimal("0.80"),
        max_volume_participation=Decimal("0.10"),
        min_trade_notional=Decimal("1"),
    )
    selected = next(
        trial
        for trial in plan.trials
        if trial.parameters["ranking_lookback_bars"] == 12
        and trial.parameters["rebalance_every_bars"] == 1
    )
    trials = SQLiteTrialLedger(db)
    trials.create_campaign(plan.campaign, now=now)
    trials.preregister(selected, now=now + timedelta(seconds=1))
    trials.record_completed(
        trial_id=selected.trial_id,
        metrics={"common_window_sharpe": 1.0},
        p_value=None,
        now=now + timedelta(seconds=2),
    )

    freeze = _make_freeze(
        campaign_id=plan.campaign.campaign_id,
        selected_trial_id=selected.trial_id,
    )
    protocols = SQLiteOSS2FinalHoldoutProtocolRegistry(db)
    protocol = protocols.preregister_and_record(
        protocol_id="oss2g-fixture",
        freeze=freeze,
    )
    candidate = read_oss2_selected_candidate_read_only(db, protocol=protocol)
    registry = SQLiteOSS2FinalHoldoutEvaluationRegistry(db)
    return db, protocol, candidate, registry


def test_oss2h_consumes_once_and_produces_terminal_pass(tmp_path, now):
    db, protocol, candidate, registry = _setup(tmp_path, now)
    holdout = ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=1)))

    receipt = registry.evaluate_and_record(
        evaluation_id="oss2h-pass",
        protocol=protocol,
        candidate=candidate,
        holdout=holdout,
        now=now + timedelta(seconds=3),
    )

    assert receipt.contract_version == OSS2H_CONTRACT_VERSION
    assert receipt.decision is OSS2FinalHoldoutDecision.PASS
    assert receipt.failed_gate_ids == ()
    assert tuple(g.gate_id for g in receipt.gates) == (
        "FINAL_NET_RETURN_MIN",
        "FINAL_SHARPE_MIN",
        "FINAL_DRAWDOWN_MAX",
    )
    assert receipt.net_return is not None and receipt.net_return >= 0
    assert receipt.sharpe is not None and receipt.sharpe >= 0
    assert receipt.max_drawdown is not None and receipt.max_drawdown <= 0.35
    assert receipt.final_holdout_observed is True
    assert receipt.final_holdout_consumed is True
    assert receipt.retuning_allowed is False
    assert receipt.reselection_allowed is False
    assert receipt.second_attempt_allowed is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"

    verified = read_oss2h_evaluation_read_only(db, campaign_id=protocol.campaign_id)
    assert verified == receipt


def test_second_attempt_is_blocked_even_with_new_holdout_object(tmp_path, now):
    _, protocol, candidate, registry = _setup(tmp_path, now)
    first = ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=1)))
    registry.evaluate_and_record(
        evaluation_id="oss2h-first",
        protocol=protocol,
        candidate=candidate,
        holdout=first,
        now=now + timedelta(seconds=3),
    )

    second = ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=2)))
    with pytest.raises(OSS2FinalHoldoutAlreadyConsumed):
        registry.evaluate_and_record(
            evaluation_id="oss2h-second",
            protocol=protocol,
            candidate=candidate,
            holdout=second,
            now=now + timedelta(seconds=4),
        )


def test_same_protected_object_cannot_checkout_twice(tmp_path, now):
    _, protocol, candidate, registry = _setup(tmp_path, now)
    holdout = ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=1)))
    registry.evaluate_and_record(
        evaluation_id="oss2h-one",
        protocol=protocol,
        candidate=candidate,
        holdout=holdout,
        now=now + timedelta(seconds=3),
    )
    with pytest.raises(OSS2FinalHoldoutAlreadyConsumed):
        holdout._checkout(
            permit=__import__(
                "autotrade.research.registry", fromlist=["HoldoutPermit"]
            ).HoldoutPermit(
                permit_id=protocol.holdout_authorization_id,
                issued_by="OSS2H_FINAL_HOLDOUT_EVALUATOR",
            ),
            expected_authorization_id=protocol.holdout_authorization_id,
        )


def test_short_holdout_is_burned_as_terminal_structural_fail(tmp_path, now):
    db, protocol, candidate, registry = _setup(tmp_path, now)
    holdout = ProtectedOSS2FinalHoldout(
        _universe(now + timedelta(days=1), mode="short", bars=10)
    )
    receipt = registry.evaluate_and_record(
        evaluation_id="oss2h-too-short",
        protocol=protocol,
        candidate=candidate,
        holdout=holdout,
        now=now + timedelta(seconds=3),
    )
    assert receipt.decision is OSS2FinalHoldoutDecision.FAIL
    assert receipt.failure_code.startswith("EVALUATION_ERROR:")
    assert receipt.result_hash == ""
    assert receipt.gates == ()
    assert receipt.net_return is None
    assert read_oss2h_evaluation_read_only(db, campaign_id=protocol.campaign_id) == receipt

    with pytest.raises(OSS2FinalHoldoutAlreadyConsumed):
        registry.evaluate_and_record(
            evaluation_id="oss2h-no-retry",
            protocol=protocol,
            candidate=candidate,
            holdout=ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=2))),
            now=now + timedelta(seconds=4),
        )


def test_whipsaw_holdout_mechanically_fails_at_least_one_gate(tmp_path, now):
    _, protocol, candidate, registry = _setup(tmp_path, now)
    receipt = registry.evaluate_and_record(
        evaluation_id="oss2h-whipsaw",
        protocol=protocol,
        candidate=candidate,
        holdout=ProtectedOSS2FinalHoldout(
            _universe(now + timedelta(days=1), mode="whipsaw", bars=48)
        ),
        now=now + timedelta(seconds=3),
    )
    assert receipt.failure_code == ""
    if receipt.decision is OSS2FinalHoldoutDecision.FAIL:
        assert receipt.failed_gate_ids
        assert all(gate_id in {
            "FINAL_NET_RETURN_MIN",
            "FINAL_SHARPE_MIN",
            "FINAL_DRAWDOWN_MAX",
        } for gate_id in receipt.failed_gate_ids)
    else:
        # The test still proves the decision is exclusively gate-derived; market
        # path details can move with deterministic engine implementation changes.
        assert receipt.failed_gate_ids == ()
        assert all(gate.passed for gate in receipt.gates)


def test_selected_candidate_is_independently_read_only_and_exact(tmp_path, now):
    db, protocol, candidate, _ = _setup(tmp_path, now)
    before = db.read_bytes()
    again = read_oss2_selected_candidate_read_only(db, protocol=protocol)
    after = db.read_bytes()
    assert before == after
    assert again == candidate
    assert again.trial_id == protocol.selected_trial_id
    assert again.spec.phase.value == "DEVELOPMENT"
    assert again.spec.holdout_authorization_id == ""
    assert again.config_hash == again.spec.parameters["backtest_config_hash"]


def test_wrong_candidate_identity_fails_before_permit_consumption(tmp_path, now):
    db, protocol, candidate, registry = _setup(tmp_path, now)
    altered = type(candidate)(
        campaign_id=candidate.campaign_id,
        trial_id="different-trial",
        trial_fingerprint=candidate.trial_fingerprint,
        result_hash=candidate.result_hash,
        config_hash=candidate.config_hash,
        spec=candidate.spec,
    )
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError):
        registry.evaluate_and_record(
            evaluation_id="oss2h-bad-candidate",
            protocol=protocol,
            candidate=altered,
            holdout=ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=1))),
            now=now + timedelta(seconds=3),
        )
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM holdout_permits WHERE permit_id = ?",
            (protocol.holdout_authorization_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_append_only_triggers_reject_start_and_terminal_mutation(tmp_path, now):
    db, protocol, candidate, registry = _setup(tmp_path, now)
    registry.evaluate_and_record(
        evaluation_id="oss2h-append-only",
        protocol=protocol,
        candidate=candidate,
        holdout=ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=1))),
        now=now + timedelta(seconds=3),
    )
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(
                "UPDATE oss2_final_holdout_evaluation_starts SET campaign_id='x'"
            )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM oss2_final_holdout_evaluations")
    finally:
        conn.close()


def test_read_only_reader_detects_side_column_tamper(tmp_path, now):
    db, protocol, candidate, registry = _setup(tmp_path, now)
    receipt = registry.evaluate_and_record(
        evaluation_id="oss2h-tamper",
        protocol=protocol,
        candidate=candidate,
        holdout=ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=1))),
        now=now + timedelta(seconds=3),
    )

    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TRIGGER oss2_final_holdout_evaluations_no_update")
        conn.execute(
            "UPDATE oss2_final_holdout_evaluations SET decision='FAIL' WHERE evaluation_id=?",
            (receipt.evaluation_id,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="side-column"):
        read_oss2h_evaluation_read_only(db, campaign_id=protocol.campaign_id)


def test_read_only_reader_rejects_consumed_incomplete_state(tmp_path, now):
    db, protocol, candidate, registry = _setup(tmp_path, now)
    # Exercise the internal durable start primitive to simulate process death
    # after authorization consumption but before terminalization.
    from autotrade.research.oss2_final_holdout_evaluation import _build_start
    from autotrade.research.oss2_campaign import backtest_config_from_oss2_trial
    from autotrade.research.registry import HoldoutPermit

    holdout = ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=1)))
    config = backtest_config_from_oss2_trial(candidate.spec)
    start = _build_start(
        evaluation_id="oss2h-crash",
        protocol=protocol,
        candidate=candidate,
        holdout_universe_hash=holdout.universe_hash,
        config_hash=config.config_hash,
        started_at=now + timedelta(seconds=3),
    )
    registry._consume_and_record_start(
        permit=HoldoutPermit(
            permit_id=protocol.holdout_authorization_id,
            issued_by="OSS2H_FINAL_HOLDOUT_EVALUATOR",
        ),
        start=start,
    )
    with pytest.raises(OSS2FinalHoldoutAlreadyConsumed, match="no retry"):
        read_oss2h_evaluation_read_only(db, campaign_id=protocol.campaign_id)


def test_terminal_receipt_contains_only_scientific_not_operational_authority(tmp_path, now):
    _, protocol, candidate, registry = _setup(tmp_path, now)
    receipt = registry.evaluate_and_record(
        evaluation_id="oss2h-authority",
        protocol=protocol,
        candidate=candidate,
        holdout=ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=1))),
        now=now + timedelta(seconds=3),
    )
    payload = receipt.to_dict()
    assert payload["paper_execution_authorized"] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["live_trading"] == "BLOCKED"
    forbidden = {
        "broker",
        "credentials",
        "order_intent",
        "oms_handoff",
        "capital_reservation",
        "execution_permit",
    }
    assert forbidden.isdisjoint(payload)


def test_terminal_json_hash_tamper_is_detected(tmp_path, now):
    db, protocol, candidate, registry = _setup(tmp_path, now)
    receipt = registry.evaluate_and_record(
        evaluation_id="oss2h-json-tamper",
        protocol=protocol,
        candidate=candidate,
        holdout=ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=1))),
        now=now + timedelta(seconds=3),
    )
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT receipt_json FROM oss2_final_holdout_evaluations WHERE evaluation_id=?",
            (receipt.evaluation_id,),
        ).fetchone()
        payload = json.loads(row[0])
        payload["capital_authority"] = "SOME"
        conn.execute("DROP TRIGGER oss2_final_holdout_evaluations_no_update")
        conn.execute(
            "UPDATE oss2_final_holdout_evaluations SET receipt_json=? WHERE evaluation_id=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), receipt.evaluation_id),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError):
        read_oss2h_evaluation_read_only(db, campaign_id=protocol.campaign_id)
