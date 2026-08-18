from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from autotrade.first_canary_prepared_evidence import (
    FirstCanaryPreparedEvidence,
    FirstCanaryPreparedEvidenceIntegrityError,
)
import autotrade.first_canary_prepared_evidence as prepared_evidence
from autotrade.domain import market_fingerprint, risk_decision_fingerprint
from autotrade.product_profile import ProductCapabilities
from test_r6_paper_crypto_canary_coordinator import (
    NOW,
    _account,
    _asset,
    _decision,
    _intent,
    _market,
)


def _evidence() -> FirstCanaryPreparedEvidence:
    account = _account(observed=NOW + timedelta(seconds=1))
    asset = _asset(account, observed=NOW + timedelta(seconds=1))
    profile = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )
    market = _market(observed=NOW + timedelta(seconds=1))
    intent = _intent(quantity=Decimal("0.0001"), limit_price=Decimal("20000"))
    decision = _decision(
        intent,
        market,
        approved_notional=Decimal("2"),
        valid_until=NOW + timedelta(seconds=20),
    )
    return FirstCanaryPreparedEvidence(
        account=account,
        asset=asset,
        product_profile=profile,
        market=market,
        risk_decision=decision,
    )


def _rehash(document: dict[str, object]) -> dict[str, object]:
    material = deepcopy(document)
    material.pop("prepared_evidence_hash", None)
    material["prepared_evidence_hash"] = sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return material


def test_prepared_evidence_round_trip_preserves_exact_authority_fingerprints() -> None:
    original = _evidence()
    document = original.document()
    restored = FirstCanaryPreparedEvidence.from_document(document)

    assert restored.account == original.account
    assert restored.asset == original.asset
    assert restored.product_profile == original.product_profile
    assert restored.market == original.market
    assert restored.risk_decision == original.risk_decision
    assert restored.fingerprint == original.fingerprint
    assert restored.account.fingerprint == document["account_fingerprint"]
    assert restored.asset.fingerprint == document["asset_fingerprint"]
    assert restored.product_profile.fingerprint == document["product_profile_fingerprint"]
    assert restored.market.fingerprint == document["market_attestation_fingerprint"]
    assert market_fingerprint(restored.market.market) == restored.risk_decision.market_fingerprint
    assert risk_decision_fingerprint(restored.risk_decision) == document["risk_decision_fingerprint"]
    assert document["credentials_persisted"] is False
    assert document["secret_persisted"] is False
    assert document["live_trading"] == "BLOCKED"


def test_prepared_evidence_tamper_is_rejected_by_document_hash() -> None:
    document = _evidence().document()
    tampered = deepcopy(document)
    tampered["account"]["buying_power"] = "999999"  # type: ignore[index]

    with pytest.raises(
        FirstCanaryPreparedEvidenceIntegrityError,
        match="document hash mismatch",
    ):
        FirstCanaryPreparedEvidence.from_document(tampered)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("schema_version", 999, "schema version"),
        ("document_type", "OTHER", "document type"),
        ("credentials_persisted", True, "credential persistence policy"),
        ("secret_persisted", True, "Secret persistence policy"),
        ("live_trading", "ENABLED", "LIVE deny"),
    ],
)
def test_prepared_evidence_rejects_top_level_policy_drift(
    field: str,
    value: object,
    match: str,
) -> None:
    document = _evidence().document()
    document[field] = value
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match=match):
        FirstCanaryPreparedEvidence.from_document(document)


def test_prepared_evidence_rejects_non_mapping_root() -> None:
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="root must be an object"):
        FirstCanaryPreparedEvidence.from_document([])  # type: ignore[arg-type]


def test_prepared_evidence_rejects_credential_persistence_policy_drift_even_with_rehashed_document() -> None:
    document = _evidence().document()
    document["secret_persisted"] = True
    with pytest.raises(
        FirstCanaryPreparedEvidenceIntegrityError,
        match="Secret persistence policy",
    ):
        FirstCanaryPreparedEvidence.from_document(_rehash(document))


@pytest.mark.parametrize(
    ("anchor", "match"),
    [
        ("account_fingerprint", "account_fingerprint mismatch"),
        ("asset_fingerprint", "asset_fingerprint mismatch"),
        ("product_profile_fingerprint", "product_profile_fingerprint mismatch"),
        ("market_attestation_fingerprint", "market_attestation_fingerprint mismatch"),
        ("risk_decision_fingerprint", "risk_decision_fingerprint mismatch"),
    ],
)
def test_prepared_evidence_rejects_rehashed_fingerprint_anchor_drift(
    anchor: str,
    match: str,
) -> None:
    document = _evidence().document()
    document[anchor] = "a" * 64
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match=match):
        FirstCanaryPreparedEvidence.from_document(_rehash(document))


def test_prepared_evidence_rejects_noncanonical_product_profile_even_when_rehashed() -> None:
    document = _evidence().document()
    profile = document["product_profile"]
    assert isinstance(profile, dict)
    profile["venue"] = "TAMPERED"
    with pytest.raises(
        FirstCanaryPreparedEvidenceIntegrityError,
        match="ProductCapabilities payload is non-canonical",
    ):
        FirstCanaryPreparedEvidence.from_document(_rehash(document))


