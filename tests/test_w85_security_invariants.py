from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta

import pytest

import autotrade.paper_candidate_admission_source_verification as source_verification
import autotrade.paper_candidate_eligibility_final as eligibility_final
from test_w85_final_admission_and_eligibility import (
    _bundle_with_final_admission,
    _rehash_final_verification,
)
from test_w85_paper_candidate_admission import _full_context, _rehash_finalization


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


@pytest.mark.parametrize(
    "field_name",
    (
        "base_resolution",
        "evidence",
        "policy",
        "measurement_plan",
        "binding_evidence",
        "shadow_registry",
        "forward_registry",
    ),
)
def test_w85_source_package_rejects_wrong_typed_parent(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    field_name,
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(
        source_verification.PaperCandidateAdmissionSourceIntegrityError,
        match=field_name,
    ):
        replace(bundle["source_package"], **{field_name: object()})


def test_w85_source_package_rejects_non_tuple_or_wrong_receipt_type(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(
        source_verification.PaperCandidateAdmissionSourceIntegrityError,
        match="measurement_receipts",
    ):
        replace(bundle["source_package"], measurement_receipts=[])
    with pytest.raises(
        source_verification.PaperCandidateAdmissionSourceIntegrityError,
        match="measurement_receipts",
    ):
        replace(bundle["source_package"], measurement_receipts=(object(),))


@pytest.mark.parametrize(
    ("argument", "value", "match"),
    (
        ("finalization", object(), "finalization"),
        ("w83_resolution", object(), "w83_resolution"),
        ("source_package", object(), "source_package"),
    ),
)
def test_w85_source_reproof_rejects_wrong_typed_public_inputs(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    argument,
    value,
    match,
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    kwargs = {
        "proof_id": "w85-wrong-public-input",
        "finalization": bundle["final"],
        "w83_resolution": bundle["w83"],
        "source_package": bundle["source_package"],
        "verified_at": bundle["registered_at"] + timedelta(seconds=1),
    }
    kwargs[argument] = value
    with pytest.raises(TypeError, match=match):
        source_verification.verify_w84_sources_for_candidate_admission(**kwargs)


def test_w85_source_reproof_rejects_naive_decision_clock(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(
        source_verification.PaperCandidateAdmissionSourceIntegrityError,
        match="timezone-aware",
    ):
        source_verification.verify_w84_sources_for_candidate_admission(
            proof_id="w85-naive-decision-clock",
            finalization=bundle["final"],
            w83_resolution=bundle["w83"],
            source_package=bundle["source_package"],
            verified_at=bundle["registered_at"].replace(tzinfo=None),
        )


def test_w85_source_reproof_rejects_decision_clock_before_durable_capture(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(
        source_verification.PaperCandidateAdmissionSourceIntegrityError,
        match="predates durable W84 measurement capture",
    ):
        source_verification.verify_w84_sources_for_candidate_admission(
            proof_id="w85-before-capture",
            finalization=bundle["final"],
            w83_resolution=bundle["w83"],
            source_package=bundle["source_package"],
            verified_at=bundle["final"].measurement_capture_at - timedelta(seconds=1),
        )


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
    ("field_name", "match"),
    (
        ("base_resolution_hash", "base resolution hash"),
        ("evidence_hash", "evidence hash"),
        ("policy_hash", "W84 policy hash"),
        ("w83_resolution_hash", "W83 resolution hash"),
        ("w83_binding_hash", "W83 binding hash"),
        ("measurement_plan_hash", "measurement plan hash"),
        ("measurement_runtime_hash", "measurement runtime hash"),
    ),
)
def test_w85_source_reproof_rejects_identity_drift_between_historical_and_canonical_final(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    field_name,
    match,
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    forged_admission_final = _rehash_finalization(
        bundle["final"],
        **{field_name: "f" * 64},
    )
    with pytest.raises(
        source_verification.PaperCandidateAdmissionSourceIntegrityError,
        match=match,
    ):
        source_verification._require_finalizations_match(
            historical=bundle["final"],
            admission=forged_admission_final,
            source_package=bundle["source_package"],
            w83_resolution=bundle["w83"],
        )


def test_w85_source_proof_payload_rejects_non_datetime_provenance(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, proof = _source_proof(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    values = {
        field.name: getattr(proof, field.name)
        for field in fields(proof)
        if field.name != "proof_hash"
    }
    values["source_capture_at"] = "not-a-datetime"
    with pytest.raises(
        source_verification.PaperCandidateAdmissionSourceIntegrityError,
        match="source_capture_at must be datetime",
    ):
        source_verification._proof_payload_from_values(values)


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


def test_w85_final_eligibility_rejects_wrong_public_types(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admitted, verified, registry = _bundle_with_final_admission(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(TypeError, match="final_verification"):
        eligibility_final.project_final_paper_candidate_eligibility(
            projection_id="w85-wrong-final-type",
            final_verification=object(),
            admission_receipt=admitted,
            lifecycle_registry=registry,
        )
    with pytest.raises(TypeError, match="lifecycle_registry"):
        eligibility_final.project_final_paper_candidate_eligibility(
            projection_id="w85-wrong-lifecycle-type",
            final_verification=verified,
            admission_receipt=admitted,
            lifecycle_registry=object(),
        )


def test_w85_final_eligibility_state_requires_finite_validity():
    with pytest.raises(
        eligibility_final.PaperCandidateFinalEligibilityIntegrityError,
        match="finite admission validity",
    ):
        eligibility_final._state_with_expiry_precedence(
            admission_valid_until=None,
            events=(),
            observed_at=source_verification.datetime.now(
                source_verification.timezone.utc
            ),
        )
