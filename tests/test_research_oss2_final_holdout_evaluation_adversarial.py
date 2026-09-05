from __future__ import annotations

from datetime import datetime, timezone
from math import inf, nan

import pytest

from autotrade.research.oss2_final_holdout_evaluation import (
    OSS2H_CONTRACT_VERSION,
    OSS2FinalHoldoutDecision,
    OSS2FinalHoldoutEvaluationIntegrityError,
    OSS2FinalHoldoutEvaluationReceipt,
    OSS2FinalHoldoutGate,
    OSS2FinalHoldoutStartReceipt,
    ProtectedOSS2FinalHoldout,
    _gate_payload,
    _hash,
    _require_aware,
    _require_aware_iso,
    _require_hash,
    _require_id,
    _start_from_row,
    _start_payload,
    _terminal_from_row,
    _terminal_payload,
    _trial_spec_from_json,
    _verify_side_columns,
)


H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64
H5 = "5" * 64
H6 = "6" * 64
H7 = "7" * 64
STARTED = "2026-09-05T12:00:00+00:00"
TERMINAL = "2026-09-05T12:00:01+00:00"


def _valid_gates() -> tuple[OSS2FinalHoldoutGate, ...]:
    return (
        OSS2FinalHoldoutGate("FINAL_NET_RETURN_MIN", True, 0.10, ">=", 0.0),
        OSS2FinalHoldoutGate("FINAL_SHARPE_MIN", True, 0.50, ">=", 0.0),
        OSS2FinalHoldoutGate("FINAL_DRAWDOWN_MAX", True, 0.10, "<=", 0.35),
    )


def _start(**overrides: object) -> OSS2FinalHoldoutStartReceipt:
    fields: dict[str, object] = {
        "evaluation_id": "eval-1",
        "contract_version": OSS2H_CONTRACT_VERSION,
        "campaign_id": "campaign-1",
        "selected_trial_id": "trial-1",
        "protocol_id": "protocol-1",
        "protocol_receipt_hash": H1,
        "candidate_binding_fingerprint": H2,
        "holdout_authorization_id": "authorization-1",
        "holdout_universe_hash": H3,
        "config_hash": H4,
        "started_at": STARTED,
    }
    fields.update(overrides)
    payload = dict(fields)
    start_hash = _hash(payload)
    return OSS2FinalHoldoutStartReceipt(
        evaluation_id=str(fields["evaluation_id"]),
        contract_version=str(fields["contract_version"]),
        campaign_id=str(fields["campaign_id"]),
        selected_trial_id=str(fields["selected_trial_id"]),
        protocol_id=str(fields["protocol_id"]),
        protocol_receipt_hash=str(fields["protocol_receipt_hash"]),
        candidate_binding_fingerprint=str(fields["candidate_binding_fingerprint"]),
        holdout_authorization_id=str(fields["holdout_authorization_id"]),
        holdout_universe_hash=str(fields["holdout_universe_hash"]),
        config_hash=str(fields["config_hash"]),
        started_at=str(fields["started_at"]),
        start_hash=start_hash,
    )


