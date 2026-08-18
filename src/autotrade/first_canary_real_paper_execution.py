from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Mapping

from autotrade.first_canary_execution_gate import (
    FirstCanaryExecutionInputs,
    FirstCanaryExecutionOutcome,
    FirstCanaryFinalEvidence,
    execute_first_canary_once,
)
from autotrade.first_canary_external_post_consent import (
    CONSENT_FILENAME,
    FirstCanaryExternalPostConsent,
    consume_external_post_consent,
    require_fresh_external_post_consent,
)
from autotrade.first_canary_prepared_evidence import FirstCanaryPreparedEvidence
from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetGateway
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import (
    PreparedCryptoPaperCanaryPackage,
)
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    AlpacaPaperCryptoMarketDataConfig,
    AlpacaPaperCryptoMarketDataGateway,
)
from autotrade.brokers.alpaca_paper_crypto_order import (
    AlpacaPaperCryptoOrderRequest,
    CryptoOrderRole,
    CryptoOrderSide,
)
from autotrade.brokers.alpaca_paper_crypto_reconciliation import (
    AlpacaPaperCryptoReconciliationGateway,
)
from autotrade.brokers.alpaca_paper_crypto_writer import (
    AlpacaPaperCryptoWriteTransport,
    HttpsAlpacaPaperCryptoWriteTransport,
)
from autotrade.brokers.alpaca_paper_flat_account import AlpacaPaperFlatAccountGateway
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.persistence import SQLiteRuntime
from autotrade.product_profile import BrokerOrderType, ProductCapabilities, TimeInForce


EVIDENCE_FILENAME = "prepared_evidence.json"
WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"


class FirstCanaryRealPaperExecutionError(RuntimeError):
    pass


class FirstCanaryRealPaperExecutionBlocked(FirstCanaryRealPaperExecutionError):
    pass


