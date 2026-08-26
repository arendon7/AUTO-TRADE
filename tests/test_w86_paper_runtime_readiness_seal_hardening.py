from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.paper_runtime_readiness_seal as seal_module
from autotrade.paper_runtime_readiness_seal import (
    PaperRuntimeReadinessSealBlocker,
    PaperRuntimeReadinessSealIntegrityError,
    PaperRuntimeReadinessSealStatus,
    PaperRuntimeReadinessSealedResult,
    seal_paper_runtime_readiness_after_collection,
)
from test_w86_paper_runtime_readiness_seal import _install_postcheck, _post_proof
from test_w86_paper_runtime_read_only_pipeline import _collect, _source_candidate


def _sealed(monkeypatch):
    result, _, _, _ = _collect(monkeypatch)
    source, candidate = _source_candidate()
    post = _post_proof(source, observed_at=result.funding_capacity.observed_at)
    _install_postcheck(monkeypatch, post)
    sealed = seal_paper_runtime_readiness_after_collection(
        seal_id="w86-seal-hardening",
        pipeline_result=result,
        source_snapshot=source,
        candidate_identity=candidate,
        core_path="/read-only/core.sqlite3",
    )
    return sealed, source, candidate


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("seal_id", "", "canonical identifier"),
        ("strategy_id", "", "canonical identifier"),
        ("product_id", "", "canonical identifier"),
        ("contract_version", "W86_BAD", "version is not canonical"),
        ("pipeline_receipt_hash", "not-a-sha", "lowercase sha256"),
        ("symbol", "", "symbol is required"),
        ("account_id", "", "account_id is required"),
        ("upstream_runtime_ready", 1, "must be bool"),
        ("source_current_state", "ACTIVE", "must be PaperCandidateEligibilityState"),
        ("source_unchanged_after_network", 1, "must be bool"),
        ("post_collection_source_verified", 1, "must be bool"),
        ("blocker_codes", ("BAD",), "canonical W86 seal blockers"),
    ),
)
def test_seal_receipt_rejects_malformed_public_contract_fields(
    monkeypatch, field, value, match
):
    sealed, _, _ = _sealed(monkeypatch)
    with pytest.raises(PaperRuntimeReadinessSealIntegrityError, match=match):
        replace(sealed.seal, **{field: value})


def test_seal_receipt_rejects_non_exact_source_projection(monkeypatch):
    sealed, _, _ = _sealed(monkeypatch)
    with pytest.raises(
        PaperRuntimeReadinessSealIntegrityError,
        match="post-collection source verification flag is not exact projection",
    ):
        replace(sealed.seal, post_collection_source_verified=False)


def test_seal_receipt_rejects_non_exact_blocker_projection(monkeypatch):
    sealed, _, _ = _sealed(monkeypatch)
    with pytest.raises(
        PaperRuntimeReadinessSealIntegrityError,
        match="blockers are not the exact fail-closed projection",
    ):
        replace(
            sealed.seal,
            blocker_codes=(PaperRuntimeReadinessSealBlocker.W85_SOURCE_CHANGED,),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("status", PaperRuntimeReadinessSealStatus.BLOCKED),
        ("paper_runtime_ready", False),
    ),
)
def test_seal_receipt_rejects_status_readiness_drift(monkeypatch, field, value):
    sealed, _, _ = _sealed(monkeypatch)
    with pytest.raises(
        PaperRuntimeReadinessSealIntegrityError,
        match="status/readiness disagrees",
    ):
        replace(sealed.seal, **{field: value})