def _receipt(**overrides: object) -> OSS2FinalHoldoutEvaluationReceipt:
    fields: dict[str, object] = {
        "evaluation_id": "eval-1",
        "contract_version": OSS2H_CONTRACT_VERSION,
        "campaign_id": "campaign-1",
        "selected_trial_id": "trial-1",
        "protocol_id": "protocol-1",
        "protocol_receipt_hash": H1,
        "start_hash": H2,
        "candidate_binding_fingerprint": H3,
        "holdout_authorization_id": "authorization-1",
        "holdout_universe_hash": H4,
        "config_hash": H5,
        "result_hash": H6,
        "decision": OSS2FinalHoldoutDecision.PASS,
        "gates": _valid_gates(),
        "failed_gate_ids": (),
        "net_return": 0.10,
        "sharpe": 0.50,
        "max_drawdown": 0.10,
        "failure_code": "",
        "started_at": STARTED,
        "terminal_at": TERMINAL,
        "final_holdout_observed": True,
        "final_holdout_consumed": True,
        "retuning_allowed": False,
        "reselection_allowed": False,
        "second_attempt_allowed": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    fields.update(overrides)
    payload = {
        "evaluation_id": fields["evaluation_id"],
        "contract_version": fields["contract_version"],
        "campaign_id": fields["campaign_id"],
        "selected_trial_id": fields["selected_trial_id"],
        "protocol_id": fields["protocol_id"],
        "protocol_receipt_hash": fields["protocol_receipt_hash"],
        "start_hash": fields["start_hash"],
        "candidate_binding_fingerprint": fields["candidate_binding_fingerprint"],
        "holdout_authorization_id": fields["holdout_authorization_id"],
        "holdout_universe_hash": fields["holdout_universe_hash"],
        "config_hash": fields["config_hash"],
        "result_hash": fields["result_hash"],
        "decision": fields["decision"].value,
        "gates": [_gate_payload(gate) for gate in fields["gates"]],
        "failed_gate_ids": list(fields["failed_gate_ids"]),
        "net_return": fields["net_return"],
        "sharpe": fields["sharpe"],
        "max_drawdown": fields["max_drawdown"],
        "failure_code": fields["failure_code"],
        "started_at": fields["started_at"],
        "terminal_at": fields["terminal_at"],
        "final_holdout_observed": fields["final_holdout_observed"],
        "final_holdout_consumed": fields["final_holdout_consumed"],
        "retuning_allowed": fields["retuning_allowed"],
        "reselection_allowed": fields["reselection_allowed"],
        "second_attempt_allowed": fields["second_attempt_allowed"],
        "paper_execution_authorized": fields["paper_execution_authorized"],
        "capital_authority": fields["capital_authority"],
        "live_trading": fields["live_trading"],
    }
    try:
        receipt_hash = _hash(payload)
    except ValueError:
        # Deliberately malformed/non-finite payloads still need a syntactically
        # valid hash to reach the earlier fail-closed constructor branch under test.
        receipt_hash = H7
    return OSS2FinalHoldoutEvaluationReceipt(
        evaluation_id=str(fields["evaluation_id"]),
        contract_version=str(fields["contract_version"]),
        campaign_id=str(fields["campaign_id"]),
        selected_trial_id=str(fields["selected_trial_id"]),
        protocol_id=str(fields["protocol_id"]),
        protocol_receipt_hash=str(fields["protocol_receipt_hash"]),
        start_hash=str(fields["start_hash"]),
        candidate_binding_fingerprint=str(fields["candidate_binding_fingerprint"]),
        holdout_authorization_id=str(fields["holdout_authorization_id"]),
        holdout_universe_hash=str(fields["holdout_universe_hash"]),
        config_hash=str(fields["config_hash"]),
        result_hash=str(fields["result_hash"]),
        decision=fields["decision"],
        gates=fields["gates"],
        failed_gate_ids=fields["failed_gate_ids"],
        net_return=fields["net_return"],
        sharpe=fields["sharpe"],
        max_drawdown=fields["max_drawdown"],
        failure_code=str(fields["failure_code"]),
        started_at=str(fields["started_at"]),
        terminal_at=str(fields["terminal_at"]),
        final_holdout_observed=fields["final_holdout_observed"],
        final_holdout_consumed=fields["final_holdout_consumed"],
        retuning_allowed=fields["retuning_allowed"],
        reselection_allowed=fields["reselection_allowed"],
        second_attempt_allowed=fields["second_attempt_allowed"],
        paper_execution_authorized=fields["paper_execution_authorized"],
        capital_authority=str(fields["capital_authority"]),
        live_trading=str(fields["live_trading"]),
        receipt_hash=receipt_hash,
    )


def _structural_receipt(**overrides: object) -> OSS2FinalHoldoutEvaluationReceipt:
    values: dict[str, object] = {
        "decision": OSS2FinalHoldoutDecision.FAIL,
        "result_hash": "",
        "gates": (),
        "failed_gate_ids": (),
        "net_return": None,
        "sharpe": None,
        "max_drawdown": None,
        "failure_code": "EVALUATION_ERROR:RuntimeError",
    }
    values.update(overrides)
    return _receipt(**values)


def test_gate_constructor_rejects_noncanonical_inputs():
    with pytest.raises(ValueError, match="gate id"):
        OSS2FinalHoldoutGate("OTHER", True, 1.0, ">=", 0.0)
    with pytest.raises(ValueError, match="comparison"):
        OSS2FinalHoldoutGate("FINAL_NET_RETURN_MIN", True, 1.0, ">", 0.0)
    with pytest.raises(ValueError, match="finite"):
        OSS2FinalHoldoutGate("FINAL_NET_RETURN_MIN", True, nan, ">=", 0.0)
    with pytest.raises(ValueError, match="finite"):
        OSS2FinalHoldoutGate("FINAL_NET_RETURN_MIN", True, 1.0, ">=", inf)
    with pytest.raises(ValueError, match="pass flag"):
        OSS2FinalHoldoutGate("FINAL_NET_RETURN_MIN", False, 1.0, ">=", 0.0)
    with pytest.raises(ValueError, match="pass flag"):
        OSS2FinalHoldoutGate("FINAL_DRAWDOWN_MAX", True, 0.50, "<=", 0.35)


def test_start_receipt_rejects_version_time_and_hash_drift():
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="start version"):
        _start(contract_version="OSS2H_WRONG")
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="invalid started_at"):
        _start(started_at="not-a-date")
    with pytest.raises(ValueError, match="timezone-aware"):
        _start(started_at="2026-09-05T12:00:00")

    valid = _start()
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="start hash"):
        OSS2FinalHoldoutStartReceipt(
            evaluation_id=valid.evaluation_id,
            contract_version=valid.contract_version,
            campaign_id=valid.campaign_id,
            selected_trial_id=valid.selected_trial_id,
            protocol_id=valid.protocol_id,
            protocol_receipt_hash=valid.protocol_receipt_hash,
            candidate_binding_fingerprint=valid.candidate_binding_fingerprint,
            holdout_authorization_id=valid.holdout_authorization_id,
            holdout_universe_hash=valid.holdout_universe_hash,
            config_hash=valid.config_hash,
            started_at=valid.started_at,
            start_hash=H7,
        )


