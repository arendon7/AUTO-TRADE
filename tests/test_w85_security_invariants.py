from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

import autotrade.paper_candidate_admission_source_verification as source_verification
import autotrade.paper_candidate_eligibility_final as eligibility_final
from test_w85_final_admission_and_eligibility import (
    _bundle_with_final_admission,
    _rehash_final_verification,
)
from test_w85_paper_candidate_admission import _full_context


def _source_proof(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    verified_at = bundle["registered_at"] + timedelta(seconds=1)
    proof = source_verification.verify_w84_sources_for_candidate_admission(
        proof_id="w85-security-source-proof",
        finalization=bundle["final"],
        w83_resolution=bundle["w83"],
        source_package=bundle["source_package"],
        verified_at=verified_at,
    )
    return bundle, proof


@pytest.mark.parametrize(
    "changes",
    (
        {"canonical_w84_finalization_reproved": False},
        {"historical_finalization_timestamp_trusted_for_freshness": True},
        {"paper_candidate_authorized": True},
        {"paper_execution_authorized": True},
        {"external_execution_authorized": True},
        {"runtime_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
    ),
)
def test_w85_source_proof_constructor_rejects_authority_or_trust_downgrade(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    changes,
):
    _, proof = _source_proof(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(source_verification.PaperCandidateAdmissionSourceIntegrityError):
        replace(proof, **changes)


def test_w85_source_proof_rejects_clock_age_and_hash_tamper(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, proof = _source_proof(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(
        source_verification.PaperCandidateAdmissionSourceIntegrityError,
        match="source age",
    ):
        replace(proof, source_age_seconds=proof.source_age_seconds + 1)
    with pytest.raises(
        source_verification.PaperCandidateAdmissionSourceIntegrityError,
        match="proof hash mismatch",
    ):
        replace(proof, proof_hash="0" * 64)


def test_w85_source_reproof_rejects_incomplete_durable_measurement_package(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    incomplete = replace(bundle["source_package"], measurement_receipts=())
    with pytest.raises(
        source_verification.PaperCandidateAdmissionSourceIntegrityError,
        match="canonical W84 final verification",
    ):
        source_verification.verify_w84_sources_for_candidate_admission(
            proof_id="w85-incomplete-source-package",
            finalization=bundle["final"],
            w83_resolution=bundle["w83"],
            source_package=incomplete,
            verified_at=bundle["registered_at"] + timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"w84_admission_source_proof_bound": False},
        {"historical_w84_timestamp_used_for_freshness": True},
    ),
)
def test_w85_final_admission_constructor_rejects_source_authority_downgrade(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    changes,
):
    _, _, verified, _ = _bundle_with_final_admission(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(Exception):
        replace(verified, **changes)


def test_w85_final_admission_constructor_rejects_source_clock_drift(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, _, verified, _ = _bundle_with_final_admission(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(Exception, match="exact admission-time"):
        replace(
            verified,
            w84_admission_source_verified_at=(
                verified.w84_admission_source_verified_at + timedelta(microseconds=1)
            ),
        )
    with pytest.raises(Exception, match="cannot predate durable W84 source capture"):
        replace(
            verified,
            w84_admission_source_capture_at=verified.admitted_at + timedelta(microseconds=1),
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"w84_admission_source_verification_hash": "f" * 64},
        {"w84_admission_source_capture_at_offset_microseconds": -1},
    ),
)
def test_w85_final_eligibility_rejects_coherently_rehashed_source_provenance_drift(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    changes,
):
    _, admitted, verified, registry = _bundle_with_final_admission(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    if "w84_admission_source_capture_at_offset_microseconds" in changes:
        offset = changes["w84_admission_source_capture_at_offset_microseconds"]
        forged = _rehash_final_verification(
            verified,
            w84_admission_source_capture_at=(
                verified.w84_admission_source_capture_at
                + timedelta(microseconds=offset)
            ),
        )
    else:
        forged = _rehash_final_verification(verified, **changes)

    monkeypatch.setattr(
        eligibility_final,
        "_now_utc",
        lambda: verified.process_verified_at + timedelta(seconds=1),
    )
    with pytest.raises(
        eligibility_final.PaperCandidateFinalEligibilityIntegrityError,
        match="does not match canonical W85 V2 verification",
    ):
        eligibility_final.project_final_paper_candidate_eligibility(
            projection_id="w85-security-provenance-drift",
            final_verification=forged,
            admission_receipt=admitted,
            lifecycle_registry=registry,
        )
