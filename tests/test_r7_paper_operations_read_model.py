from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import (
    PreparedCryptoPaperCanaryPackage,
    _hash_json as _package_hash_json,
    _package_payload_from_values,
)
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleBinding,
    CryptoLifecycleStatus,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.alpaca_paper_operational import account_attestation_payload
from autotrade.brokers.paper_portfolio import (
    PaperPortfolioOpenOrder,
    PaperPortfolioPosition,
    PaperPortfolioSnapshot,
)
from autotrade.domain import (
    OrderIntent,
    OrderRecord,
    OrderStatus,
    OrderType,
    Side,
    intent_fingerprint,
)
from autotrade.first_canary_fee_aware_recovery import FirstCanaryFeeAwareRecoveryLifecycle
from autotrade.paper_operations_read_model import (
    FirstCanaryCloseSourceDiscovery,
    PaperOperationsReadModel,
    PaperOperationsReadModelConflict,
    PaperOperationsReadModelMissing,
    read_paper_safety_snapshot,
    read_workspace_paper_account,
)
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime
from autotrade.product_profile import BrokerOrderType, TimeInForce


NOW = datetime(2026, 8, 21, 17, 5, tzinfo=timezone.utc)
ACCOUNT_ID = "12345678-1234-1234-1234-123456789abc"
ACCOUNT_REFERENCE = "b" * 64
ACCOUNT_FP = "1" * 64
ASSET_FP = "2" * 64
PRODUCT_FP = "3" * 64
GROSS = Decimal("0.00014432")
NET = Decimal("0.000143959")
PRICE = Decimal("72760")
BROKER_ORDER_ID = "broker-r7-source"
STRATEGY_ID = "R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION"
OLD_CREDS = AlpacaPaperCredentials(key_id="paper-key-old", secret_key="paper-secret-old")
CURRENT_CREDS = AlpacaPaperCredentials(key_id="paper-key-current", secret_key="paper-secret-current")


class _PortfolioReader:
    def __init__(self, snapshot: PaperPortfolioSnapshot) -> None:
        self.value = snapshot
        self.calls = 0

    def snapshot(self, *, credentials, expected_account_id, now):
        self.calls += 1
        assert credentials == CURRENT_CREDS
        assert expected_account_id == ACCOUNT_ID
        assert now == NOW
        return self.value


def _account(*, credentials: AlpacaPaperCredentials, at: datetime = NOW) -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id=ACCOUNT_ID,
        account_reference=ACCOUNT_REFERENCE,
        credential_reference=credentials.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=False,
        attested_at=at,
        request_id="request-account-r7",
        source_host=ALPACA_PAPER_TRADING_HOST,
        source_path=ALPACA_PAPER_ACCOUNT_PATH,
    )


