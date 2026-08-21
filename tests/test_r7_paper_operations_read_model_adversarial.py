from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import autotrade.paper_operations_read_model as model
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import FirstCanaryAttemptWorkspace
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.paper_portfolio import PaperPortfolioPosition, PaperPortfolioSnapshot
from autotrade.state import SafetyControlState


NOW = datetime(2026, 8, 21, 17, 30, tzinfo=timezone.utc)
CREDS = AlpacaPaperCredentials(key_id="paper-key-adversarial", secret_key="paper-secret")


def _account() -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="12345678-1234-1234-1234-123456789abc",
        account_reference="b" * 64,
        credential_reference=CREDS.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("1000"),
        portfolio_value=Decimal("1000"),
        shorting_enabled=False,
        attested_at=NOW,
        request_id="r7-adversarial-account",
        source_host=ALPACA_PAPER_TRADING_HOST,
        source_path=ALPACA_PAPER_ACCOUNT_PATH,
    )


def _position(*, symbol: str = "BTC/USD", side: str = "long", quantity: Decimal = Decimal("0.1")) -> PaperPortfolioPosition:
    return PaperPortfolioPosition(
        asset_id="asset",
        broker_symbol="BTCUSD",
        symbol=symbol,
        asset_class="crypto",
        exchange="CRYPTO",
        side=side,
        quantity=quantity,
        available_quantity=abs(quantity),
        avg_entry_price=Decimal("10"),
        current_price=Decimal("11"),
        market_value=Decimal("1.1"),
        cost_basis=Decimal("1"),
        unrealized_pl=Decimal("0.1"),
        unrealized_plpc=Decimal("0.1"),
    )


def _portfolio(positions: tuple[PaperPortfolioPosition, ...]) -> PaperPortfolioSnapshot:
    return PaperPortfolioSnapshot(
        account=_account(),
        positions=positions,
        open_orders=(),
        positions_request_id="positions-request",
        orders_request_id="orders-request",
        positions_response_sha256="4" * 64,
        orders_response_sha256="5" * 64,
        observed_at=NOW,
    )


def test_low_level_parsers_fail_closed_on_invalid_types_and_domains(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="pathlib.Path"):
        model._workspace("bad")  # type: ignore[arg-type]
    with pytest.raises(model.PaperOperationsReadModelMissing, match="workspace"):
        model._workspace(tmp_path / "missing")

    with pytest.raises(model.PaperOperationsReadModelConflict, match="decimal string"):
        model._decimal(None, "x", nonnegative=True)
    with pytest.raises(model.PaperOperationsReadModelConflict, match="invalid decimal"):
        model._decimal("not-a-number", "x", nonnegative=True)
    with pytest.raises(model.PaperOperationsReadModelConflict, match="outside allowed"):
        model._decimal("-1", "x", nonnegative=True)

    with pytest.raises(model.PaperOperationsReadModelConflict, match="datetime text"):
        model._datetime(None, "x")
    with pytest.raises(model.PaperOperationsReadModelConflict, match="invalid datetime"):
        model._datetime("not-a-date", "x")
    with pytest.raises(ValueError, match="timezone-aware"):
        model._datetime("2026-08-21T17:30:00", "x")

    with pytest.raises(model.PaperOperationsReadModelConflict, match="flag is invalid"):
        model._sqlite_flag(True, "x")
    with pytest.raises(model.PaperOperationsReadModelConflict, match="flag is invalid"):
        model._sqlite_flag(2, "x")
    with pytest.raises(model.PaperOperationsReadModelConflict, match="must be text"):
        model._plain_text(3, "x")
    with pytest.raises(model.PaperOperationsReadModelConflict, match="control characters"):
        model._plain_text("bad\nreason", "x")
    with pytest.raises(model.PaperOperationsReadModelConflict, match="non-empty text"):
        model._text({}, "missing")
    with pytest.raises(ValueError, match="SHA-256"):
        model._hash_text({"hash": "bad"}, "hash")


