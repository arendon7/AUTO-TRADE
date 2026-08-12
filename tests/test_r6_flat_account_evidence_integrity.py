from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.brokers.alpaca_paper_flat_account_evidence import (
    PaperFlatAccountEvidenceError,
    PaperFlatAccountEvidenceStore,
)
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace


NOW = datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc)


def h(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")


def account(creds: AlpacaPaperCredentials) -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="7ca57c2a-1b8f-4e18-9414-cb88b80227c7",
        account_reference=h("evidence-integrity-account"),
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


def flat(creds: AlpacaPaperCredentials) -> PaperFlatAccountAttestation:
    bound = account(creds)
    return PaperFlatAccountAttestation(
        account_attestation_fingerprint=bound.fingerprint,
        credential_reference=creds.credential_reference,
        position_count=0,
        open_order_count=0,
        positions_response_hash=h("positions"),
        orders_response_hash=h("orders"),
        positions_request_id="req-positions",
        orders_request_id="req-orders",
        attested_at=NOW,
    )


def prepared_store(tmp_path):
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    creds = credentials()
    workspace.write_account_attestation(account(creds))
    store = PaperFlatAccountEvidenceStore(workspace)
    attestation = flat(creds)
    store.write(attestation)
    return workspace, store, attestation


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, payload):
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def test_flat_account_evidence_type_guards(tmp_path) -> None:
    with pytest.raises(TypeError, match="PaperOperationalWorkspace"):
        PaperFlatAccountEvidenceStore(object())  # type: ignore[arg-type]

    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    store = PaperFlatAccountEvidenceStore(workspace)
    with pytest.raises(TypeError, match="PaperFlatAccountAttestation"):
        store.write(object())  # type: ignore[arg-type]


def test_write_rejects_credential_reference_not_bound_to_same_account(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    creds = credentials()
    workspace.write_account_attestation(account(creds))
    store = PaperFlatAccountEvidenceStore(workspace)
    mismatched = replace(flat(creds), credential_reference=h("other-credential"))

    with pytest.raises(PaperFlatAccountEvidenceError, match="credential reference"):
        store.write(mismatched)


def test_write_rejects_conflicting_second_evidence(tmp_path) -> None:
    workspace, store, attestation = prepared_store(tmp_path)
    assert store.path.is_file()

    conflicting = replace(attestation, position_count=1)
    with pytest.raises(PaperFlatAccountEvidenceError, match="cannot persist"):
        store.write(conflicting)


def test_read_missing_artifact_is_fail_closed(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    creds = credentials()
    workspace.write_account_attestation(account(creds))

    with pytest.raises(PaperFlatAccountEvidenceError, match="cannot read"):
        PaperFlatAccountEvidenceStore(workspace).read()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema_version", 2),
        ("environment", "LIVE"),
        ("credentials_persisted", True),
        ("broker_mutation_performed", True),
        ("execution_authorized", True),
        ("capital_authority", "TRADING"),
        ("production_status", "LIVE"),
    ],
)
def test_read_rejects_tampered_non_authorizing_envelope(tmp_path, key, value) -> None:
    _, store, _ = prepared_store(tmp_path)
    payload = read_json(store.path)
    payload[key] = value
    write_json(store.path, payload)

    with pytest.raises(PaperFlatAccountEvidenceError):
        store.read()


def test_read_rejects_invalid_typed_attestation_payload(tmp_path) -> None:
    _, store, _ = prepared_store(tmp_path)
    payload = read_json(store.path)
    payload["position_count"] = True
    write_json(store.path, payload)

    with pytest.raises(PaperFlatAccountEvidenceError, match="persisted flat-account evidence is invalid"):
        store.read()


def test_read_rejects_naive_attestation_timestamp(tmp_path) -> None:
    _, store, _ = prepared_store(tmp_path)
    payload = read_json(store.path)
    payload["attested_at"] = "2026-08-12T02:00:00"
    write_json(store.path, payload)

    with pytest.raises(PaperFlatAccountEvidenceError, match="persisted flat-account evidence is invalid"):
        store.read()


def test_read_rejects_clean_state_tampering(tmp_path) -> None:
    _, store, _ = prepared_store(tmp_path)
    payload = read_json(store.path)
    payload["clean_for_first_canary"] = False
    write_json(store.path, payload)

    with pytest.raises(PaperFlatAccountEvidenceError, match="clean-state evidence mismatch"):
        store.read()


def test_read_rejects_fingerprint_tampering(tmp_path) -> None:
    _, store, _ = prepared_store(tmp_path)
    payload = read_json(store.path)
    payload["attestation_fingerprint"] = h("tampered-flat-evidence")
    write_json(store.path, payload)

    with pytest.raises(PaperFlatAccountEvidenceError, match="fingerprint mismatch"):
        store.read()


def test_read_rejects_account_binding_changed_after_flat_evidence(tmp_path) -> None:
    workspace, store, _ = prepared_store(tmp_path)
    account_payload = read_json(workspace.account_attestation_path)
    account_payload["attestation_fingerprint"] = h("different-account-attestation")
    write_json(workspace.account_attestation_path, account_payload)

    with pytest.raises(PaperFlatAccountEvidenceError, match="no longer matches persisted account evidence"):
        store.read()


def test_read_rejects_credential_binding_changed_after_flat_evidence(tmp_path) -> None:
    workspace, store, _ = prepared_store(tmp_path)
    account_payload = read_json(workspace.account_attestation_path)
    account_payload["credential_reference"] = h("different-credential-reference")
    write_json(workspace.account_attestation_path, account_payload)

    with pytest.raises(PaperFlatAccountEvidenceError, match="no longer matches persisted credential reference"):
        store.read()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("environment", "LIVE", "not PAPER"),
        ("credentials_persisted", True, "cannot contain credentials"),
        ("attestation_fingerprint", "bad", "fingerprint is invalid"),
        ("credential_reference", "bad", "credential reference is invalid"),
    ],
)
def test_bound_account_evidence_is_revalidated_on_every_read(
    tmp_path, key, value, message
) -> None:
    workspace, store, _ = prepared_store(tmp_path)
    payload = read_json(workspace.account_attestation_path)
    payload[key] = value
    write_json(workspace.account_attestation_path, payload)

    with pytest.raises(PaperFlatAccountEvidenceError, match=message):
        store.read()