def test_seal_receipt_rejects_non_exact_finite_ttl(monkeypatch):
    sealed, _, _ = _sealed(monkeypatch)
    with pytest.raises(
        PaperRuntimeReadinessSealIntegrityError,
        match="valid_until is not the exact finite",
    ):
        replace(sealed.seal, valid_until=sealed.seal.valid_until + timedelta(seconds=1))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("upstream_pipeline_integrity_verified", False),
        ("separate_execution_approval_required", False),
        ("broker_write_performed", True),
        ("capital_reserved", True),
        ("external_execution_authorized", True),
        ("runtime_execution_authorized", True),
        ("capital_authority", "BROKER"),
        ("live_trading", "ENABLED"),
    ),
)
def test_seal_receipt_rejects_every_authority_escalation(monkeypatch, field, value):
    sealed, _, _ = _sealed(monkeypatch)
    with pytest.raises(PaperRuntimeReadinessSealIntegrityError, match="may not grant"):
        replace(sealed.seal, **{field: value})


def test_seal_to_dict_is_hash_bound_and_non_authorizing(monkeypatch):
    sealed, _, _ = _sealed(monkeypatch)
    payload = sealed.seal.to_dict()
    assert payload["receipt_hash"] == sealed.seal.receipt_hash
    assert payload["paper_runtime_ready"] is True
    assert payload["paper_execution_authorized"] is False
    assert payload["external_execution_authorized"] is False
    assert payload["runtime_execution_authorized"] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["live_trading"] == "BLOCKED"


@pytest.mark.parametrize(
    "field",
    (
        "pipeline_receipt_hash",
        "funding_capacity_hash",
        "post_collection_source_hash",
        "source_snapshot_hash",
        "candidate_identity_hash",
    ),
)
def test_sealed_result_rejects_each_cross_evidence_binding_tamper(monkeypatch, field):
    sealed, _, _ = _sealed(monkeypatch)
    tampered_seal = replace(sealed.seal)
    object.__setattr__(tampered_seal, field, "0" * 64)
    with pytest.raises(PaperRuntimeReadinessSealIntegrityError, match="bind|binding"):
        PaperRuntimeReadinessSealedResult(
            pipeline=sealed.pipeline,
            post_collection_source=sealed.post_collection_source,
            seal=tampered_seal,
        )


def test_expected_blockers_cover_all_fail_closed_dimensions(monkeypatch):
    sealed, source, _ = _sealed(monkeypatch)
    observed = sealed.seal.observed_at
    blockers = seal_module._expected_blockers(
        upstream_runtime_ready=False,
        upstream_funding_valid_until=observed - timedelta(microseconds=1),
        source_unchanged=False,
        source_current_state=lifecycle.PaperCandidateEligibilityState.SUSPENDED,
        source_admission_valid_until=observed - timedelta(microseconds=1),
        observed_at=observed,
    )
    assert blockers == (
        PaperRuntimeReadinessSealBlocker.UPSTREAM_RUNTIME_NOT_READY,
        PaperRuntimeReadinessSealBlocker.UPSTREAM_RUNTIME_EXPIRED,
        PaperRuntimeReadinessSealBlocker.W85_SOURCE_CHANGED,
        PaperRuntimeReadinessSealBlocker.W85_CANDIDATE_NOT_ACTIVE,
        PaperRuntimeReadinessSealBlocker.W85_ADMISSION_EXPIRED,
    )
    assert source.admission_valid_until >= observed


@pytest.mark.parametrize(
    ("pipeline", "source", "candidate", "match"),
    (
        (None, "SOURCE", "CANDIDATE", "pipeline_result"),
        ("PIPELINE", None, "CANDIDATE", "source_snapshot"),
        ("PIPELINE", "SOURCE", None, "candidate_identity"),
    ),
)
def test_validate_upstream_rejects_wrong_public_types(
    monkeypatch, pipeline, source, candidate, match
):
    sealed, real_source, real_candidate = _sealed(monkeypatch)
    real_pipeline = sealed.pipeline
    pipeline = real_pipeline if pipeline == "PIPELINE" else pipeline
    source = real_source if source == "SOURCE" else source
    candidate = real_candidate if candidate == "CANDIDATE" else candidate
    with pytest.raises(TypeError, match=match):
        seal_module._validate_upstream(pipeline, source, candidate)