def test_json_and_sqlite_file_guards_reject_unsafe_inputs(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(model.PaperOperationsReadModelMissing, match="missing or unsafe"):
        model._read_json_file(missing, label="artifact")

    malformed = tmp_path / "malformed.json"
    malformed.write_bytes(b"{not-json")
    with pytest.raises(model.PaperOperationsReadModelConflict, match="unreadable"):
        model._read_json_file(malformed, label="artifact")

    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(model.PaperOperationsReadModelConflict, match="root must be an object"):
        model._read_json_file(array, label="artifact")

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(model.PaperOperationsReadModelMissing, match="missing or unsafe"):
        model._read_json_file(link, label="artifact")

    db = tmp_path / "core.sqlite3"
    db.write_bytes(b"sqlite-placeholder")
    model._require_stable_sqlite(db, label="core.sqlite3")
    Path(str(db) + "-shm").write_bytes(b"active")
    with pytest.raises(model.PaperOperationsReadModelConflict, match="WAL/SHM"):
        model._require_stable_sqlite(db, label="core.sqlite3")
    with pytest.raises(model.PaperOperationsReadModelMissing, match="cannot hash"):
        model._file_sha256(tmp_path / "absent.sqlite3")


def test_account_and_safety_value_objects_reject_tampered_hashes() -> None:
    account = _account()
    with pytest.raises(ValueError, match="attestation is required"):
        model.PaperWorkspaceAccountAnchor(
            attestation=object(),  # type: ignore[arg-type]
            artifact_sha256="1" * 64,
            anchor_hash="2" * 64,
        )
    with pytest.raises(ValueError, match="anchor hash mismatch"):
        model.PaperWorkspaceAccountAnchor(
            attestation=account,
            artifact_sha256="1" * 64,
            anchor_hash="2" * 64,
        )

    state = SafetyControlState(version=1)
    with pytest.raises(ValueError, match="SafetyControlState"):
        model.PaperSafetyReadSnapshot(
            state=object(),  # type: ignore[arg-type]
            core_db_sha256="1" * 64,
            observed_at=NOW,
            snapshot_hash="2" * 64,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        model.PaperSafetyReadSnapshot(
            state=state,
            core_db_sha256="1" * 64,
            observed_at=NOW.replace(tzinfo=None),
            snapshot_hash="2" * 64,
        )
    with pytest.raises(ValueError, match="snapshot hash mismatch"):
        model.PaperSafetyReadSnapshot(
            state=state,
            core_db_sha256="1" * 64,
            observed_at=NOW,
            snapshot_hash="2" * 64,
        )


def test_first_close_shape_rejects_empty_non_btc_short_and_zero_positions() -> None:
    assert model._first_close_blockers(_portfolio(())) == (
        "FIRST_CLOSE_REQUIRES_EXACTLY_ONE_POSITION",
    )
    wrong_symbol = model._first_close_blockers(_portfolio((_position(symbol="ETH/USD"),)))
    assert "FIRST_CLOSE_REQUIRES_POSITIVE_BTC_USD_LONG" in wrong_symbol
    short = model._first_close_blockers(_portfolio((_position(side="short", quantity=Decimal("-0.1")),)))
    assert "FIRST_CLOSE_REQUIRES_POSITIVE_BTC_USD_LONG" in short
    zero = model._first_close_blockers(_portfolio((_position(quantity=Decimal("0")),)))
    assert "FIRST_CLOSE_REQUIRES_POSITIVE_BTC_USD_LONG" in zero
    with pytest.raises(model.PaperOperationsReadModelConflict, match="EXACTLY_ONE_POSITION"):
        model._first_close_position(_portfolio(()))
    assert model._target_quantity(_portfolio(())) == 0


def test_terminal_zero_recovery_guard_rejects_noncanonical_states() -> None:
    base = {
        "status": "CRYPTO_PAPER_FIRST_CANARY_RECOVERED_FLAT_NO_RETRY",
        "retry_post": False,
        "recovery_get_only": True,
        "live_trading": "BLOCKED",
    }
    model._require_proven_terminal_zero(base, kind="RECOVERY")

    for key, value, match in (
        ("retry_post", True, "retry/LIVE"),
        ("live_trading", "ENABLED", "retry/LIVE"),
        ("status", "UNKNOWN", "allowlisted"),
        ("recovery_get_only", False, "GET-only"),
    ):
        invalid = dict(base)
        invalid[key] = value
        with pytest.raises(model.PaperOperationsReadModelConflict, match=match):
            model._require_proven_terminal_zero(invalid, kind="RECOVERY")


def test_terminal_zero_initial_guard_checks_each_terminal_contract() -> None:
    base = {
        "status": "CRYPTO_PAPER_FIRST_CANARY_RECONCILED_FINAL_NO_RETRY",
        "retry_post": False,
        "live_trading": "BLOCKED",
        "persisted_final_resolution": True,
        "evidence_type": "ORDER_PLUS_POSITION",
        "broker_order_status": "rejected",
        "broker_filled_quantity": "0",
        "lifecycle_status": CryptoLifecycleStatus.ENTRY_TERMINAL_NO_FILL.value,
    }
    model._require_proven_terminal_zero(base, kind="INITIAL")

    cases = (
        ("status", "PENDING", "not terminal"),
        ("persisted_final_resolution", False, "not durable final"),
        ("evidence_type", "ORDER_ABSENCE", "evidence type"),
        ("broker_order_status", "filled", "terminal no-fill"),
        ("broker_filled_quantity", "0.1", "broker fill is not zero"),
        ("lifecycle_status", CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED.value, "lifecycle"),
    )
    for key, value, match in cases:
        invalid = dict(base)
        invalid[key] = value
        with pytest.raises(model.PaperOperationsReadModelConflict, match=match):
            model._require_proven_terminal_zero(invalid, kind="INITIAL")

    with pytest.raises(model.PaperOperationsReadModelConflict, match="unknown terminal"):
        model._require_proven_terminal_zero(base, kind="OTHER")


def test_terminal_document_and_source_discovery_distinguish_empty_from_absent_history(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id="first-canary-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    with pytest.raises(model.PaperOperationsReadModelConflict, match="no terminal reconciliation"):
        model._terminal_attempt_document(attempt)

    # FirstCanaryAttemptWorkspace.open deliberately creates the execution root.
    # With only an unburned attempt present, discovery correctly reports that no
    # certified source matches the current exposure rather than claiming history
    # is absent.
    with pytest.raises(model.PaperOperationsReadModelMissing, match="no certified first-canary source"):
        model.FirstCanaryCloseSourceDiscovery(workspace_path=workspace).discover(
            portfolio=_portfolio((_position(),)),
            now=NOW,
        )

    truly_empty = tmp_path / "no-history-workspace"
    truly_empty.mkdir()
    with pytest.raises(model.PaperOperationsReadModelMissing, match="execution history"):
        model.FirstCanaryCloseSourceDiscovery(workspace_path=truly_empty).discover(
            portfolio=_portfolio((_position(),)),
            now=NOW,
        )
