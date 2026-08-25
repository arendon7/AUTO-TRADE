from __future__ import annotations

from datetime import timedelta

import pytest

import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.paper_runtime_candidate_identity as candidate_module
import autotrade.paper_runtime_read_only_pipeline as pipeline_module
import autotrade.paper_runtime_readiness_source_snapshot as source_module
from autotrade.brokers.alpaca_paper_crypto_market_data import AlpacaPaperCryptoMarketDataConfig
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials, AlpacaPaperGatewayConfig
from autotrade.paper_runtime_read_only_pipeline import (
    PaperRuntimeReadOnlyPipelineIntegrityError,
    collect_paper_runtime_readiness,
)
from test_w86_paper_runtime_final_readiness import _bind_candidate, _source
from test_w86_paper_runtime_market_truth import ACCOUNT_ID, AT, _candidate


def _coherent_source_candidate(*, state=lifecycle.PaperCandidateEligibilityState.ACTIVE):
    seed = _candidate()
    source = _source(state=state)
    for field in (
        "selected_trial_fingerprint",
        "strategy_spec_hash",
        "loaded_runtime_code_hash",
        "fee_product_economics_hash",
        "intent_fingerprint",
    ):
        object.__setattr__(source, field, getattr(seed, field))
    object.__setattr__(
        source,
        "proof_hash",
        source_module._hash(source_module._proof_payload(source, include_hash=False)),
    )
    return source, _bind_candidate(source)


def _rehash_source_and_candidate(source, candidate):
    object.__setattr__(
        source,
        "proof_hash",
        source_module._hash(source_module._proof_payload(source, include_hash=False)),
    )
    object.__setattr__(candidate, "w85_source_snapshot_hash", source.proof_hash)
    object.__setattr__(
        candidate,
        "proof_hash",
        candidate_module._hash(candidate_module._payload(candidate, include_hash=False)),
    )


def _assert_pre_network_block(monkeypatch, source, candidate, match: str):
    constructed = False

    class MustNotRun:
        def __init__(self, *args, **kwargs):
            nonlocal constructed
            constructed = True
            raise AssertionError("PAPER network gateway constructed after failed W85 preflight")

    monkeypatch.setattr(pipeline_module, "_now_utc", lambda: AT)
    monkeypatch.setattr(pipeline_module, "AlpacaPaperAccountGateway", MustNotRun)
    credentials = AlpacaPaperCredentials("pipeline-preflight-key", "pipeline-preflight-secret")
    with pytest.raises(PaperRuntimeReadOnlyPipelineIntegrityError, match=match):
        collect_paper_runtime_readiness(
            collection_id="pre-network-block",
            source_snapshot=source,
            candidate_identity=candidate,
            credentials=credentials,
            expected_account_id=ACCOUNT_ID,
            trading_config=AlpacaPaperGatewayConfig(enabled=True),
            core_path="/must-not-open.sqlite3",
            market_data_config=AlpacaPaperCryptoMarketDataConfig(enabled=True),
        )
    assert constructed is False


def test_expired_w85_admission_is_rejected_before_first_get(monkeypatch):
    source, candidate = _coherent_source_candidate()
    object.__setattr__(source, "admission_valid_until", AT - timedelta(microseconds=1))
    _rehash_source_and_candidate(source, candidate)
    _assert_pre_network_block(monkeypatch, source, candidate, "expired before network read")


def test_future_dated_w85_snapshot_is_rejected_before_first_get(monkeypatch):
    source, candidate = _coherent_source_candidate()
    object.__setattr__(source, "reproved_at", AT + timedelta(microseconds=1))
    _rehash_source_and_candidate(source, candidate)
    _assert_pre_network_block(monkeypatch, source, candidate, "process future before network read")


def test_suspended_w85_state_is_rejected_before_first_get(monkeypatch):
    source, candidate = _coherent_source_candidate(
        state=lifecycle.PaperCandidateEligibilityState.SUSPENDED
    )
    _assert_pre_network_block(
        monkeypatch,
        source,
        candidate,
        "not ACTIVE/current/read-only/consistent",
    )


def test_validly_rehashed_source_candidate_provenance_drift_is_rejected_before_get(monkeypatch):
    source, candidate = _coherent_source_candidate()
    object.__setattr__(candidate, "intent_fingerprint", "f" * 64)
    object.__setattr__(
        candidate,
        "proof_hash",
        candidate_module._hash(candidate_module._payload(candidate, include_hash=False)),
    )
    _assert_pre_network_block(
        monkeypatch,
        source,
        candidate,
        "candidate/source provenance mismatch before network read",
    )
