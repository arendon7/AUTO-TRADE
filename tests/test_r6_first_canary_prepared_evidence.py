from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.first_canary_prepared_evidence import (
    FirstCanaryPreparedEvidence,
    FirstCanaryPreparedEvidenceIntegrityError,
)
from autotrade.domain import risk_decision_fingerprint
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


def test_prepared_evidence_rejects_credential_persistence_policy_drift_even_with_rehashed_document() -> None:
    original = _evidence()
    document = original.document()
    material = deepcopy(document)
    material.pop("prepared_evidence_hash")
    material["secret_persisted"] = True

    from hashlib import sha256
    import json

    material["prepared_evidence_hash"] = sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(
        FirstCanaryPreparedEvidenceIntegrityError,
        match="Secret persistence policy",
    ):
        FirstCanaryPreparedEvidence.from_document(material)


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
