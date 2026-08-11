from dataclasses import replace

import pytest

from autotrade.portfolio_manager import PortfolioSizingBlocked

from test_r4_portfolio_manager import _candidates, _manager, _size


def test_candidate_cannot_read_another_strategy_health_identity(tmp_path, now):
    manager = _manager(tmp_path, now)
    candidates = list(_candidates(now))
    candidates[0] = replace(candidates[0], health_entity_id="beta")
    with pytest.raises(PortfolioSizingBlocked) as exc:
        _size(manager, now, candidates=tuple(candidates))
    assert exc.value.reason_code == "HEALTH_ENTITY_STRATEGY_MISMATCH"


def test_decision_binds_base_robustness_evidence(tmp_path, now):
    decision = _size(_manager(tmp_path, now), now)
    assert len(decision.base_robustness_fingerprint) == 64
    payload = decision.to_payload()
    assert payload["base_robustness_fingerprint"] == decision.base_robustness_fingerprint
    assert payload["fingerprint"] == decision.fingerprint