def load_restart_safe_execution_inputs(
    *,
    workspace_path: Path,
    attempt_id: str,
    credentials: AlpacaPaperCredentials,
) -> tuple[
    FirstCanaryAttemptWorkspace,
    FirstCanaryExecutionInputs,
    dict[str, object],
    dict[str, object],
]:
    if not isinstance(credentials, AlpacaPaperCredentials):
        raise TypeError("ephemeral PAPER credentials are required")
    workspace = _workspace(workspace_path)
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace.root,
        attempt_id=attempt_id,
    )
    attempt.assert_unexecuted()
    if (attempt.attempt_root / CONSENT_FILENAME).exists():
        raise FirstCanaryRealPaperExecutionBlocked(
            "external POST consent already exists; execution replay is forbidden"
        )

    preparation = attempt.read(path=attempt.preparation_path)
    preparation_hash = attempt.require_document_hash(
        preparation,
        hash_key="preparation_hash",
        label="first-canary preparation",
    )
    restart_path = attempt.attempt_root / EVIDENCE_FILENAME
    restart_safe = attempt.read(path=restart_path)
    attempt.require_document_hash(
        restart_safe,
        hash_key="restart_safe_hash",
        label="restart-safe preparation",
    )

    if preparation.get("environment") != "PAPER" or preparation.get("live_trading") != "BLOCKED":
        raise FirstCanaryRealPaperExecutionBlocked("prepared execution is not strict PAPER/LIVE-deny")
    if preparation.get("external_post_authorized") is not False:
        raise FirstCanaryRealPaperExecutionBlocked("prepared execution already claims external POST authority")
    if restart_safe.get("external_post_authorized") is not False:
        raise FirstCanaryRealPaperExecutionBlocked("restart-safe evidence already claims external POST authority")
    if restart_safe.get("attempt_id") != attempt_id:
        raise FirstCanaryRealPaperExecutionBlocked("restart-safe attempt identity mismatch")
    if restart_safe.get("preparation_hash") != preparation_hash:
        raise FirstCanaryRealPaperExecutionBlocked("restart-safe preparation hash mismatch")
    if restart_safe.get("credential_reference") != credentials.credential_reference:
        raise FirstCanaryRealPaperExecutionBlocked(
            "effective PAPER key differs from restart-safe credential reference"
        )

    nested = _mapping(restart_safe, "prepared_evidence")
    evidence = FirstCanaryPreparedEvidence.from_document(nested)
    if restart_safe.get("prepared_evidence_hash") != nested.get("prepared_evidence_hash"):
        raise FirstCanaryRealPaperExecutionBlocked("restart-safe typed evidence hash mismatch")
    if evidence.account.credential_reference != credentials.credential_reference:
        raise FirstCanaryRealPaperExecutionBlocked(
            "prepared evidence credential reference differs from effective PAPER key"
        )

    package = _package_from_payload(_mapping(preparation, "prepared_package"))
    if restart_safe.get("package_hash") != package.package_hash:
        raise FirstCanaryRealPaperExecutionBlocked("restart-safe package hash mismatch")
    broker_order = _broker_order_from_payload(
        _mapping(preparation, "broker_order"),
        product_profile=evidence.product_profile,
        asset_fingerprint=evidence.asset.fingerprint,
    )
    if broker_order.fingerprint != package.crypto_order_fingerprint:
        raise FirstCanaryRealPaperExecutionBlocked("broker order/package fingerprint mismatch")
    if broker_order.payload_hash != package.crypto_order_payload_hash:
        raise FirstCanaryRealPaperExecutionBlocked("broker order/package payload hash mismatch")
    if broker_order.client_order_id != package.client_order_id:
        raise FirstCanaryRealPaperExecutionBlocked("broker order/package client_order_id mismatch")

    if workspace.core_db_path.is_symlink() or not workspace.core_db_path.is_file():
        raise FirstCanaryRealPaperExecutionBlocked("authoritative core.sqlite3 is missing or unsafe")
    if attempt.database_path.is_symlink() or not attempt.database_path.is_file():
        raise FirstCanaryRealPaperExecutionBlocked("attempt.sqlite3 is missing or unsafe")

    inputs = FirstCanaryExecutionInputs(
        attempt=attempt,
        core_runtime=SQLiteRuntime(workspace.core_db_path),
        attempt_runtime=SQLiteRuntime(attempt.database_path),
        credentials=credentials,
        package=package,
        broker_order=broker_order,
        prepared_account=evidence.account,
        prepared_asset=evidence.asset,
        prepared_product_profile=evidence.product_profile,
        prepared_market=evidence.market,
        risk_decision=evidence.risk_decision,
        preparation_authority_state_fingerprint=_hash_text(
            restart_safe,
            "authority_state_fingerprint",
        ),
    )
    return attempt, inputs, preparation, restart_safe


def collect_fresh_final_evidence(
    *,
    workspace_path: Path,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    account_gateway=None,
    asset_gateway=None,
    flat_gateway=None,
    market_gateway=None,
) -> FirstCanaryFinalEvidence:
    _aware(now, "now")
    instant = now.astimezone(timezone.utc)
    workspace = _workspace(workspace_path)
    expected_account_id = _account_anchor(workspace)
    config = AlpacaPaperGatewayConfig(enabled=True)

    account_reader = account_gateway or AlpacaPaperAccountGateway(config=config)
    account = account_reader.attest_account(
        credentials=credentials,
        expected_account_id=expected_account_id,
        now=instant,
    )
    asset_reader = asset_gateway or AlpacaPaperCryptoAssetGateway(config=config)
    asset = asset_reader.attest_asset(
        credentials=credentials,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=account.credential_reference,
        now=instant,
        symbol="BTC/USD",
    )
    product_profile = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )
    flat_reader = flat_gateway or AlpacaPaperFlatAccountGateway(config=config)
    flat = flat_reader.attest_flatness(
        credentials=credentials,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=account.credential_reference,
        now=instant,
    )
    market_reader = market_gateway or AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True)
    )
    market = market_reader.attest_snapshot(
        credentials=credentials,
        now=instant,
        symbol="BTC/USD",
    )
    return FirstCanaryFinalEvidence(
        account=account,
        asset=asset,
        product_profile=product_profile,
        market=market,
        flat_account=flat,
    )