def test_terminal_receipt_rejects_temporal_and_observation_drift():
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="receipt version"):
        _receipt(contract_version="OSS2H_WRONG")
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="predates"):
        _receipt(terminal_at="2026-09-05T11:59:59+00:00")
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="consumed/observed"):
        _receipt(final_holdout_observed=False)
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="consumed/observed"):
        _receipt(final_holdout_consumed=False)


def test_terminal_receipt_rejects_post_holdout_scientific_flexibility():
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="retuning"):
        _receipt(retuning_allowed=True)
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="retuning"):
        _receipt(reselection_allowed=True)
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="retuning"):
        _receipt(second_attempt_allowed=True)


def test_terminal_receipt_rejects_operational_authority():
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="PAPER"):
        _receipt(paper_execution_authorized=True)
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="capital or LIVE"):
        _receipt(capital_authority="USD_1")
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="capital or LIVE"):
        _receipt(live_trading="ENABLED")


def test_structural_failure_cannot_fabricate_success_evidence():
    assert _structural_receipt().decision is OSS2FinalHoldoutDecision.FAIL
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="terminal FAIL"):
        _structural_receipt(decision=OSS2FinalHoldoutDecision.PASS)
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="fabricate result/gate"):
        _structural_receipt(result_hash=H6)
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="fabricate result/gate"):
        _structural_receipt(gates=_valid_gates())
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="fabricate metric"):
        _structural_receipt(net_return=0.0)


def test_metric_receipt_requires_complete_finite_three_gate_evidence():
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="finite metrics"):
        _receipt(net_return=None)
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="finite metrics"):
        _receipt(sharpe=nan)
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="drawdown"):
        _receipt(max_drawdown=1.1)
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="exactly three"):
        _receipt(gates=_valid_gates()[:2])


