from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import json
import runpy

import pytest

from autotrade.first_canary_prepared_evidence import FirstCanaryPreparedEvidence
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from test_r6_first_canary_execution_gate import (
    ATTEMPT_ID,
    NOW,
    _small_asset,
)
from test_r6_paper_crypto_canary_coordinator import _account, _market
from test_r6_paper_crypto_cold_start_final_guard import _flat_attestation, _setup


ROOT = Path(__file__).resolve().parents[1]
BASE_PREPARE = ROOT / "scripts/mac_crypto_first_canary_prepare.py"
RESTART_SAFE = ROOT / "scripts/mac_crypto_first_canary_prepare_restart_safe.py"
KEY_ID = "simulation-paper-key"
SECRET = "simulation-paper-secret"
CREDENTIAL_REFERENCE = sha256(KEY_ID.encode("utf-8")).hexdigest()


def _base_session_callable(workspace: Path):
    namespace = runpy.run_path(str(BASE_PREPARE))
    prepared_at = NOW + timedelta(seconds=4)
    account = replace(
        _account(observed=prepared_at),
        credential_reference=CREDENTIAL_REFERENCE,
    )
    asset = _small_asset(account, observed=prepared_at)
    flat = _flat_attestation(account, at=prepared_at)
    market = _market(observed=prepared_at)

    def prepare_callable(*, workspace_path, attempt_id, credentials, now):
        assert workspace_path == workspace.resolve()
        return namespace["prepare_from_evidence"](
            workspace_path=workspace_path,
            attempt_id=attempt_id,
            credentials=credentials,
            account=account,
            asset=asset,
            flat_account=flat,
            market_attestation=market,
            now=now,
        )

    return prepare_callable


def test_restart_safe_prepare_persists_exact_typed_evidence_without_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)
    workspace = tmp_path / "workspace"
    _setup(workspace)
    credentials = AlpacaPaperCredentials(key_id=KEY_ID, secret_key=SECRET)
    namespace = runpy.run_path(str(RESTART_SAFE))

    result = namespace["prepare_restart_safe"](
        workspace_path=workspace,
        attempt_id=ATTEMPT_ID,
        credentials=credentials,
        now=NOW + timedelta(seconds=4),
        prepare_callable=_base_session_callable(workspace),
    )

    assert result["status"] == "CRYPTO_PAPER_FIRST_CANARY_RESTART_SAFE_PREPARED_NO_POST"
    assert result["attempt_id"] == ATTEMPT_ID
    assert result["credentials_persisted"] is False
    assert result["secret_persisted"] is False
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["live_trading"] == "BLOCKED"

    evidence_path = workspace / "first_canary_execution" / ATTEMPT_ID / "prepared_evidence.json"
    raw_text = evidence_path.read_text(encoding="utf-8")
    assert SECRET not in raw_text
    assert KEY_ID not in raw_text
    document = json.loads(raw_text)
    assert document["credential_reference"] == CREDENTIAL_REFERENCE
    assert document["credentials_persisted"] is False
    assert document["secret_persisted"] is False
    restored = FirstCanaryPreparedEvidence.from_document(document["prepared_evidence"])
    assert restored.account.fingerprint == document["prepared_evidence"]["account_fingerprint"]
    assert restored.asset.fingerprint == document["prepared_evidence"]["asset_fingerprint"]
    assert restored.product_profile.fingerprint == document["prepared_evidence"]["product_profile_fingerprint"]
    assert restored.market.fingerprint == document["prepared_evidence"]["market_attestation_fingerprint"]


def test_restart_safe_prepare_refuses_write_enabled_environment_before_preparation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    namespace = runpy.run_path(str(RESTART_SAFE))
    called = False

    def prepare_callable(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not run")

    with pytest.raises(namespace["RestartSafePreparationError"], match="refuses broker-write"):
        namespace["prepare_restart_safe"](
            workspace_path=workspace,
            attempt_id=ATTEMPT_ID,
            credentials=AlpacaPaperCredentials(key_id=KEY_ID, secret_key=SECRET),
            now=NOW,
            prepare_callable=prepare_callable,
        )
    assert called is False