def execute_real_paper_first_canary_once(
    *,
    workspace_path: Path,
    attempt_id: str,
    credentials: AlpacaPaperCredentials,
    confirmation: str,
    now: datetime,
    final_evidence: FirstCanaryFinalEvidence | None = None,
    delegate: AlpacaPaperCryptoWriteTransport | None = None,
    reconciler=None,
) -> tuple[FirstCanaryExternalPostConsent, FirstCanaryExecutionOutcome]:
    _aware(now, "now")
    instant = now.astimezone(timezone.utc)
    attempt, inputs, preparation, restart_safe = load_restart_safe_execution_inputs(
        workspace_path=workspace_path,
        attempt_id=attempt_id,
        credentials=credentials,
    )

    final = final_evidence or collect_fresh_final_evidence(
        workspace_path=workspace_path,
        credentials=credentials,
        now=instant,
    )

    # The consent latch is intentionally persisted only after all GET-only final
    # evidence has been collected, but before any broker POST delegate can run.
    consent = consume_external_post_consent(
        attempt=attempt,
        preparation=preparation,
        restart_safe=restart_safe,
        confirmation=confirmation,
        now=instant,
    )
    require_fresh_external_post_consent(receipt=consent, now=instant)

    effective_delegate = delegate or HttpsAlpacaPaperCryptoWriteTransport()
    effective_reconciler = reconciler or AlpacaPaperCryptoReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True)
    )
    outcome = execute_first_canary_once(
        inputs=inputs,
        final_evidence=final,
        delegate=effective_delegate,
        reconciler=effective_reconciler,
        now=instant,
    )
    return consent, outcome


def _workspace(path: Path) -> PaperOperationalWorkspace:
    if not isinstance(path, Path):
        raise TypeError("workspace_path must be pathlib.Path")
    raw = path.expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise FirstCanaryRealPaperExecutionBlocked(
            "existing non-symlink PAPER workspace is required"
        )
    return PaperOperationalWorkspace(root=raw.resolve())