def _write_account_anchor(workspace: Path) -> None:
    payload = account_attestation_payload(
        _account(credentials=OLD_CREDS, at=NOW - timedelta(days=1))
    )
    (workspace / "account_attestation.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_core_safety(
    workspace: Path,
    *,
    kill_active: int = 1,
    kill_reason: str = "R6_HEALTH_R4_EVIDENCE_REQUIRED",
    circuit_active: int = 0,
    circuit_reason: str = "",
    version: int = 7,
    include_circuit: bool = True,
) -> Path:
    path = workspace / "core.sqlite3"
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        if include_circuit:
            conn.execute(
                """
                CREATE TABLE safety_state (
                    singleton_id INTEGER PRIMARY KEY,
                    kill_switch_active INTEGER NOT NULL,
                    kill_switch_reason TEXT NOT NULL,
                    circuit_active INTEGER NOT NULL,
                    circuit_reason TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO safety_state VALUES (1, ?, ?, ?, ?, ?, ?)",
                (
                    kill_active,
                    kill_reason,
                    circuit_active,
                    circuit_reason,
                    version,
                    NOW.isoformat(),
                ),
            )
        else:
            conn.execute(
                """
                CREATE TABLE safety_state (
                    singleton_id INTEGER PRIMARY KEY,
                    kill_switch_active INTEGER NOT NULL,
                    kill_switch_reason TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO safety_state VALUES (1, ?, ?, ?, ?)",
                (kill_active, kill_reason, version, NOW.isoformat()),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def _position(*, quantity: Decimal = NET, available: Decimal | None = None) -> PaperPortfolioPosition:
    available_value = quantity if available is None else available
    return PaperPortfolioPosition(
        asset_id="btc-asset-id",
        broker_symbol="BTCUSD",
        symbol="BTC/USD",
        asset_class="crypto",
        exchange="CRYPTO",
        side="long",
        quantity=quantity,
        available_quantity=available_value,
        avg_entry_price=Decimal("72760"),
        current_price=Decimal("73000"),
        market_value=quantity * Decimal("73000"),
        cost_basis=quantity * Decimal("72760"),
        unrealized_pl=quantity * Decimal("240"),
        unrealized_plpc=Decimal("0.0032985"),
    )


def _open_order() -> PaperPortfolioOpenOrder:
    return PaperPortfolioOpenOrder(
        broker_order_id="open-order-1",
        client_order_id="client-open-1",
        broker_symbol="BTCUSD",
        symbol="BTC/USD",
        asset_class="crypto",
        side="sell",
        order_type="limit",
        time_in_force="gtc",
        status="new",
        quantity=Decimal("0.00001"),
        filled_quantity=Decimal("0"),
        limit_price=Decimal("80000"),
        stop_price=None,
    )


def _portfolio(
    *,
    positions: tuple[PaperPortfolioPosition, ...] | None = None,
    open_orders: tuple[PaperPortfolioOpenOrder, ...] = (),
    account_reference: str = ACCOUNT_REFERENCE,
) -> PaperPortfolioSnapshot:
    account = _account(credentials=CURRENT_CREDS)
    if account_reference != ACCOUNT_REFERENCE:
        account = AlpacaPaperAccountAttestation(
            account_id=account.account_id,
            account_reference=account_reference,
            credential_reference=account.credential_reference,
            status=account.status,
            currency=account.currency,
            buying_power=account.buying_power,
            portfolio_value=account.portfolio_value,
            shorting_enabled=account.shorting_enabled,
            attested_at=account.attested_at,
            request_id=account.request_id,
            source_host=account.source_host,
            source_path=account.source_path,
        )
    return PaperPortfolioSnapshot(
        account=account,
        positions=(_position(),) if positions is None else positions,
        open_orders=open_orders,
        positions_request_id="request-positions-r7",
        orders_request_id="request-orders-r7",
        positions_response_sha256="4" * 64,
        orders_response_sha256="5" * 64,
        observed_at=NOW,
    )


def _package(*, order: OrderRecord, binding: CryptoLifecycleBinding, prepared_state) -> PreparedCryptoPaperCanaryPackage:
    values: dict[str, object] = {
        "lifecycle_id": binding.lifecycle_id,
        "order_id": order.order_id,
        "client_order_id": binding.entry_client_order_id,
        "symbol": order.intent.symbol,
        "intent_fingerprint": intent_fingerprint(order.intent),
        "risk_decision_id": order.risk_decision_id,
        "risk_decision_fingerprint": "7" * 64,
        "risk_decision_safety_state_version": 0,
        "risk_decision_valid_until": NOW + timedelta(minutes=2),
        "market_fingerprint": "8" * 64,
        "market_attestation_fingerprint": "9" * 64,
        "account_attestation_fingerprint": ACCOUNT_FP,
        "asset_attestation_fingerprint": ASSET_FP,
        "product_profile_fingerprint": PRODUCT_FP,
        "crypto_order_fingerprint": binding.entry_order_fingerprint,
        "crypto_order_payload_hash": "a" * 64,
        "lifecycle_binding_hash": binding.fingerprint,
        "lifecycle_control_hash": prepared_state.control_hash,
        "lifecycle_event_head_hash": prepared_state.event_head_hash,
        "quantity": GROSS,
        "limit_price": PRICE,
        "notional": GROSS * PRICE,
        "effective_notional_cap": Decimal("12"),
        "prepared_at": NOW,
        "execution_deadline": NOW + timedelta(seconds=30),
        "order_status": OrderStatus.VALIDATED.value,
        "broker_order_type": BrokerOrderType.LIMIT.value,
        "time_in_force": TimeInForce.IOC.value,
        "opening_short": False,
        "uses_margin": False,
        "network_write_authorized": False,
        "next_action": "OPERATOR_DECISION_REQUIRED",
    }
    return PreparedCryptoPaperCanaryPackage(
        **values,
        package_hash=_package_hash_json(_package_payload_from_values(values)),
    )


def _write_hashed(
    attempt: FirstCanaryAttemptWorkspace,
    path: Path,
    document: dict[str, object],
    hash_key: str,
) -> None:
    payload = dict(document)
    payload[hash_key] = attempt.document_hash(payload, hash_key=hash_key)
    attempt.write_once(path=path, document=payload)


def _checkpoint_delete(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()
    finally:
        conn.close()


def _write_successful_source(workspace: Path, attempt_id: str) -> FirstCanaryAttemptWorkspace:
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=attempt_id,
    )
    runtime = SQLiteRuntime(attempt.database_path)
    suffix = attempt_id[-8:]
    intent = OrderIntent(
        intent_id=f"source-intent-{suffix}",
        idempotency_key=f"source-idem-{suffix}",
        strategy_id=STRATEGY_ID,
        symbol="BTC/USD",
        side=Side.BUY,
        quantity=GROSS,
        order_type=OrderType.LIMIT,
        created_at=NOW,
        limit_price=PRICE,
    )
    order = OrderRecord(
        order_id=f"source-order-{suffix}",
        intent=intent,
        risk_decision_id=f"source-risk-{suffix}",
        status=OrderStatus.SUBMITTING,
        created_at=NOW,
        submitted_at=NOW + timedelta(milliseconds=20),
    )
    SQLiteOrderStore(runtime).create_if_absent(order)
    raw = ":".join((order.order_id, ACCOUNT_FP, ASSET_FP, PRODUCT_FP))
    lifecycle_id = "r6c-entry-" + sha256(raw.encode("utf-8")).hexdigest()[:40]
    binding = CryptoLifecycleBinding(
        lifecycle_id=lifecycle_id,
        account_attestation_fingerprint=ACCOUNT_FP,
        asset_attestation_fingerprint=ASSET_FP,
        product_profile_fingerprint=PRODUCT_FP,
        symbol="BTC/USD",
        entry_order_fingerprint="4" * 64,
        entry_client_order_id=f"atr7-source-{suffix}",
        entry_quantity=GROSS,
        created_at=NOW,
    )
    lifecycle = FirstCanaryFeeAwareRecoveryLifecycle(runtime)
    prepared = lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(
        lifecycle_id,
        at=NOW + timedelta(milliseconds=30),
    )
    lifecycle.reconcile_entry(
        lifecycle_id,
        broker_order_id=f"{BROKER_ORDER_ID}-{suffix}",
        broker_status="filled",
        filled_quantity=GROSS,
        confirmed_net_long_quantity=NET,
        at=NOW + timedelta(milliseconds=40),
    )
    package = _package(order=order, binding=binding, prepared_state=prepared)
    _write_hashed(
        attempt,
        attempt.preparation_path,
        {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "environment": "PAPER",
            "account_reference": ACCOUNT_REFERENCE,
            "credential_reference": OLD_CREDS.credential_reference,
            "prepared_account_fingerprint": ACCOUNT_FP,
            "prepared_package": package.canonical_payload(),
            "external_post_authorized": False,
            "broker_write_performed": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        },
        "preparation_hash",
    )
    _write_hashed(
        attempt,
        attempt.execution_started_path,
        {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "client_order_id": package.client_order_id,
            "package_hash": package.package_hash,
            "retry_forbidden": True,
            "writer_invocation_permitted_once": True,
            "live_trading": "BLOCKED",
        },
        "execution_started_hash",
    )
    start_hash = json.loads(
        attempt.execution_started_path.read_text(encoding="utf-8")
    )["execution_started_hash"]
    _write_hashed(
        attempt,
        attempt.execution_result_path,
        {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "client_order_id": package.client_order_id,
            "package_hash": package.package_hash,
            "execution_started_hash": start_hash,
            "entry_attempt_count": 1,
            "broker_post_outcome": "BROKER_RESPONSE_RECEIVED",
            "retry_forbidden": True,
            "live_trading": "BLOCKED",
        },
        "execution_result_hash",
    )
    _write_hashed(
        attempt,
        attempt.recovery_resolution_path,
        {
            "schema_version": 1,
            "status": "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECONCILIATION_FINAL_NO_RETRY",
            "attempt_id": attempt_id,
            "client_order_id": package.client_order_id,
            "reconciliation_type": "ORDER_PLUS_POSITION",
            "broker_order_id": f"{BROKER_ORDER_ID}-{suffix}",
            "broker_order_status": "filled",
            "broker_filled_quantity": str(GROSS),
            "position_quantity": str(NET),
            "resulting_lifecycle_status": CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED.value,
            "entry_attempt_count": 1,
            "retry_post": False,
            "recovery_get_only": True,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        },
        "recovery_resolution_hash",
    )
    _checkpoint_delete(attempt.database_path)
    return attempt


def _write_zero_terminal_source(workspace: Path, attempt_id: str) -> None:
    attempt = FirstCanaryAttemptWorkspace.open(workspace_path=workspace, attempt_id=attempt_id)
    _write_hashed(
        attempt,
        attempt.preparation_path,
        {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "environment": "PAPER",
            "account_reference": ACCOUNT_REFERENCE,
            "credential_reference": OLD_CREDS.credential_reference,
            "prepared_account_fingerprint": ACCOUNT_FP,
            "external_post_authorized": False,
            "broker_write_performed": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        },
        "preparation_hash",
    )
    _write_hashed(
        attempt,
        attempt.execution_started_path,
        {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "retry_forbidden": True,
            "live_trading": "BLOCKED",
        },
        "execution_started_hash",
    )
    _write_hashed(
        attempt,
        attempt.recovery_resolution_path,
        {
            "schema_version": 1,
            "status": "CRYPTO_PAPER_FIRST_CANARY_RECOVERED_FLAT_NO_RETRY",
            "attempt_id": attempt_id,
            "position_quantity": "0",
            "retry_post": False,
            "recovery_get_only": True,
            "live_trading": "BLOCKED",
        },
        "recovery_resolution_hash",
    )


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    _write_account_anchor(workspace)
    _write_core_safety(workspace)
    return workspace


def test_workspace_account_anchor_is_canonical_and_allows_key_rotation(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    anchor = read_workspace_paper_account(workspace)
    assert anchor.attestation.account_id == ACCOUNT_ID
    assert anchor.attestation.account_reference == ACCOUNT_REFERENCE
    assert anchor.attestation.credential_reference == OLD_CREDS.credential_reference
    assert len(anchor.anchor_hash) == 64
    assert CURRENT_CREDS.credential_reference != anchor.attestation.credential_reference


def test_workspace_account_tamper_and_symlink_fail_closed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    path = workspace / "account_attestation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_host"] = "wrong.example"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PaperOperationsReadModelConflict, match="exact Alpaca PAPER"):
        read_workspace_paper_account(workspace)

    workspace2 = _workspace(tmp_path / "second")
    original = workspace2 / "account_attestation.json"
    backup = workspace2 / "account-backup.json"
    original.rename(backup)
    original.symlink_to(backup)
    with pytest.raises(PaperOperationsReadModelMissing, match="missing or unsafe"):
        read_workspace_paper_account(workspace2)


def test_safety_snapshot_is_read_only_and_preserves_engaged_kill(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    path = workspace / "core.sqlite3"
    before = path.read_bytes()
    snapshot = read_paper_safety_snapshot(workspace, now=NOW)
    assert snapshot.state.kill_switch_active is True
    assert snapshot.state.kill_switch_reason == "R6_HEALTH_R4_EVIDENCE_REQUIRED"
    assert snapshot.state.circuit_active is False
    assert snapshot.state.version == 7
    assert path.read_bytes() == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
    assert len(snapshot.snapshot_hash) == 64


def test_safety_reader_rejects_sidecars_incomplete_schema_and_inconsistent_flags(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    path = workspace / "core.sqlite3"
    Path(str(path) + "-wal").write_bytes(b"active")
    with pytest.raises(PaperOperationsReadModelConflict, match="WAL/SHM"):
        read_paper_safety_snapshot(workspace, now=NOW)

    workspace2 = tmp_path / "incomplete"
    workspace2.mkdir()
    _write_core_safety(workspace2, include_circuit=False)
    with pytest.raises(PaperOperationsReadModelConflict, match="schema is incomplete"):
        read_paper_safety_snapshot(workspace2, now=NOW)

    workspace3 = tmp_path / "inconsistent"
    workspace3.mkdir()
    _write_core_safety(workspace3, kill_active=0, kill_reason="should-be-empty")
    with pytest.raises(PaperOperationsReadModelConflict, match="inconsistent"):
        read_paper_safety_snapshot(workspace3, now=NOW)


def test_source_discovery_ignores_terminal_flat_and_binds_exact_nonzero_source(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _write_zero_terminal_source(
        workspace,
        "first-canary-00000000000000000000000000000001",
    )
    _write_successful_source(
        workspace,
        "first-canary-11111111111111111111111111111111",
    )
    source = FirstCanaryCloseSourceDiscovery(workspace_path=workspace).discover(
        portfolio=_portfolio(),
        now=NOW,
    )
    assert source.account_reference == ACCOUNT_REFERENCE
    assert source.source_credential_reference == OLD_CREDS.credential_reference
    assert source.prepared_account_fingerprint == ACCOUNT_FP
    assert source.source.confirmed_net_long_quantity == NET
    assert source.source.source_lifecycle.binding.account_attestation_fingerprint == ACCOUNT_FP
    assert len(source.binding_hash) == 64


def test_source_discovery_blocks_unresolved_burned_attempt(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id="first-canary-00000000000000000000000000000002",
    )
    _write_hashed(
        attempt,
        attempt.preparation_path,
        {
            "schema_version": 1,
            "attempt_id": attempt.attempt_id,
            "environment": "PAPER",
            "account_reference": ACCOUNT_REFERENCE,
            "credential_reference": OLD_CREDS.credential_reference,
            "prepared_account_fingerprint": ACCOUNT_FP,
            "external_post_authorized": False,
            "broker_write_performed": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        },
        "preparation_hash",
    )
    _write_hashed(
        attempt,
        attempt.execution_started_path,
        {
            "schema_version": 1,
            "attempt_id": attempt.attempt_id,
            "retry_forbidden": True,
            "live_trading": "BLOCKED",
        },
        "execution_started_hash",
    )
    with pytest.raises(PaperOperationsReadModelConflict, match="no terminal reconciliation"):
        FirstCanaryCloseSourceDiscovery(workspace_path=workspace).discover(
            portfolio=_portfolio(),
            now=NOW,
        )


def test_source_discovery_blocks_wrong_account_quantity_and_multiple_sources(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _write_successful_source(
        workspace,
        "first-canary-11111111111111111111111111111111",
    )
    with pytest.raises(PaperOperationsReadModelConflict, match="another PAPER account"):
        FirstCanaryCloseSourceDiscovery(workspace_path=workspace).discover(
            portfolio=_portfolio(account_reference="e" * 64),
            now=NOW,
        )
    with pytest.raises(PaperOperationsReadModelConflict, match="differs from fresh broker position"):
        FirstCanaryCloseSourceDiscovery(workspace_path=workspace).discover(
            portfolio=_portfolio(positions=(_position(quantity=Decimal("0.00012")),)),
            now=NOW,
        )

    _write_successful_source(
        workspace,
        "first-canary-22222222222222222222222222222222",
    )
    with pytest.raises(PaperOperationsReadModelConflict, match="multiple certified"):
        FirstCanaryCloseSourceDiscovery(workspace_path=workspace).discover(
            portfolio=_portfolio(),
            now=NOW,
        )


def test_read_model_produces_ready_get_only_snapshot_with_rotated_key(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _write_successful_source(
        workspace,
        "first-canary-11111111111111111111111111111111",
    )
    reader = _PortfolioReader(_portfolio())
    snapshot = PaperOperationsReadModel(
        workspace_path=workspace,
        portfolio_reader=reader,
    ).snapshot(credentials=CURRENT_CREDS, now=NOW)
    assert reader.calls == 1
    assert snapshot.ready_for_close_preparation is True
    assert snapshot.blockers == ()
    assert snapshot.close_source is not None
    assert snapshot.close_source.account_reference == ACCOUNT_REFERENCE
    assert snapshot.safety.state.kill_switch_active is True
    document = snapshot.to_dict()
    assert document["environment"] == "PAPER"
    assert document["broker_write_authorized"] is False
    assert document["retry_post"] is False
    assert document["credentials_persisted"] is False
    assert document["live_trading"] == "BLOCKED"
    assert document["ready_for_close_preparation"] is True


def test_read_model_surfaces_structural_blockers_without_discovering_source(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace / "first_canary_execution").mkdir()
    portfolio = _portfolio(
        positions=(_position(available=Decimal("0.0001")),),
        open_orders=(_open_order(),),
    )
    snapshot = PaperOperationsReadModel(
        workspace_path=workspace,
        portfolio_reader=_PortfolioReader(portfolio),
    ).snapshot(credentials=CURRENT_CREDS, now=NOW)
    assert snapshot.ready_for_close_preparation is False
    assert "FIRST_CLOSE_REQUIRES_ZERO_OPEN_ORDERS" in snapshot.blockers
    assert "FIRST_CLOSE_REQUIRES_FULL_POSITION_AVAILABLE" in snapshot.blockers
    assert snapshot.close_source is None


def test_read_model_rejects_fresh_broker_account_reference_drift(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(PaperOperationsReadModelConflict, match="account_reference"):
        PaperOperationsReadModel(
            workspace_path=workspace,
            portfolio_reader=_PortfolioReader(_portfolio(account_reference="e" * 64)),
        ).snapshot(credentials=CURRENT_CREDS, now=NOW)


def test_read_model_requires_aware_time_and_ephemeral_credentials(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    model = PaperOperationsReadModel(
        workspace_path=workspace,
        portfolio_reader=_PortfolioReader(_portfolio()),
    )
    with pytest.raises(TypeError, match="ephemeral"):
        model.snapshot(credentials=object(), now=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware"):
        model.snapshot(credentials=CURRENT_CREDS, now=NOW.replace(tzinfo=None))
