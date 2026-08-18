from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pytest

from autotrade.first_canary_external_post_consent import (
    FirstCanaryExternalPostConsentBlocked,
    external_post_challenge,
)
from autotrade.first_canary_prepared_evidence import FirstCanaryPreparedEvidence
from autotrade.first_canary_real_paper_execution import (
    FirstCanaryRealPaperExecutionBlocked,
    collect_fresh_final_evidence,
    execute_real_paper_first_canary_once,
    load_restart_safe_execution_inputs,
)
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    CryptoFirstCanaryAttemptConflict,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
import autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io as cold_pre_io
from test_r6_first_canary_execution_gate import (
    CREDENTIAL_REFERENCE,
    KEY_ID,
    NOW,
    SECRET,
    _CountingSimulationDelegate,
    _FoundReconciler,
    _final,
    _prepare_session,
)


EVIDENCE_FILENAME = "prepared_evidence.json"


def _persist_restart_safe(session) -> dict[str, object]:
    evidence = FirstCanaryPreparedEvidence(
        account=session.account,
        asset=session.asset,
        product_profile=session.product_profile,
        market=session.market_attestation,
        risk_decision=session.risk_decision,
    ).document()
    document: dict[str, object] = {
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
    session.attempt.write_once(
        path=session.attempt.attempt_root / EVIDENCE_FILENAME,
        document=document,
    )
    return document


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(key_id=KEY_ID, secret_key=SECRET)


def _post_challenge(session) -> str:
    package = session.preparation.package
    return external_post_challenge(
        attempt_id=session.attempt.attempt_id,
        client_order_id=package.client_order_id,
        notional=package.notional,
    )


class _AccountGateway:
    def __init__(self, value) -> None:
        self.value = value
        self.calls = 0

    def attest_account(self, **kwargs):
        self.calls += 1
        assert kwargs["expected_account_id"] == self.value.account_id
        return self.value


class _AssetGateway:
    def __init__(self, value) -> None:
        self.value = value
        self.calls = 0

    def attest_asset(self, **kwargs):
        self.calls += 1
        assert kwargs["symbol"] == "BTC/USD"
        assert kwargs["expected_credential_reference"] == self.value.credential_reference
        return self.value


class _FlatGateway:
    def __init__(self, value) -> None:
        self.value = value
        self.calls = 0

    def attest_flatness(self, **kwargs):
        self.calls += 1
        assert kwargs["expected_credential_reference"] == self.value.credential_reference
        return self.value


class _MarketGateway:
    def __init__(self, value) -> None:
        self.value = value
        self.calls = 0

    def attest_snapshot(self, **kwargs):
        self.calls += 1
        assert kwargs["symbol"] == "BTC/USD"
        return self.value


def test_restart_safe_loader_reconstructs_exact_typed_execution_inputs(tmp_path, monkeypatch) -> None:
    _, session, original = _prepare_session(tmp_path, monkeypatch)
    _persist_restart_safe(session)

    attempt, loaded, preparation, restart = load_restart_safe_execution_inputs(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=_credentials(),
    )

    assert attempt.attempt_id == session.attempt.attempt_id
    assert loaded.package == original.package
    assert loaded.broker_order == original.broker_order
    assert loaded.prepared_account == original.prepared_account
    assert loaded.prepared_asset == original.prepared_asset
    assert loaded.prepared_product_profile == original.prepared_product_profile
    assert loaded.risk_decision == original.risk_decision
    assert preparation["preparation_hash"] == session.preparation_document["preparation_hash"]
    assert restart["credential_reference"] == CREDENTIAL_REFERENCE


def test_real_paper_wrapper_crosses_injected_delegate_exactly_once_after_second_consent(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    _persist_restart_safe(session)
    execute_at = NOW + timedelta(seconds=4, milliseconds=300)
    monkeypatch.setattr(
        cold_pre_io,
        "_utc_now",
        lambda: execute_at + timedelta(milliseconds=35),
    )
    delegate = _CountingSimulationDelegate()
    reconciler = _FoundReconciler()

    consent, outcome = execute_real_paper_first_canary_once(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=_credentials(),
        confirmation=_post_challenge(session),
        now=execute_at,
        final_evidence=_final(inputs, at=execute_at),
        delegate=delegate,
        reconciler=reconciler,
    )

    assert consent.attempt_id == session.attempt.attempt_id
    assert consent.package_hash == session.preparation.package.package_hash
    assert delegate.calls == 1
    assert reconciler.calls == 1
    assert outcome.retry_forbidden is True
    assert outcome.status == "RECONCILED_FINAL"
    durable_consent = session.attempt.read(
        path=session.attempt.attempt_root / "external_post_consent.json"
    )
    started = session.attempt.read(path=session.attempt.execution_started_path)
    assert durable_consent["exact_paper_post_authorized"] is True
    assert durable_consent["retry_authorized"] is False
    assert started["retry_forbidden"] is True

    with pytest.raises(CryptoFirstCanaryAttemptConflict, match="POST replay is forbidden"):
        execute_real_paper_first_canary_once(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=_credentials(),
            confirmation=_post_challenge(session),
            now=execute_at + timedelta(seconds=1),
            final_evidence=_final(inputs, at=execute_at + timedelta(seconds=1)),
            delegate=_CountingSimulationDelegate(),
            reconciler=_FoundReconciler(),
        )


def test_wrong_second_consent_never_crosses_delegate(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    _persist_restart_safe(session)
    execute_at = NOW + timedelta(seconds=4, milliseconds=300)
    delegate = _CountingSimulationDelegate()

    with pytest.raises(FirstCanaryExternalPostConsentBlocked, match="exact external PAPER POST confirmation"):
        execute_real_paper_first_canary_once(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=_credentials(),
            confirmation="EXECUTE ONCE",
            now=execute_at,
            final_evidence=_final(inputs, at=execute_at),
            delegate=delegate,
            reconciler=_FoundReconciler(),
        )
    assert delegate.calls == 0
    assert session.attempt.execution_started_path.exists() is False


def test_restart_safe_loader_rejects_different_effective_paper_key(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    _persist_restart_safe(session)
    other = AlpacaPaperCredentials(key_id="different-paper-key", secret_key="different-paper-secret")

    with pytest.raises(FirstCanaryRealPaperExecutionBlocked, match="effective PAPER key differs"):
        load_restart_safe_execution_inputs(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=other,
        )


def test_collect_fresh_final_evidence_uses_only_injected_get_gateways(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    final = _final(inputs, at=NOW + timedelta(seconds=4))
    account = _AccountGateway(final.account)
    asset = _AssetGateway(final.asset)
    flat = _FlatGateway(final.flat_account)
    market = _MarketGateway(final.market)

    observed = collect_fresh_final_evidence(
        workspace_path=session.workspace.root,
        credentials=_credentials(),
        now=NOW + timedelta(seconds=4),
        account_gateway=account,
        asset_gateway=asset,
        flat_gateway=flat,
        market_gateway=market,
    )

    assert observed.account == final.account
    assert observed.asset == final.asset
    assert observed.market == final.market
    assert observed.flat_account == final.flat_account
    assert observed.product_profile.fingerprint == final.product_profile.fingerprint
    assert account.calls == asset.calls == flat.calls == market.calls == 1


def test_collect_fresh_final_evidence_requires_verified_account_anchor(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    session.workspace.account_attestation_path.unlink()
    with pytest.raises(FirstCanaryRealPaperExecutionBlocked, match="verified PAPER account is missing"):
        collect_fresh_final_evidence(
            workspace_path=session.workspace.root,
            credentials=_credentials(),
            now=NOW + timedelta(seconds=4),
            account_gateway=object(),
            asset_gateway=object(),
            flat_gateway=object(),
            market_gateway=object(),
        )


def test_collect_fresh_final_evidence_rejects_unreadable_account_anchor(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    session.workspace.account_attestation_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(FirstCanaryRealPaperExecutionBlocked, match="unreadable"):
        collect_fresh_final_evidence(
            workspace_path=session.workspace.root,
            credentials=_credentials(),
            now=NOW + timedelta(seconds=4),
            account_gateway=object(),
            asset_gateway=object(),
            flat_gateway=object(),
            market_gateway=object(),
        )


def test_collect_fresh_final_evidence_rejects_non_paper_account_anchor(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    path = session.workspace.account_attestation_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["environment"] = "LIVE"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FirstCanaryRealPaperExecutionBlocked, match="not PAPER"):
        collect_fresh_final_evidence(
            workspace_path=session.workspace.root,
            credentials=_credentials(),
            now=NOW + timedelta(seconds=4),
            account_gateway=object(),
            asset_gateway=object(),
            flat_gateway=object(),
            market_gateway=object(),
        )


def test_collect_fresh_final_evidence_rejects_credential_persistence_anchor(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    path = session.workspace.account_attestation_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["credentials_persisted"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FirstCanaryRealPaperExecutionBlocked, match="credential policy"):
        collect_fresh_final_evidence(
            workspace_path=session.workspace.root,
            credentials=_credentials(),
            now=NOW + timedelta(seconds=4),
            account_gateway=object(),
            asset_gateway=object(),
            flat_gateway=object(),
            market_gateway=object(),
        )


def test_loader_rejects_non_path_workspace_before_any_execution(tmp_path) -> None:
    with pytest.raises(TypeError, match="pathlib.Path"):
        load_restart_safe_execution_inputs(
            workspace_path=str(tmp_path),  # type: ignore[arg-type]
            attempt_id="first-canary-0123456789abcdef0123456789abcdef",
            credentials=_credentials(),
        )


def test_loader_rejects_symlink_workspace_before_any_execution(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    link = tmp_path / "workspace-link"
    link.symlink_to(session.workspace.root, target_is_directory=True)
    with pytest.raises(FirstCanaryRealPaperExecutionBlocked, match="non-symlink"):
        load_restart_safe_execution_inputs(
            workspace_path=Path(link),
            attempt_id=session.attempt.attempt_id,
            credentials=_credentials(),
        )