def _account_anchor(workspace: PaperOperationalWorkspace) -> str:
    path = workspace.account_attestation_path
    if path.is_symlink() or not path.is_file():
        raise FirstCanaryRealPaperExecutionBlocked(
            "verified PAPER account is missing; verify the PAPER account first"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FirstCanaryRealPaperExecutionBlocked("verified PAPER account evidence is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("environment") != "PAPER":
        raise FirstCanaryRealPaperExecutionBlocked("workspace account evidence is not PAPER")
    if payload.get("credentials_persisted") is not False:
        raise FirstCanaryRealPaperExecutionBlocked("workspace account evidence violates credential policy")
    account_id = payload.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise FirstCanaryRealPaperExecutionBlocked("workspace PAPER account ID is missing")
    return account_id.strip()


def _package_from_payload(raw: Mapping[str, object]) -> PreparedCryptoPaperCanaryPackage:
    try:
        return PreparedCryptoPaperCanaryPackage(
            lifecycle_id=_text(raw, "lifecycle_id"),
            order_id=_text(raw, "order_id"),
            client_order_id=_text(raw, "client_order_id"),
            symbol=_text(raw, "symbol"),
            intent_fingerprint=_hash_text(raw, "intent_fingerprint"),
            risk_decision_id=_text(raw, "risk_decision_id"),
            risk_decision_fingerprint=_hash_text(raw, "risk_decision_fingerprint"),
            risk_decision_safety_state_version=_integer(raw, "risk_decision_safety_state_version"),
            risk_decision_valid_until=_datetime(raw, "risk_decision_valid_until"),
            market_fingerprint=_hash_text(raw, "market_fingerprint"),
            market_attestation_fingerprint=_hash_text(raw, "market_attestation_fingerprint"),
            account_attestation_fingerprint=_hash_text(raw, "account_attestation_fingerprint"),
            asset_attestation_fingerprint=_hash_text(raw, "asset_attestation_fingerprint"),
            product_profile_fingerprint=_hash_text(raw, "product_profile_fingerprint"),
            crypto_order_fingerprint=_hash_text(raw, "crypto_order_fingerprint"),
            crypto_order_payload_hash=_hash_text(raw, "crypto_order_payload_hash"),
            lifecycle_binding_hash=_hash_text(raw, "lifecycle_binding_hash"),
            lifecycle_control_hash=_hash_text(raw, "lifecycle_control_hash"),
            lifecycle_event_head_hash=_hash_text(raw, "lifecycle_event_head_hash"),
            quantity=_decimal(raw.get("quantity"), "quantity"),
            limit_price=_decimal(raw.get("limit_price"), "limit_price"),
            notional=_decimal(raw.get("notional"), "notional"),
            effective_notional_cap=_decimal(raw.get("effective_notional_cap"), "effective_notional_cap"),
            prepared_at=_datetime(raw, "prepared_at"),
            execution_deadline=_datetime(raw, "execution_deadline"),
            order_status=_text(raw, "order_status"),
            broker_order_type=_text(raw, "broker_order_type"),
            time_in_force=_text(raw, "time_in_force"),
            opening_short=_boolean(raw, "opening_short"),
            uses_margin=_boolean(raw, "uses_margin"),
            network_write_authorized=_boolean(raw, "network_write_authorized"),
            next_action=_text(raw, "next_action"),
            package_hash=_hash_text(raw, "package_hash"),
        )
    except ValueError as exc:
        raise FirstCanaryRealPaperExecutionBlocked("prepared package payload is invalid") from exc


def _broker_order_from_payload(
    raw: Mapping[str, object],
    *,
    product_profile: ProductCapabilities,
    asset_fingerprint: str,
) -> AlpacaPaperCryptoOrderRequest:
    payload = _mapping(raw, "payload")
    if raw.get("product_profile_fingerprint") != product_profile.fingerprint:
        raise FirstCanaryRealPaperExecutionBlocked("broker order ProductCapabilities binding mismatch")
    if raw.get("asset_attestation_fingerprint") != asset_fingerprint:
        raise FirstCanaryRealPaperExecutionBlocked("broker order asset binding mismatch")
    limit_raw = payload.get("limit_price")
    stop_raw = payload.get("stop_price")
    request = AlpacaPaperCryptoOrderRequest(
        role=CryptoOrderRole(_text(raw, "role")),
        symbol=_text(payload, "symbol"),
        side=CryptoOrderSide(_text(payload, "side")),
        quantity=_decimal(payload.get("qty"), "qty"),
        order_type=BrokerOrderType(_text(payload, "type")),
        time_in_force=TimeInForce(_text(payload, "time_in_force")),
        client_order_id=_text(payload, "client_order_id"),
        product_profile_fingerprint=product_profile.fingerprint,
        asset_attestation_fingerprint=asset_fingerprint,
        limit_price=None if limit_raw is None else _decimal(limit_raw, "limit_price"),
        stop_price=None if stop_raw is None else _decimal(stop_raw, "stop_price"),
    )
    if raw.get("fingerprint") != request.fingerprint or raw.get("payload_hash") != request.payload_hash:
        raise FirstCanaryRealPaperExecutionBlocked("durable broker order payload is non-canonical")
    return request


def _mapping(raw: Mapping[str, object], key: str) -> dict[str, object]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise FirstCanaryRealPaperExecutionBlocked(f"{key} must be object")
    return dict(value)


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise FirstCanaryRealPaperExecutionBlocked(f"{key} is missing or invalid")
    return value


def _hash_text(raw: Mapping[str, object], key: str) -> str:
    value = _text(raw, key)
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise FirstCanaryRealPaperExecutionBlocked(f"{key} must be lowercase SHA-256")
    return value


def _integer(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FirstCanaryRealPaperExecutionBlocked(f"{key} must be integer")
    return value


def _boolean(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise FirstCanaryRealPaperExecutionBlocked(f"{key} must be boolean")
    return value


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise FirstCanaryRealPaperExecutionBlocked(f"{label} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise FirstCanaryRealPaperExecutionBlocked(f"{label} is invalid decimal") from exc
    if not parsed.is_finite():
        raise FirstCanaryRealPaperExecutionBlocked(f"{label} must be finite")
    return parsed


def _datetime(raw: Mapping[str, object], key: str) -> datetime:
    value = _text(raw, key)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FirstCanaryRealPaperExecutionBlocked(f"{key} is invalid datetime") from exc
    _aware(parsed, key)
    return parsed.astimezone(timezone.utc)


def _aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "EVIDENCE_FILENAME",
    "FirstCanaryRealPaperExecutionBlocked",
    "FirstCanaryRealPaperExecutionError",
    "collect_fresh_final_evidence",
    "execute_real_paper_first_canary_once",
    "load_restart_safe_execution_inputs",
]
