from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from autotrade.brokers.alpaca_paper_flat_account import (
    AlpacaPaperFlatAccountGateway,
    PaperFlatAccountIntegrityError,
    PaperFlatAccountReadPolicy,
)
from autotrade.brokers.alpaca_paper_flat_account_evidence import (
    PaperFlatAccountEvidenceError,
    PaperFlatAccountEvidenceStore,
)
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperPolicyError,
    AlpacaPaperReadRequest,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace


NOW = datetime(2026, 8, 12, 1, 50, tzinfo=timezone.utc)


def h(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class FakeTransport:
    def __init__(self, *, positions: list[object], orders: list[object]) -> None:
        self.positions = positions
        self.orders = orders
        self.requests: list[AlpacaPaperReadRequest] = []

    def read(self, request: AlpacaPaperReadRequest) -> AlpacaPaperHttpResponse:
        self.requests.append(request)
        if request.url.endswith("/v2/positions"):
            payload = self.positions
            request_id = "req-positions"
        else:
            payload = self.orders
            request_id = "req-orders"
        return AlpacaPaperHttpResponse(
            status_code=200,
            body=json.dumps(payload).encode("utf-8"),
            final_url=request.url,
            headers={"content-type": "application/json", "x-request-id": request_id},
        )


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")


def account_attestation(creds: AlpacaPaperCredentials) -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="7ca57c2a-1b8f-4e18-9414-cb88b80227c7",
        account_reference=h("flat-account-test-account"),
        credential_reference=creds.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=False,
        attested_at=NOW,
        request_id="req-account",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )


def flat_attestation(creds: AlpacaPaperCredentials):
    account = account_attestation(creds)
    return AlpacaPaperFlatAccountGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        transport=FakeTransport(positions=[], orders=[]),
    ).attest_flatness(
        credentials=creds,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=creds.credential_reference,
        now=NOW,
    )


def test_flat_account_gate_proves_empty_positions_and_open_orders() -> None:
    transport = FakeTransport(positions=[], orders=[])
    creds = credentials()
    account = account_attestation(creds)
    attestation = AlpacaPaperFlatAccountGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        transport=transport,
    ).attest_flatness(
        credentials=creds,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=creds.credential_reference,
        now=NOW,
    )

    assert attestation.position_count == 0
    assert attestation.open_order_count == 0
    assert attestation.clean_for_first_canary is True
    assert len(transport.requests) == 2
    assert transport.requests[0].method == "GET"
    assert transport.requests[0].url.endswith("/v2/positions")
    assert "status=open" in transport.requests[1].url
    assert all("paper-api.alpaca.markets" in request.url for request in transport.requests)


def test_flat_account_gate_blocks_first_canary_truth_when_exposure_exists() -> None:
    transport = FakeTransport(
        positions=[{"symbol": "AAPL", "qty": "1"}],
        orders=[{"id": "order-1", "status": "new"}],
    )
    creds = credentials()
    account = account_attestation(creds)
    attestation = AlpacaPaperFlatAccountGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        transport=transport,
    ).attest_flatness(
        credentials=creds,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=creds.credential_reference,
        now=NOW,
    )

    assert attestation.position_count == 1
    assert attestation.open_order_count == 1
    assert attestation.clean_for_first_canary is False


def test_flat_account_gate_rejects_credentials_not_bound_to_account_evidence() -> None:
    creds = credentials()
    transport = FakeTransport(positions=[], orders=[])
    with pytest.raises(PaperFlatAccountIntegrityError, match="credentials do not match"):
        AlpacaPaperFlatAccountGateway(
            config=AlpacaPaperGatewayConfig(enabled=True),
            transport=transport,
        ).attest_flatness(
            credentials=creds,
            account_attestation_fingerprint=h("account"),
            expected_credential_reference=h("different-key"),
            now=NOW,
        )
    assert transport.requests == []


def test_flat_account_policy_allows_only_exact_get_targets() -> None:
    policy = PaperFlatAccountReadPolicy()
    headers = {
        "Accept": "application/json",
        "User-Agent": "AUTO-TRADE-R6/0.28R",
        "APCA-API-KEY-ID": "key",
        "APCA-API-SECRET-KEY": "secret",
    }
    policy.validate(
        AlpacaPaperReadRequest(
            method="GET",
            url="https://paper-api.alpaca.markets/v2/orders?status=open&limit=500&direction=asc&nested=true",
            timeout_seconds=5,
            headers=headers,
        )
    )
    with pytest.raises(AlpacaPaperPolicyError):
        policy.validate(
            AlpacaPaperReadRequest(
                method="GET",
                url="https://paper-api.alpaca.markets/v2/orders?status=all&limit=500&direction=asc&nested=true",
                timeout_seconds=5,
                headers=headers,
            )
        )
    with pytest.raises(AlpacaPaperPolicyError):
        policy.validate(
            AlpacaPaperReadRequest(
                method="POST",
                url="https://paper-api.alpaca.markets/v2/orders?status=open&limit=500&direction=asc&nested=true",
                timeout_seconds=5,
                headers=headers,
            )
        )


def test_flat_account_evidence_requires_persisted_account_binding(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    creds = credentials()
    attestation = flat_attestation(creds)
    store = PaperFlatAccountEvidenceStore(workspace)

    with pytest.raises(PaperFlatAccountEvidenceError, match="account attestation is required"):
        store.write(attestation)


def test_flat_account_evidence_rejects_mismatched_persisted_account(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    creds = credentials()
    other = AlpacaPaperCredentials(key_id="other-paper-key", secret_key="other-paper-secret")
    workspace.write_account_attestation(account_attestation(other))

    store = PaperFlatAccountEvidenceStore(workspace)
    with pytest.raises(PaperFlatAccountEvidenceError, match="persisted account attestation"):
        store.write(flat_attestation(creds))


def test_flat_account_evidence_round_trips_without_secret_or_mutation_authority(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    creds = credentials()
    account = account_attestation(creds)
    workspace.write_account_attestation(account)
    attestation = flat_attestation(creds)
    store = PaperFlatAccountEvidenceStore(workspace)
    store.write(attestation)
    assert store.read() == attestation
    raw = store.path.read_text(encoding="utf-8")
    assert "paper-secret" not in raw
    assert '"broker_mutation_performed": false' in raw
    assert '"execution_authorized": false' in raw
    assert '"capital_authority": "NONE"' in raw
