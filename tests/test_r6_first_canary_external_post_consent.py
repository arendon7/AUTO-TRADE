from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.first_canary_external_post_consent import (
    CONSENT_FILENAME,
    FirstCanaryExternalPostConsentBlocked,
    consume_external_post_consent,
    external_post_challenge,
    require_fresh_external_post_consent,
)
from autotrade.first_canary_prepared_evidence import FirstCanaryPreparedEvidence
from test_r6_first_canary_execution_gate import NOW, _prepare_session


def _restart_safe(session):
    evidence = FirstCanaryPreparedEvidence(
        account=session.account,
        asset=session.asset,
        product_profile=session.product_profile,
        market=session.market_attestation,
        risk_decision=session.risk_decision,
    ).document()
    document = {
        "schema_version": 1,
        "document_type": "R6_CRYPTO_PAPER_FIRST_CANARY_RESTART_SAFE_PREPARATION",
        "attempt_id": session.attempt.attempt_id,
        "package_hash": session.preparation.package.package_hash,
        "client_order_id": session.preparation.package.client_order_id,
        "preparation_hash": session.preparation_document["preparation_hash"],
        "authority_state_fingerprint": session.authority_state_fingerprint,
        "credential_reference": session.credentials.credential_reference,
        "prepared_evidence": evidence,
        "prepared_evidence_hash": evidence["prepared_evidence_hash"],
        "created_at": (NOW + timedelta(seconds=4)).isoformat(),
        "credentials_persisted": False,
        "secret_persisted": False,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "live_trading": "BLOCKED",
    }
    document["restart_safe_hash"] = session.attempt.document_hash(
        document,
        hash_key="restart_safe_hash",
    )
    return document


def _challenge(session) -> str:
    package = session.preparation.package
    return external_post_challenge(
        attempt_id=session.attempt.attempt_id,
        client_order_id=package.client_order_id,
        notional=package.notional,
    )


def _receipt(tmp_path, monkeypatch):
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    now = NOW + timedelta(seconds=4, milliseconds=200)
    receipt = consume_external_post_consent(
        attempt=session.attempt,
        preparation=session.preparation_document,
        restart_safe=_restart_safe(session),
        confirmation=_challenge(session),
        now=now,
    )
    return session, receipt, now


def test_exact_external_post_consent_is_durable_bounded_and_secret_free(tmp_path, monkeypatch) -> None:
    session, receipt, now = _receipt(tmp_path, monkeypatch)

    assert receipt.symbol == "BTC/USD"
    assert Decimal("1") <= receipt.notional <= Decimal("5")
    assert receipt.challenge == _challenge(session)
    require_fresh_external_post_consent(receipt=receipt, now=now + timedelta(seconds=1))
    durable = session.attempt.read(path=session.attempt.attempt_root / CONSENT_FILENAME)
    assert durable["exact_paper_post_authorized"] is True
    assert durable["one_shot"] is True
    assert durable["retry_authorized"] is False
    assert durable["credentials_persisted"] is False
    assert durable["secret_persisted"] is False
    assert durable["live_trading"] == "BLOCKED"
    serialized = str(durable).lower()
    assert "simulation-paper-secret" not in serialized
    assert "apca_api_secret_key" not in serialized


