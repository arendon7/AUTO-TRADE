from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

import autotrade.paper_runtime_funding_capacity as funding_module
from autotrade.paper_runtime_funding_capacity import (
    PaperRuntimeFundingCapacityBlocker,
    PaperRuntimeFundingCapacityIntegrityError,
    PaperRuntimeFundingCapacityProof,
    PaperRuntimeFundingCapacityStatus,
)
from test_w86_paper_runtime_funding_capacity import _chain, _fund


def _rehashed(proof: PaperRuntimeFundingCapacityProof, **changes):
    values = {
        name: getattr(proof, name)
        for name in PaperRuntimeFundingCapacityProof.__dataclass_fields__
        if name != "proof_hash"
    }
    values.update(changes)
    return PaperRuntimeFundingCapacityProof(
        **values,
        proof_hash=funding_module._hash(funding_module._payload_values(values)),
    )


def test_rehashed_account_freshness_lie_is_rejected(monkeypatch):
    proof = _fund(monkeypatch, _chain(monkeypatch))
    with pytest.raises(
        PaperRuntimeFundingCapacityIntegrityError,
        match="account freshness flag is inconsistent",
    ):
        _rehashed(proof, account_fresh=False)


def test_rehashed_blocker_projection_lie_is_rejected(monkeypatch):
    proof = _fund(monkeypatch, _chain(monkeypatch))
    forged_blockers = (
        PaperRuntimeFundingCapacityBlocker.INSUFFICIENT_BUYING_POWER,
    )
    with pytest.raises(
        PaperRuntimeFundingCapacityIntegrityError,
        match="blocker set is not the exact fail-closed projection",
    ):
        _rehashed(
            proof,
            blocker_codes=forged_blockers,
            status=PaperRuntimeFundingCapacityStatus.BLOCKED,
            paper_runtime_ready=False,
            valid_until=proof.observed_at,
        )


def test_rehashed_final_readiness_state_lie_is_rejected(monkeypatch):
    proof = _fund(monkeypatch, _chain(monkeypatch))
    with pytest.raises(
        PaperRuntimeFundingCapacityIntegrityError,
        match="blocker set is not the exact fail-closed projection",
    ):
        _rehashed(proof, final_readiness_ready=False)


def test_rehashed_valid_until_extension_is_rejected(monkeypatch):
    proof = _fund(monkeypatch, _chain(monkeypatch))
    with pytest.raises(
        PaperRuntimeFundingCapacityIntegrityError,
        match="valid_until is inconsistent",
    ):
        _rehashed(proof, valid_until=proof.valid_until + timedelta(microseconds=1))


def test_embedded_policy_hash_must_match_exact_windows(monkeypatch):
    proof = _fund(monkeypatch, _chain(monkeypatch))
    with pytest.raises(
        PaperRuntimeFundingCapacityIntegrityError,
        match="policy hash disagrees",
    ):
        _rehashed(proof, max_account_age_seconds=4)


def test_constructor_rejects_policy_widening_even_with_rehashed_payload(monkeypatch):
    proof = _fund(monkeypatch, _chain(monkeypatch))
    values = {
        name: getattr(proof, name)
        for name in PaperRuntimeFundingCapacityProof.__dataclass_fields__
        if name != "proof_hash"
    }
    values["max_account_age_seconds"] = 6
    values["policy_hash"] = funding_module._policy_hash(6, proof.ready_ttl_seconds)
    with pytest.raises(ValueError, match="max_account_age_seconds"):
        PaperRuntimeFundingCapacityProof(
            **values,
            proof_hash=funding_module._hash(funding_module._payload_values(values)),
        )


def test_direct_dataclass_replace_cannot_escalate_runtime_or_capital(monkeypatch):
    proof = _fund(monkeypatch, _chain(monkeypatch))
    with pytest.raises(PaperRuntimeFundingCapacityIntegrityError, match="may not grant"):
        replace(proof, paper_execution_authorized=True)
    with pytest.raises(PaperRuntimeFundingCapacityIntegrityError, match="may not grant"):
        replace(proof, capital_authority="PAPER")
