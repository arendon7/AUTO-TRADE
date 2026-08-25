from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta

import pytest

import autotrade.fee_product_economics as product_module
import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.paper_candidate_eligibility_final as eligibility_final
import autotrade.paper_runtime_candidate_identity as identity
import autotrade.paper_runtime_readiness_source_snapshot as snapshot
import autotrade.promotion_strategy_version_binding as w83_module
from test_w86_w85_durable_eligibility_source import _active_source_bundle


def _identity_bundle(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, registry, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    monkeypatch.setattr(snapshot, "_now_utc", lambda: reproved_at + timedelta(seconds=1))
    source_proof = snapshot.W85DurableEligibilitySnapshotReader(bundle["core"]).verify_current(
        proof_id="w86-identity-source",
        eligibility=eligibility,
        final_verification=verified,
    )
    product = bundle["ctx"]["chain"]["product"]
    return bundle, admitted, verified, registry, eligibility, source_proof, product


def _rehash_w83(value, **changes):
    values = {
        field.name: getattr(value, field.name)
        for field in fields(value)
        if field.name != "resolution_hash"
    }
    values.update(changes)
    return type(value)(
        **values,
        resolution_hash=w83_module._hash(w83_module._payload_from_values(values)),
    )


def _rehash_product(value, **changes):
    values = {
        field.name: getattr(value, field.name)
        for field in fields(value)
        if field.name != "evidence_hash"
    }
    values.update(changes)
    return type(value)(
        **values,
        evidence_hash=product_module._hash(
            product_module._evidence_payload_from_values(values)
        ),
    )


def test_w86_identity_derives_product_fields_from_exact_w82_w83_chain(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, _, _, source_proof, product = _identity_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    proof = identity.bind_paper_runtime_candidate_identity(
        proof_id="w86-runtime-identity",
        source_proof=source_proof,
        final_verification=verified,
        w83_resolution=bundle["w83"],
        product_economics=product,
    )

    assert proof.w85_source_snapshot_hash == source_proof.proof_hash
    assert proof.authority_key == admitted.authority_key
    assert proof.admission_hash == admitted.admission_hash
    assert proof.w83_resolution_hash == bundle["w83"].resolution_hash
    assert proof.w83_binding_hash == bundle["w83"].binding_evidence_hash
    assert proof.selected_strategy_id == bundle["w83"].selected_strategy_id
    assert proof.selected_strategy_version == bundle["w83"].selected_strategy_version
    assert proof.strategy_spec_hash == source_proof.strategy_spec_hash
    assert proof.loaded_runtime_code_hash == source_proof.loaded_runtime_code_hash
    assert proof.fee_product_economics_hash == product.evidence_hash
    assert proof.intent_fingerprint == product.intent_fingerprint
    assert proof.product_id == product.product_id
    assert proof.asset_class == product.asset_class
    assert proof.venue == product.venue
    assert proof.symbol == product.symbol
    assert proof.side == product.side.value
    assert proof.base_currency == product.base_currency
    assert proof.quote_currency == product.quote_currency
    assert proof.product_identity_verified is True
    assert proof.strategy_runtime_identity_verified is True
    assert proof.paper_execution_authorized is False
    assert proof.external_execution_authorized is False
    assert proof.runtime_execution_authorized is False
    assert proof.capital_authority == "NONE"
    assert proof.live_trading == "BLOCKED"
    assert proof.to_dict()["proof_hash"] == proof.proof_hash


def test_w86_identity_rejects_valid_rehashed_w83_with_different_resolution_identity(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, verified, _, _, source_proof, product = _identity_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    forged = _rehash_w83(
        bundle["w83"],
        resolution_id="w83-valid-but-not-admitted",
    )
    assert forged.resolution_hash != bundle["w83"].resolution_hash
    with pytest.raises(
        identity.PaperRuntimeCandidateIdentityIntegrityError,
        match="exact supplied W83 resolution",
    ):
        identity.bind_paper_runtime_candidate_identity(
            proof_id="w86-other-w83",
            source_proof=source_proof,
            final_verification=verified,
            w83_resolution=forged,
            product_economics=product,
        )


def test_w86_identity_rejects_valid_rehashed_product_not_bound_by_w83(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, verified, _, _, source_proof, product = _identity_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    forged = _rehash_product(product, product_id="different-product")
    assert forged.evidence_hash != product.evidence_hash
    with pytest.raises(
        identity.PaperRuntimeCandidateIdentityIntegrityError,
        match="exact supplied W82 product economics",
    ):
        identity.bind_paper_runtime_candidate_identity(
            proof_id="w86-other-product",
            source_proof=source_proof,
            final_verification=verified,
            w83_resolution=bundle["w83"],
            product_economics=forged,
        )


def test_w86_identity_rejects_suspended_candidate_even_with_valid_product_chain(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, registry, _, source_proof, product = _identity_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    suspend_at = source_proof.reproved_at + timedelta(seconds=1)
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: suspend_at)
    registry.append(
        event_id="w86-identity-suspend",
        admission_receipt=admitted,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="RUNTIME_IDENTITY_REVIEW",
    )
    eligibility_at = suspend_at + timedelta(seconds=1)
    monkeypatch.setattr(eligibility_final, "_now_utc", lambda: eligibility_at)
    suspended = eligibility_final.project_final_paper_candidate_eligibility(
        projection_id="w86-identity-suspended",
        final_verification=verified,
        admission_receipt=admitted,
        lifecycle_registry=registry,
    )
    monkeypatch.setattr(snapshot, "_now_utc", lambda: eligibility_at + timedelta(seconds=1))
    suspended_source = snapshot.W85DurableEligibilitySnapshotReader(bundle["core"]).verify_current(
        proof_id="w86-identity-suspended-source",
        eligibility=suspended,
        final_verification=verified,
    )
    assert suspended_source.current_state is lifecycle.PaperCandidateEligibilityState.SUSPENDED
    with pytest.raises(
        identity.PaperRuntimeCandidateIdentityIntegrityError,
        match="current ACTIVE W85 candidate",
    ):
        identity.bind_paper_runtime_candidate_identity(
            proof_id="w86-suspended-identity",
            source_proof=suspended_source,
            final_verification=verified,
            w83_resolution=bundle["w83"],
            product_economics=product,
        )


def test_w86_identity_rejects_w82_product_authority_escalation_even_if_rehashed(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, verified, _, _, source_proof, product = _identity_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    forged = object.__new__(type(product))
    values = {
        field.name: getattr(product, field.name)
        for field in fields(product)
        if field.name != "evidence_hash"
    }
    values["external_execution_authorized"] = True
    forged_hash = product_module._hash(product_module._evidence_payload_from_values(values))
    for field in fields(product):
        object.__setattr__(
            forged,
            field.name,
            forged_hash if field.name == "evidence_hash" else values[field.name],
        )
    with pytest.raises(
        identity.PaperRuntimeCandidateIdentityIntegrityError,
        match="authority/no-claims boundary",
    ):
        identity._validate_product(forged)


@pytest.mark.parametrize(
    "changes",
    (
        {"paper_execution_authorized": True},
        {"external_execution_authorized": True},
        {"runtime_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"product_identity_verified": False},
        {"strategy_runtime_identity_verified": False},
        {"side": "SHORT"},
        {"base_currency": "usd"},
    ),
)
def test_w86_identity_proof_constructor_rejects_authority_or_identity_downgrade(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    changes,
):
    bundle, _, verified, _, _, source_proof, product = _identity_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    proof = identity.bind_paper_runtime_candidate_identity(
        proof_id="w86-identity-guard",
        source_proof=source_proof,
        final_verification=verified,
        w83_resolution=bundle["w83"],
        product_economics=product,
    )
    with pytest.raises(identity.PaperRuntimeCandidateIdentityIntegrityError):
        replace(proof, **changes)


def test_w86_identity_proof_rejects_hash_tamper(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, verified, _, _, source_proof, product = _identity_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    proof = identity.bind_paper_runtime_candidate_identity(
        proof_id="w86-identity-hash",
        source_proof=source_proof,
        final_verification=verified,
        w83_resolution=bundle["w83"],
        product_economics=product,
    )
    with pytest.raises(
        identity.PaperRuntimeCandidateIdentityIntegrityError,
        match="proof hash mismatch",
    ):
        replace(proof, proof_hash="f" * 64)


def test_w86_identity_type_guards(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, verified, _, _, source_proof, product = _identity_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    cases = (
        {"source_proof": object()},
        {"final_verification": object()},
        {"w83_resolution": object()},
        {"product_economics": object()},
    )
    base = {
        "proof_id": "w86-type-guard",
        "source_proof": source_proof,
        "final_verification": verified,
        "w83_resolution": bundle["w83"],
        "product_economics": product,
    }
    for changes in cases:
        with pytest.raises(TypeError):
            identity.bind_paper_runtime_candidate_identity(**{**base, **changes})