def test_external_post_consent_requires_exact_human_challenge(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    with pytest.raises(FirstCanaryExternalPostConsentBlocked, match="exact external PAPER POST confirmation"):
        consume_external_post_consent(
            attempt=session.attempt,
            preparation=session.preparation_document,
            restart_safe=_restart_safe(session),
            confirmation="EXECUTE",
            now=NOW + timedelta(seconds=4, milliseconds=200),
        )
    assert (session.attempt.attempt_root / CONSENT_FILENAME).exists() is False


def test_external_post_consent_is_burned_before_any_possible_replay(tmp_path, monkeypatch) -> None:
    session, _, now = _receipt(tmp_path, monkeypatch)
    restart = _restart_safe(session)

    with pytest.raises(FirstCanaryExternalPostConsentBlocked, match="already consumed"):
        consume_external_post_consent(
            attempt=session.attempt,
            preparation=session.preparation_document,
            restart_safe=restart,
            confirmation=_challenge(session),
            now=now + timedelta(milliseconds=10),
        )


def test_external_post_consent_rejects_tampered_restart_safe_evidence(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    restart = _restart_safe(session)
    restart["client_order_id"] = "tampered-client-order-id"

    with pytest.raises(Exception, match="restart-safe preparation hash mismatch"):
        consume_external_post_consent(
            attempt=session.attempt,
            preparation=session.preparation_document,
            restart_safe=restart,
            confirmation=_challenge(session),
            now=NOW + timedelta(seconds=4, milliseconds=200),
        )


def test_external_post_consent_rejects_expired_prepared_package(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    deadline = session.preparation.package.execution_deadline
    with pytest.raises(FirstCanaryExternalPostConsentBlocked, match="expired"):
        consume_external_post_consent(
            attempt=session.attempt,
            preparation=session.preparation_document,
            restart_safe=_restart_safe(session),
            confirmation=_challenge(session),
            now=deadline + timedelta(milliseconds=1),
        )


def test_external_post_consent_expiry_never_becomes_retry_permission(tmp_path, monkeypatch) -> None:
    _, receipt, _ = _receipt(tmp_path, monkeypatch)
    with pytest.raises(FirstCanaryExternalPostConsentBlocked, match="new attempt rather than retry POST"):
        require_fresh_external_post_consent(
            receipt=receipt,
            now=receipt.expires_at,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"package_hash": "NOT-A-SHA256"}, "package_hash must be lowercase SHA-256"),
        ({"symbol": "ETH/USD"}, "BTC/USD only"),
        ({"notional": Decimal("0.99")}, "within USD 1-5"),
        ({"notional": Decimal("NaN")}, "finite Decimal"),
        ({"source_host": "api.alpaca.markets"}, "exact Alpaca PAPER host"),
        ({"source_path": "/v2/positions"}, "exact crypto order path"),
        ({"receipt_hash": "0" * 64}, "consent hash mismatch"),
    ],
)
def test_external_post_consent_receipt_rejects_rebinding(tmp_path, monkeypatch, changes, message) -> None:
    _, receipt, _ = _receipt(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=message):
        replace(receipt, **changes)


def test_external_post_consent_receipt_rejects_invalid_expiry_windows(tmp_path, monkeypatch) -> None:
    _, receipt, _ = _receipt(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="expiry must follow"):
        replace(receipt, expires_at=receipt.consented_at)
    with pytest.raises(ValueError, match="exceeds ten-second"):
        replace(receipt, expires_at=receipt.consented_at + timedelta(seconds=11))


def test_external_post_challenge_rejects_non_decimal_and_nonfinite_notional() -> None:
    with pytest.raises(ValueError, match="finite Decimal"):
        external_post_challenge(
            attempt_id="first-canary-0123456789abcdef0123456789abcdef",
            client_order_id="r6-first-canary-test",
            notional=2,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="finite Decimal"):
        external_post_challenge(
            attempt_id="first-canary-0123456789abcdef0123456789abcdef",
            client_order_id="r6-first-canary-test",
            notional=Decimal("Infinity"),
        )


def test_external_post_consent_rejects_restart_safe_policy_drift(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    for key, value, message in (
        ("document_type", "WRONG_TYPE", "evidence type is invalid"),
        ("credentials_persisted", True, "credential persistence policy"),
        ("live_trading", "ENABLED", "does not preserve LIVE deny"),
        ("external_post_authorized", True, "must not already authorize external POST"),
    ):
        restart = _restart_safe(session)
        restart[key] = value
        restart["restart_safe_hash"] = session.attempt.document_hash(
            restart,
            hash_key="restart_safe_hash",
        )
        with pytest.raises(FirstCanaryExternalPostConsentBlocked, match=message):
            consume_external_post_consent(
                attempt=session.attempt,
                preparation=session.preparation_document,
                restart_safe=restart,
                confirmation=_challenge(session),
                now=NOW + timedelta(seconds=4, milliseconds=200),
            )
