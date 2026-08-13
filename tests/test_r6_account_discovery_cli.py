from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperHttpResponse,
    AlpacaPaperReadRequest,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/r6_external_paper_account_discovery.py"
SPEC = importlib.util.spec_from_file_location("account_discovery_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

ACCOUNT_ID = "e6fe16f3-64a4-4921-8928-cadf02f92f98"


class FakeTransport:
    def __init__(self, response: AlpacaPaperHttpResponse) -> None:
        self.response = response
        self.calls: list[AlpacaPaperReadRequest] = []

    def read(self, request: AlpacaPaperReadRequest) -> AlpacaPaperHttpResponse:
        self.calls.append(request)
        return self.response


def response_ok(**changes) -> AlpacaPaperHttpResponse:
    payload = {
        "id": ACCOUNT_ID,
        "account_number": "PA3TEST12345",
        "status": "ACTIVE",
        "currency": "USD",
    }
    values = {
        "status_code": 200,
        "body": json.dumps(payload, separators=(",", ":")).encode(),
        "final_url": f"https://{ALPACA_PAPER_TRADING_HOST}{ALPACA_PAPER_ACCOUNT_PATH}",
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "x-request-id": "paper-discovery-request-123",
        },
    }
    values.update(changes)
    return AlpacaPaperHttpResponse(**values)


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(key_id="PAPERKEY123", secret_key="PAPERSECRET456")


def test_discovery_is_exactly_one_get_and_creates_no_attestation(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fake = FakeTransport(response_ok())
    result = module.run_account_discovery(
        workspace=workspace,
        credentials=credentials(),
        transport=fake,
    )

    assert len(fake.calls) == 1
    request = fake.calls[0]
    assert request.method == "GET"
    assert request.url == f"https://{ALPACA_PAPER_TRADING_HOST}/v2/account"
    assert result["account_id"] == ACCOUNT_ID
    assert result["account_number_hint"].endswith("2345")
    assert result["persistent_evidence_created"] is False
    assert result["account_attested"] is False
    assert result["operator_confirmation_required"] is True
    assert result["order_write_authorized"] is False
    assert result["external_order_submitted"] is False
    assert result["capital_authority"] == "NONE"
    assert result["live_trading"] == "BLOCKED"
    assert list(workspace.iterdir()) == []


def test_discovery_output_never_contains_secret_material(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = module.run_account_discovery(
        workspace=workspace,
        credentials=credentials(),
        transport=FakeTransport(response_ok()),
    )
    rendered = json.dumps(result, sort_keys=True)
    assert "PAPERKEY123" not in rendered
    assert "PAPERSECRET456" not in rendered
    assert "PA3TEST12345" not in rendered


def test_discovery_rejects_missing_or_symlink_workspace_before_io(tmp_path) -> None:
    fake = FakeTransport(response_ok())
    with pytest.raises(module.PaperAccountDiscoveryError, match="already exist"):
        module.run_account_discovery(
            workspace=tmp_path / "missing",
            credentials=credentials(),
            transport=fake,
        )
    assert fake.calls == []

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(module.PaperAccountDiscoveryError, match="symlink"):
        module.run_account_discovery(
            workspace=link,
            credentials=credentials(),
            transport=fake,
        )
    assert fake.calls == []


@pytest.mark.parametrize(
    "response",
    [
        response_ok(status_code=201),
        response_ok(headers={"content-type": "text/html", "x-request-id": "request"}),
        response_ok(headers={"content-type": "application/json"}),
        response_ok(body=b"not-json"),
        response_ok(final_url="https://example.com/v2/account"),
    ],
)
def test_discovery_fails_closed_on_ambiguous_response(tmp_path, response) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(Exception):
        module.run_account_discovery(
            workspace=workspace,
            credentials=credentials(),
            transport=FakeTransport(response),
        )