def test_metric_receipt_rejects_gate_order_failed_list_and_decision_drift():
    gates = _valid_gates()
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="gate order"):
        _receipt(gates=(gates[1], gates[0], gates[2]))

    failing = (
        OSS2FinalHoldoutGate("FINAL_NET_RETURN_MIN", False, -0.10, ">=", 0.0),
        OSS2FinalHoldoutGate("FINAL_SHARPE_MIN", True, 0.50, ">=", 0.0),
        OSS2FinalHoldoutGate("FINAL_DRAWDOWN_MAX", True, 0.10, "<=", 0.35),
    )
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="failed gate list"):
        _receipt(
            gates=failing,
            net_return=-0.10,
            decision=OSS2FinalHoldoutDecision.FAIL,
            failed_gate_ids=(),
        )
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="mechanically derived"):
        _receipt(
            gates=failing,
            net_return=-0.10,
            decision=OSS2FinalHoldoutDecision.PASS,
            failed_gate_ids=("FINAL_NET_RETURN_MIN",),
        )

    terminal_fail = _receipt(
        gates=failing,
        net_return=-0.10,
        decision=OSS2FinalHoldoutDecision.FAIL,
        failed_gate_ids=("FINAL_NET_RETURN_MIN",),
    )
    assert terminal_fail.decision is OSS2FinalHoldoutDecision.FAIL


def test_receipt_hash_drift_is_rejected():
    valid = _receipt()
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="receipt hash"):
        OSS2FinalHoldoutEvaluationReceipt(
            evaluation_id=valid.evaluation_id,
            contract_version=valid.contract_version,
            campaign_id=valid.campaign_id,
            selected_trial_id=valid.selected_trial_id,
            protocol_id=valid.protocol_id,
            protocol_receipt_hash=valid.protocol_receipt_hash,
            start_hash=valid.start_hash,
            candidate_binding_fingerprint=valid.candidate_binding_fingerprint,
            holdout_authorization_id=valid.holdout_authorization_id,
            holdout_universe_hash=valid.holdout_universe_hash,
            config_hash=valid.config_hash,
            result_hash=valid.result_hash,
            decision=valid.decision,
            gates=valid.gates,
            failed_gate_ids=valid.failed_gate_ids,
            net_return=valid.net_return,
            sharpe=valid.sharpe,
            max_drawdown=valid.max_drawdown,
            failure_code=valid.failure_code,
            started_at=valid.started_at,
            terminal_at=valid.terminal_at,
            final_holdout_observed=valid.final_holdout_observed,
            final_holdout_consumed=valid.final_holdout_consumed,
            retuning_allowed=valid.retuning_allowed,
            reselection_allowed=valid.reselection_allowed,
            second_attempt_allowed=valid.second_attempt_allowed,
            paper_execution_authorized=valid.paper_execution_authorized,
            capital_authority=valid.capital_authority,
            live_trading=valid.live_trading,
            receipt_hash=H7,
        )


def test_payload_helpers_round_trip_valid_receipts():
    start = _start()
    assert _start_payload(start, include_hash=True)["start_hash"] == start.start_hash
    assert "start_hash" not in _start_payload(start, include_hash=False)

    receipt = _receipt()
    assert _terminal_payload(receipt, include_hash=True)["receipt_hash"] == receipt.receipt_hash
    assert "receipt_hash" not in _terminal_payload(receipt, include_hash=False)
    assert _gate_payload(receipt.gates[0])["gate_id"] == "FINAL_NET_RETURN_MIN"


def test_low_level_identity_hash_and_time_guards_fail_closed():
    with pytest.raises(ValueError, match="canonical id"):
        _require_id("bad id", "x")
    with pytest.raises(ValueError, match="canonical id"):
        _require_id("", "x")
    with pytest.raises(ValueError, match="sha256"):
        _require_hash("A" * 64, "x")
    with pytest.raises(ValueError, match="sha256"):
        _require_hash("abc", "x")
    with pytest.raises(ValueError, match="timezone-aware"):
        _require_aware(datetime(2026, 9, 5, 12, 0, 0), "now")
    _require_aware(datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc), "now")
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="invalid when"):
        _require_aware_iso("not-time", "when")


def test_parsers_and_side_column_verification_reject_corruption():
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="trial JSON"):
        _trial_spec_from_json("not-json")
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="trial JSON"):
        _trial_spec_from_json("{}")
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="start receipt"):
        _start_from_row({"start_json": "not-json"})
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="terminal receipt"):
        _terminal_from_row({"receipt_json": "not-json"})
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="missing side-column"):
        _verify_side_columns({}, {"expected": "x"}, "fixture")
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="side-column mismatch"):
        _verify_side_columns({"expected": "wrong"}, {"expected": "right"}, "fixture")


def test_protected_holdout_requires_real_aligned_universe():
    with pytest.raises(TypeError, match="AlignedMarketUniverse"):
        ProtectedOSS2FinalHoldout(object())