def test_prepared_evidence_constructor_rejects_cross_account_asset() -> None:
    value = _evidence()
    other_account = _account(observed=NOW + timedelta(seconds=2))
    bad_asset = replace(
        value.asset,
        account_attestation_fingerprint=other_account.fingerprint,
    )

    with pytest.raises(
        FirstCanaryPreparedEvidenceIntegrityError,
        match="prepared account",
    ):
        FirstCanaryPreparedEvidence(
            account=value.account,
            asset=bad_asset,
            product_profile=value.product_profile,
            market=value.market,
            risk_decision=value.risk_decision,
        )


def test_prepared_evidence_constructor_rejects_cross_credential_asset() -> None:
    value = _evidence()
    bad_asset = replace(value.asset, credential_reference="b" * 64)
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="credential provenance"):
        FirstCanaryPreparedEvidence(
            account=value.account,
            asset=bad_asset,
            product_profile=value.product_profile,
            market=value.market,
            risk_decision=value.risk_decision,
        )


def test_prepared_evidence_constructor_rejects_unbound_product_profile() -> None:
    value = _evidence()
    bad_profile = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint="c" * 64,
        observed_at=value.asset.observed_at,
        fractionable=value.asset.fractionable,
        marginable=value.asset.marginable,
        shortable=value.asset.shortable,
    )
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="prepared asset"):
        FirstCanaryPreparedEvidence(
            account=value.account,
            asset=value.asset,
            product_profile=bad_profile,
            market=value.market,
            risk_decision=value.risk_decision,
        )


def test_prepared_evidence_constructor_rejects_market_symbol_drift() -> None:
    value = _evidence()
    bad_market = replace(value.market, market=replace(value.market.market, symbol="ETH/USD"))
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="market symbol"):
        FirstCanaryPreparedEvidence(
            account=value.account,
            asset=value.asset,
            product_profile=value.product_profile,
            market=bad_market,
            risk_decision=value.risk_decision,
        )


def test_prepared_evidence_constructor_rejects_risk_market_drift() -> None:
    value = _evidence()
    bad_decision = replace(value.risk_decision, market_fingerprint="d" * 64)
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="RiskDecision"):
        FirstCanaryPreparedEvidence(
            account=value.account,
            asset=value.asset,
            product_profile=value.product_profile,
            market=value.market,
            risk_decision=bad_decision,
        )


@pytest.mark.parametrize(
    ("section", "mutation", "match"),
    [
        ("account", ("buying_power", "NaN"), "account payload is invalid"),
        ("asset", ("min_order_size", "NaN"), "asset payload is invalid"),
        ("market", ("location", ""), "location is missing or invalid"),
        ("risk_decision", ("status", "NOT_A_STATUS"), "RiskDecision payload is invalid"),
    ],
)
def test_prepared_evidence_rejects_rehashed_malformed_typed_sections(
    section: str,
    mutation: tuple[str, object],
    match: str,
) -> None:
    document = _evidence().document()
    target = document[section]
    assert isinstance(target, dict)
    key, value = mutation
    target[key] = value
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match=match):
        FirstCanaryPreparedEvidence.from_document(_rehash(document))


def test_prepared_evidence_rejects_missing_nested_object() -> None:
    document = _evidence().document()
    document["market"] = "not-an-object"
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="market must be an object"):
        FirstCanaryPreparedEvidence.from_document(_rehash(document))


def test_prepared_evidence_scalar_parser_fail_closed_edges() -> None:
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="x is missing or invalid"):
        prepared_evidence._text({"x": ""}, "x")
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="must be lowercase SHA-256"):
        prepared_evidence._required_hash({"x": "NOT-A-HASH"}, "x")
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="must be boolean"):
        prepared_evidence._bool({"x": 1}, "x")
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="must be integer"):
        prepared_evidence._integer({"x": True}, "x")
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="decimal string"):
        prepared_evidence._decimal_value(1, "x")
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="x is invalid"):
        prepared_evidence._decimal_value("not-decimal", "x")
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="must be finite"):
        prepared_evidence._decimal_value("Infinity", "x")


def test_prepared_evidence_datetime_and_canonical_scalar_edges() -> None:
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="invalid datetime"):
        prepared_evidence._datetime({"x": "not-a-date"}, "x")
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="timezone-aware"):
        prepared_evidence._datetime({"x": "2026-08-18T10:00:00"}, "x")
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="timezone-aware"):
        prepared_evidence._time_text(datetime(2026, 8, 18, 10, 0, 0))
    with pytest.raises(FirstCanaryPreparedEvidenceIntegrityError, match="decimal must be finite"):
        prepared_evidence._decimal_text(Decimal("NaN"))
    assert prepared_evidence._decimal({"x": "2.500"}, "x") == Decimal("2.500")
    assert len(prepared_evidence._hash({"b": 2, "a": 1})) == 64
