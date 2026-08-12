from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import runpy

import pytest

from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/r6_external_paper_preflight.py"
NOW = datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)


class FakeAccountGateway:
    def __init__(self, expected: AlpacaPaperCredentials) -> None:
        self.expected = expected
        self.calls = 0

    def attest_account(self, *, credentials, expected_account_id, now):
        self.calls += 1
        assert credentials == self.expected
        assert expected_account_id == "e6fe16f3-64a4-4921-8928-cadf02f92f98"
        assert now == NOW
        return AlpacaPaperAccountAttestation(
            account_id=expected_account_id,
            account_reference="a" * 64,
            credential_reference=credentials.credential_reference,
            status="ACTIVE",
            currency="USD",
            buying_power=Decimal("100000"),
            portfolio_value=Decimal("100000"),
            shorting_enabled=True,
            attested_at=now,
            request_id="preflight-request-001",
            source_host="paper-api.alpaca.markets",
            source_path="/v2/account",
        )


def namespace() -> dict[str, object]:
    return runpy.run_path(str(SCRIPT))


def test_preflight_writes_sanitized_account_evidence_and_no_order_authority(tmp_path) -> None:
    ns = namespace()
    credentials = AlpacaPaperCredentials(
        key_id="PKTESTKEY1234567890",
        secret_key="paper-preflight-super-secret",
    )
    gateway = FakeAccountGateway(credentials)
    result = ns["run_account_preflight"](
        workspace=tmp_path / "workspace",
        expected_account_id="e6fe16f3-64a4-4921-8928-cadf02f92f98",
        credentials=credentials,
        gateway=gateway,
        now=NOW,
    )
    assert gateway.calls == 1
    assert result["network_method"] == "GET"
    assert result["network_path"] == "/v2/account"
    assert result["order_write_authorized"] is False
    assert result["external_order_submitted"] is False
    assert result["live_trading"] == "BLOCKED"

    artifact = PaperOperationalWorkspace.initialize(
        tmp_path / "workspace"
    ).account_attestation_path.read_text(encoding="utf-8")
    assert "paper-preflight-super-secret" not in artifact
    assert "PKTESTKEY1234567890" not in artifact
    payload = json.loads(artifact)
    assert payload["credential_reference"] == credentials.credential_reference
    assert payload["credentials_persisted"] is False


def test_cli_refuses_network_read_without_explicit_flag(tmp_path, monkeypatch) -> None:
    ns = namespace()
    monkeypatch.setenv("APCA_API_KEY_ID", "PKTESTKEY1234567890")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "paper-preflight-super-secret")
    with pytest.raises(SystemExit, match="disabled unless --allow-paper-account-read"):
        ns["main"](
            [
                "--workspace",
                str(tmp_path / "workspace"),
                "--expected-account-id",
                "e6fe16f3-64a4-4921-8928-cadf02f92f98",
            ]
        )


def test_credentials_are_environment_only_and_repr_redacted(monkeypatch) -> None:
    ns = namespace()
    monkeypatch.setenv("APCA_API_KEY_ID", "PKTESTKEY1234567890")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "paper-preflight-super-secret")
    credentials = ns["_credentials_from_environment"]()
    assert credentials.key_id == "PKTESTKEY1234567890"
    assert "PKTESTKEY1234567890" not in repr(credentials)
    assert "paper-preflight-super-secret" not in repr(credentials)


def test_missing_environment_credentials_fail_closed(monkeypatch) -> None:
    ns = namespace()
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(SystemExit, match="must exist only in environment"):
        ns["_credentials_from_environment"]()


def test_script_contains_no_secret_cli_flags_or_order_write_surface() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "--secret" not in source
    assert "--key-id" not in source
    assert "/v2/orders" not in source
    assert "alpaca_paper_writer" not in source
    assert "alpaca_paper_execution_bridge" not in source
    assert ".submit_once(" not in source
    assert "stage_external_submission" not in source
