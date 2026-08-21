from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Mapping, Protocol

from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    ATTEMPT_ID_RE,
    EXECUTION_DIR,
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.paper_portfolio import (
    AlpacaPaperPortfolioGateway,
    PaperPortfolioPosition,
    PaperPortfolioSnapshot,
)
from autotrade.paper_close_source_provenance import (
    FirstCanaryCloseSourceReader,
    PaperCloseSourceProvenance,
    PaperCloseSourceProvenanceError,
)
from autotrade.state import SafetyControlState


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ACCOUNT_ID_RE = re.compile(r"^[0-9a-fA-F-]{16,64}$")
_FIRST_CLOSE_SYMBOL = "BTC/USD"
_TERMINAL_ZERO_RECOVERY_STATUSES = frozenset(
    {
        "CRYPTO_PAPER_FIRST_CANARY_RECOVERED_FLAT_NO_RETRY",
        "CRYPTO_PAPER_FIRST_CANARY_BURNED_PRE_WRITER_FLAT_NO_RETRY",
        "CRYPTO_PAPER_FIRST_CANARY_ALREADY_RECOVERED_NO_RETRY",
    }
)
_TERMINAL_NO_FILL_BROKER_STATUSES = frozenset({"canceled", "expired", "rejected"})


class PaperOperationsReadModelError(RuntimeError):
    pass


class PaperOperationsReadModelMissing(PaperOperationsReadModelError):
    pass


class PaperOperationsReadModelConflict(PaperOperationsReadModelError):
    pass


class PaperPortfolioReader(Protocol):
    def snapshot(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        expected_account_id: str,
        now: datetime,
    ) -> PaperPortfolioSnapshot: ...


@dataclass(frozen=True, slots=True)
class PaperWorkspaceAccountAnchor:
    attestation: AlpacaPaperAccountAttestation
    artifact_sha256: str
    anchor_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.attestation, AlpacaPaperAccountAttestation):
            raise ValueError("workspace PAPER account attestation is required")
        _require_hash(self.artifact_sha256, "account artifact sha256")
        _require_hash(self.anchor_hash, "account anchor hash")
        if self.anchor_hash != _hash_json(self._payload()):
            raise ValueError("workspace PAPER account anchor hash mismatch")

    def _payload(self) -> dict[str, object]:
        return {
            "account_id": self.attestation.account_id,
            "account_reference": self.attestation.account_reference,
            "attestation_fingerprint": self.attestation.fingerprint,
            "artifact_sha256": self.artifact_sha256,
            "source_host": self.attestation.source_host,
            "source_path": self.attestation.source_path,
        }


@dataclass(frozen=True, slots=True)
class PaperSafetyReadSnapshot:
    state: SafetyControlState
    core_db_sha256: str
    observed_at: datetime
    snapshot_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.state, SafetyControlState):
            raise ValueError("SafetyControlState is required")
        _require_hash(self.core_db_sha256, "core db sha256")
        _require_hash(self.snapshot_hash, "Safety snapshot hash")
        _require_aware(self.observed_at, "Safety observed_at")
        if self.snapshot_hash != _hash_json(self._payload()):
            raise ValueError("Safety read snapshot hash mismatch")

    def _payload(self) -> dict[str, object]:
        return {
            "circuit_active": self.state.circuit_active,
            "circuit_reason": self.state.circuit_reason,
            "core_db_sha256": self.core_db_sha256,
            "kill_switch_active": self.state.kill_switch_active,
            "kill_switch_reason": self.state.kill_switch_reason,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "updated_at": (
                None
                if self.state.updated_at is None
                else self.state.updated_at.astimezone(timezone.utc).isoformat()
            ),
            "version": self.state.version,
        }


@dataclass(frozen=True, slots=True)
class AccountBoundPaperCloseSource:
    source: PaperCloseSourceProvenance
    account_reference: str
    source_credential_reference: str
    prepared_account_fingerprint: str
    binding_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, PaperCloseSourceProvenance):
            raise ValueError("certified PaperCloseSourceProvenance is required")
        for label, value in (
            ("account_reference", self.account_reference),
            ("source_credential_reference", self.source_credential_reference),
            ("prepared_account_fingerprint", self.prepared_account_fingerprint),
            ("binding_hash", self.binding_hash),
        ):
            _require_hash(value, label)
        if (
            self.source.source_lifecycle.binding.account_attestation_fingerprint
            != self.prepared_account_fingerprint
        ):
            raise ValueError("source lifecycle account fingerprint differs from preparation")
        if self.binding_hash != _hash_json(self._payload()):
            raise ValueError("account-bound close source hash mismatch")

    def _payload(self) -> dict[str, object]:
        return {
            "account_reference": self.account_reference,
            "prepared_account_fingerprint": self.prepared_account_fingerprint,
            "source_credential_reference": self.source_credential_reference,
            "source_provenance_hash": self.source.provenance_hash,
        }


@dataclass(frozen=True, slots=True)
class PaperOperationsSnapshot:
    account_anchor: PaperWorkspaceAccountAnchor
    portfolio: PaperPortfolioSnapshot
    safety: PaperSafetyReadSnapshot
    close_source: AccountBoundPaperCloseSource | None
    blockers: tuple[str, ...]
    observed_at: datetime
    snapshot_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.account_anchor, PaperWorkspaceAccountAnchor):
            raise ValueError("workspace account anchor is required")
        if not isinstance(self.portfolio, PaperPortfolioSnapshot):
            raise ValueError("fresh PAPER portfolio is required")
        if not isinstance(self.safety, PaperSafetyReadSnapshot):
            raise ValueError("read-only Safety snapshot is required")
        if self.close_source is not None and not isinstance(
            self.close_source, AccountBoundPaperCloseSource
        ):
            raise ValueError("close source type is invalid")
        if not isinstance(self.blockers, tuple) or any(
            not isinstance(item, str) or not item for item in self.blockers
        ):
            raise ValueError("operations blockers must be a tuple of non-empty strings")
        _require_aware(self.observed_at, "operations observed_at")
        _require_hash(self.snapshot_hash, "operations snapshot hash")
        if self.portfolio.account.account_reference != self.account_anchor.attestation.account_reference:
            raise ValueError("broker Portfolio account differs from workspace account anchor")
        if self.close_source is not None:
            if self.close_source.account_reference != self.portfolio.account.account_reference:
                raise ValueError("close source belongs to another PAPER account")
            if self.close_source.source.confirmed_net_long_quantity != _target_quantity(self.portfolio):
                raise ValueError("close source quantity differs from fresh broker position")
        if self.snapshot_hash != _hash_json(self._payload()):
            raise ValueError("PAPER operations snapshot hash mismatch")

    @property
    def ready_for_close_preparation(self) -> bool:
        return not self.blockers and self.close_source is not None

    def to_dict(self) -> dict[str, object]:
        position = self.portfolio.positions[0].to_dict() if len(self.portfolio.positions) == 1 else None
        return {
            "environment": "PAPER",
            "account_id": self.portfolio.account.account_id,
            "account_reference": self.portfolio.account.account_reference,
            "portfolio_value": str(self.portfolio.account.portfolio_value),
            "buying_power": str(self.portfolio.account.buying_power),
            "position_count": len(self.portfolio.positions),
            "open_order_count": len(self.portfolio.open_orders),
            "position": position,
            "open_orders": [item.to_dict() for item in self.portfolio.open_orders],
            "portfolio_fingerprint": self.portfolio.fingerprint,
            "portfolio_observed_at": self.portfolio.observed_at.astimezone(timezone.utc).isoformat(),
            "safety": {
                "kill_switch_active": self.safety.state.kill_switch_active,
                "kill_switch_reason": self.safety.state.kill_switch_reason,
                "circuit_active": self.safety.state.circuit_active,
                "circuit_reason": self.safety.state.circuit_reason,
                "version": self.safety.state.version,
                "snapshot_hash": self.safety.snapshot_hash,
            },
            "source": (
                None
                if self.close_source is None
                else {
                    "attempt_id": self.close_source.source.attempt_id,
                    "strategy_id": self.close_source.source.strategy_id,
                    "broker_order_id": self.close_source.source.broker_order_id,
                    "broker_order_status": self.close_source.source.broker_order_status,
                    "gross_filled_quantity": str(self.close_source.source.gross_filled_quantity),
                    "confirmed_net_long_quantity": str(
                        self.close_source.source.confirmed_net_long_quantity
                    ),
                    "provenance_hash": self.close_source.source.provenance_hash,
                    "account_binding_hash": self.close_source.binding_hash,
                }
            ),
            "blockers": list(self.blockers),
            "ready_for_close_preparation": self.ready_for_close_preparation,
            "broker_write_authorized": False,
            "retry_post": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "snapshot_hash": self.snapshot_hash,
        }

    def _payload(self) -> dict[str, object]:
        return {
            "account_anchor_hash": self.account_anchor.anchor_hash,
            "blockers": list(self.blockers),
            "close_source_binding_hash": (
                None if self.close_source is None else self.close_source.binding_hash
            ),
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "portfolio_fingerprint": self.portfolio.fingerprint,
            "safety_snapshot_hash": self.safety.snapshot_hash,
        }


def read_workspace_paper_account(workspace_path: Path) -> PaperWorkspaceAccountAnchor:
    root = _workspace(workspace_path)
    path = root / "account_attestation.json"
    document, raw_bytes = _read_json_file(path, label="workspace PAPER account attestation")
    for key, value in (
        ("schema_version", 1),
        ("environment", "PAPER"),
        ("credentials_persisted", False),
        ("external_order_submitted", False),
        ("live_trading", "BLOCKED"),
    ):
        if document.get(key) != value:
            raise PaperOperationsReadModelConflict(f"workspace PAPER account authority mismatch: {key}")
    account_id = _text(document, "account_id")
    if not _ACCOUNT_ID_RE.fullmatch(account_id):
        raise PaperOperationsReadModelConflict("workspace PAPER account_id is malformed")
    account_reference = _hash_text(document, "account_reference")
    credential_reference = _hash_text(document, "credential_reference")
    source_host = _text(document, "source_host")
    source_path = _text(document, "source_path")
    if source_host != ALPACA_PAPER_TRADING_HOST or source_path != ALPACA_PAPER_ACCOUNT_PATH:
        raise PaperOperationsReadModelConflict("workspace account source is not exact Alpaca PAPER")
    shorting_enabled = document.get("shorting_enabled")
    if not isinstance(shorting_enabled, bool):
        raise PaperOperationsReadModelConflict("workspace account shorting flag is invalid")
    attested_at = _datetime(document.get("attested_at"), "account attested_at")
    try:
        attestation = AlpacaPaperAccountAttestation(
            account_id=account_id,
            account_reference=account_reference,
            credential_reference=credential_reference,
            status=_text(document, "status"),
            currency=_text(document, "currency"),
            buying_power=_decimal(document.get("buying_power"), "buying_power", nonnegative=True),
            portfolio_value=_decimal(
                document.get("portfolio_value"), "portfolio_value", nonnegative=True
            ),
            shorting_enabled=shorting_enabled,
            attested_at=attested_at,
            request_id=_text(document, "request_id"),
            source_host=source_host,
            source_path=source_path,
        )
    except (TypeError, ValueError) as exc:
        raise PaperOperationsReadModelConflict("workspace PAPER account attestation is invalid") from exc
    if attestation.status != "ACTIVE" or attestation.currency != "USD":
        raise PaperOperationsReadModelConflict("workspace PAPER account must be ACTIVE USD")
    if _hash_text(document, "attestation_fingerprint") != attestation.fingerprint:
        raise PaperOperationsReadModelConflict("workspace PAPER account fingerprint mismatch")
    artifact_sha256 = sha256(raw_bytes).hexdigest()
    values = {
        "account_id": attestation.account_id,
        "account_reference": attestation.account_reference,
        "attestation_fingerprint": attestation.fingerprint,
        "artifact_sha256": artifact_sha256,
        "source_host": attestation.source_host,
        "source_path": attestation.source_path,
    }
    return PaperWorkspaceAccountAnchor(
        attestation=attestation,
        artifact_sha256=artifact_sha256,
        anchor_hash=_hash_json(values),
    )


def read_paper_safety_snapshot(
    workspace_path: Path,
    *,
    now: datetime,
) -> PaperSafetyReadSnapshot:
    root = _workspace(workspace_path)
    instant = _aware_utc(now, "Safety read time")
    db_path = root / "core.sqlite3"
    _require_stable_sqlite(db_path, label="core.sqlite3")
    before_hash = _file_sha256(db_path)
    uri = f"{db_path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error as exc:
        raise PaperOperationsReadModelMissing("cannot open core.sqlite3 read-only") from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(safety_state)").fetchall()
        }
        required = {
            "singleton_id",
            "kill_switch_active",
            "kill_switch_reason",
            "circuit_active",
            "circuit_reason",
            "version",
            "updated_at",
        }
        if not required.issubset(columns):
            raise PaperOperationsReadModelConflict("durable Safety schema is incomplete for R7")
        rows = conn.execute(
            "SELECT kill_switch_active, kill_switch_reason, circuit_active, circuit_reason, version, updated_at "
            "FROM safety_state WHERE singleton_id=1"
        ).fetchall()
        if len(rows) != 1:
            raise PaperOperationsReadModelMissing("durable Safety singleton row is missing")
        row = rows[0]
        kill_active = _sqlite_flag(row["kill_switch_active"], "kill switch")
        circuit_active = _sqlite_flag(row["circuit_active"], "circuit")
        kill_reason = _plain_text(row["kill_switch_reason"], "kill-switch reason")
        circuit_reason = _plain_text(row["circuit_reason"], "circuit reason")
        if kill_active != bool(kill_reason):
            raise PaperOperationsReadModelConflict("kill-switch active/reason state is inconsistent")
        if circuit_active != bool(circuit_reason):
            raise PaperOperationsReadModelConflict("circuit active/reason state is inconsistent")
        version = row["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise PaperOperationsReadModelConflict("durable Safety version is invalid")
        updated_raw = row["updated_at"]
        updated_at = None
        if updated_raw is not None:
            updated_at = _datetime(updated_raw, "Safety updated_at")
        state = SafetyControlState(
            kill_switch_active=kill_active,
            kill_switch_reason=kill_reason,
            circuit_active=circuit_active,
            circuit_reason=circuit_reason,
            version=version,
            updated_at=updated_at,
        )
    except PaperOperationsReadModelError:
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        raise PaperOperationsReadModelConflict("durable Safety read failed closed") from exc
    finally:
        conn.close()
    after_hash = _file_sha256(db_path)
    if after_hash != before_hash:
        raise PaperOperationsReadModelConflict("core.sqlite3 changed during read-only Safety snapshot")
    _require_stable_sqlite(db_path, label="core.sqlite3")
    values = {
        "circuit_active": state.circuit_active,
        "circuit_reason": state.circuit_reason,
        "core_db_sha256": before_hash,
        "kill_switch_active": state.kill_switch_active,
        "kill_switch_reason": state.kill_switch_reason,
        "observed_at": instant.isoformat(),
        "updated_at": None if state.updated_at is None else state.updated_at.astimezone(timezone.utc).isoformat(),
        "version": state.version,
    }
    return PaperSafetyReadSnapshot(
        state=state,
        core_db_sha256=before_hash,
        observed_at=instant,
        snapshot_hash=_hash_json(values),
    )


class FirstCanaryCloseSourceDiscovery:
    """Find exactly one terminal first-canary source for the current broker exposure.

    Unburned preparations are irrelevant. Any burned attempt without terminal
    reconciliation blocks discovery. Proven terminal zero-exposure attempts are
    ignored; a non-zero terminal source must match the exact fresh broker quantity
    and the exact PAPER account before full provenance verification is accepted.
    """

    def __init__(
        self,
        *,
        workspace_path: Path,
        reader_factory: Callable[..., FirstCanaryCloseSourceReader] = FirstCanaryCloseSourceReader,
    ) -> None:
        self._workspace = _workspace(workspace_path)
        self._reader_factory = reader_factory

    def discover(
        self,
        *,
        portfolio: PaperPortfolioSnapshot,
        now: datetime,
    ) -> AccountBoundPaperCloseSource:
        instant = _aware_utc(now, "source discovery time")
        position = _first_close_position(portfolio)
        execution_root = self._workspace / EXECUTION_DIR
        if execution_root.is_symlink() or not execution_root.is_dir():
            raise PaperOperationsReadModelMissing("first-canary execution history is missing or unsafe")
        candidates: list[AccountBoundPaperCloseSource] = []
        for child in sorted(execution_root.iterdir(), key=lambda item: item.name):
            if not ATTEMPT_ID_RE.fullmatch(child.name):
                continue
            if child.is_symlink() or not child.is_dir():
                raise PaperOperationsReadModelConflict("first-canary history contains unsafe attempt directory")
            attempt = FirstCanaryAttemptWorkspace(
                workspace_root=self._workspace,
                attempt_id=child.name,
            )
            if not attempt.execution_started_path.exists():
                continue
            started = _read_hashed_attempt(
                attempt,
                attempt.execution_started_path,
                hash_key="execution_started_hash",
                label="execution-start latch",
            )
            if started.get("retry_forbidden") is not True or started.get("live_trading") != "BLOCKED":
                raise PaperOperationsReadModelConflict("burned attempt replay/LIVE latch is invalid")
            preparation = _read_hashed_attempt(
                attempt,
                attempt.preparation_path,
                hash_key="preparation_hash",
                label="first-canary preparation",
            )
            source_account_reference = _hash_text(preparation, "account_reference")
            source_credential_reference = _hash_text(preparation, "credential_reference")
            prepared_account_fingerprint = _hash_text(
                preparation, "prepared_account_fingerprint"
            )
            if source_account_reference != portfolio.account.account_reference:
                raise PaperOperationsReadModelConflict(
                    "burned first-canary attempt belongs to another PAPER account"
                )
            terminal, terminal_kind = _terminal_attempt_document(attempt)
            terminal_quantity = _decimal(
                terminal.get("position_quantity"),
                "terminal position_quantity",
                nonnegative=True,
            )
            if terminal_quantity == 0:
                _require_proven_terminal_zero(terminal, kind=terminal_kind)
                continue
            if terminal_quantity != position.quantity:
                raise PaperOperationsReadModelConflict(
                    "terminal first-canary exposure differs from fresh broker position"
                )
            try:
                source = self._reader_factory(
                    workspace_path=self._workspace,
                    attempt_id=child.name,
                ).verify(now=instant)
            except PaperCloseSourceProvenanceError as exc:
                raise PaperOperationsReadModelConflict(
                    "non-zero first-canary source failed certified provenance verification"
                ) from exc
            if source.preparation_hash != preparation.get("preparation_hash"):
                raise PaperOperationsReadModelConflict("source provenance preparation hash drifted")
            if source.confirmed_net_long_quantity != terminal_quantity:
                raise PaperOperationsReadModelConflict("source provenance net quantity drifted")
            if (
                source.source_lifecycle.binding.account_attestation_fingerprint
                != prepared_account_fingerprint
            ):
                raise PaperOperationsReadModelConflict(
                    "source lifecycle account fingerprint differs from hashed preparation"
                )
            values = {
                "account_reference": source_account_reference,
                "prepared_account_fingerprint": prepared_account_fingerprint,
                "source_credential_reference": source_credential_reference,
                "source_provenance_hash": source.provenance_hash,
            }
            candidates.append(
                AccountBoundPaperCloseSource(
                    source=source,
                    account_reference=source_account_reference,
                    source_credential_reference=source_credential_reference,
                    prepared_account_fingerprint=prepared_account_fingerprint,
                    binding_hash=_hash_json(values),
                )
            )
        if not candidates:
            raise PaperOperationsReadModelMissing(
                "no certified first-canary source matches the current BTC/USD PAPER exposure"
            )
        if len(candidates) != 1:
            raise PaperOperationsReadModelConflict(
                "multiple certified first-canary sources match the same broker exposure"
            )
        return candidates[0]


class PaperOperationsReadModel:
    """R7 broker-truth portfolio view with no broker-write surface."""

    def __init__(
        self,
        *,
        workspace_path: Path,
        portfolio_reader: PaperPortfolioReader | None = None,
        source_discovery_factory: Callable[..., FirstCanaryCloseSourceDiscovery] = FirstCanaryCloseSourceDiscovery,
    ) -> None:
        self._workspace = _workspace(workspace_path)
        self._portfolio_reader = portfolio_reader or AlpacaPaperPortfolioGateway(
            config=AlpacaPaperGatewayConfig(enabled=True)
        )
        self._source_discovery_factory = source_discovery_factory

    def snapshot(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        now: datetime,
    ) -> PaperOperationsSnapshot:
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise TypeError("ephemeral Alpaca PAPER credentials are required")
        instant = _aware_utc(now, "operations snapshot time")
        account_anchor = read_workspace_paper_account(self._workspace)
        portfolio = self._portfolio_reader.snapshot(
            credentials=credentials,
            expected_account_id=account_anchor.attestation.account_id,
            now=instant,
        )
        if portfolio.account.account_reference != account_anchor.attestation.account_reference:
            raise PaperOperationsReadModelConflict(
                "fresh broker account_reference differs from workspace PAPER anchor"
            )
        if portfolio.account.credential_reference != credentials.credential_reference:
            raise PaperOperationsReadModelConflict("fresh Portfolio credential binding mismatch")
        safety = read_paper_safety_snapshot(self._workspace, now=instant)
        blockers = _first_close_blockers(portfolio)
        source: AccountBoundPaperCloseSource | None = None
        if not blockers:
            try:
                source = self._source_discovery_factory(
                    workspace_path=self._workspace
                ).discover(portfolio=portfolio, now=instant)
            except PaperOperationsReadModelMissing as exc:
                blockers = (f"SOURCE_NOT_CERTIFIED:{exc}",)
        values = {
            "account_anchor_hash": account_anchor.anchor_hash,
            "blockers": list(blockers),
            "close_source_binding_hash": None if source is None else source.binding_hash,
            "observed_at": instant.isoformat(),
            "portfolio_fingerprint": portfolio.fingerprint,
            "safety_snapshot_hash": safety.snapshot_hash,
        }
        return PaperOperationsSnapshot(
            account_anchor=account_anchor,
            portfolio=portfolio,
            safety=safety,
            close_source=source,
            blockers=blockers,
            observed_at=instant,
            snapshot_hash=_hash_json(values),
        )


def _first_close_blockers(portfolio: PaperPortfolioSnapshot) -> tuple[str, ...]:
    blockers: list[str] = []
    if len(portfolio.positions) != 1:
        blockers.append("FIRST_CLOSE_REQUIRES_EXACTLY_ONE_POSITION")
    if portfolio.open_orders:
        blockers.append("FIRST_CLOSE_REQUIRES_ZERO_OPEN_ORDERS")
    if len(portfolio.positions) == 1:
        position = portfolio.positions[0]
        if (
            position.symbol != _FIRST_CLOSE_SYMBOL
            or position.asset_class != "crypto"
            or position.side != "long"
            or position.quantity <= 0
        ):
            blockers.append("FIRST_CLOSE_REQUIRES_POSITIVE_BTC_USD_LONG")
        elif position.available_quantity != position.quantity:
            blockers.append("FIRST_CLOSE_REQUIRES_FULL_POSITION_AVAILABLE")
    return tuple(blockers)


def _first_close_position(portfolio: PaperPortfolioSnapshot) -> PaperPortfolioPosition:
    blockers = _first_close_blockers(portfolio)
    if blockers:
        raise PaperOperationsReadModelConflict(";".join(blockers))
    return portfolio.positions[0]


def _target_quantity(portfolio: PaperPortfolioSnapshot) -> Decimal:
    if len(portfolio.positions) != 1:
        return Decimal("0")
    return portfolio.positions[0].quantity


def _terminal_attempt_document(
    attempt: FirstCanaryAttemptWorkspace,
) -> tuple[dict[str, object], str]:
    if attempt.recovery_resolution_path.exists():
        return (
            _read_hashed_attempt(
                attempt,
                attempt.recovery_resolution_path,
                hash_key="recovery_resolution_hash",
                label="GET-only recovery resolution",
            ),
            "RECOVERY",
        )
    if attempt.reconciliation_path.exists():
        return (
            _read_hashed_attempt(
                attempt,
                attempt.reconciliation_path,
                hash_key="reconciliation_hash",
                label="initial reconciliation",
            ),
            "INITIAL",
        )
    raise PaperOperationsReadModelConflict(
        "burned first-canary attempt has no terminal reconciliation; recovery is required before any close"
    )


def _require_proven_terminal_zero(document: Mapping[str, object], *, kind: str) -> None:
    if document.get("retry_post") is not False or document.get("live_trading") != "BLOCKED":
        raise PaperOperationsReadModelConflict("zero-exposure terminal attempt violates retry/LIVE deny")
    if kind == "RECOVERY":
        status = document.get("status")
        if status not in _TERMINAL_ZERO_RECOVERY_STATUSES:
            raise PaperOperationsReadModelConflict(
                "zero-exposure recovery status is not an allowlisted terminal-flat state"
            )
        if document.get("recovery_get_only") is not True:
            raise PaperOperationsReadModelConflict("zero-exposure recovery is not GET-only")
        return
    if kind != "INITIAL":
        raise PaperOperationsReadModelConflict("unknown terminal attempt kind")
    if document.get("status") != "CRYPTO_PAPER_FIRST_CANARY_RECONCILED_FINAL_NO_RETRY":
        raise PaperOperationsReadModelConflict("zero-exposure initial reconciliation is not terminal")
    if document.get("persisted_final_resolution") is not True:
        raise PaperOperationsReadModelConflict("zero-exposure initial resolution is not durable final")
    if document.get("evidence_type") != "ORDER_PLUS_POSITION":
        raise PaperOperationsReadModelConflict("zero-exposure initial evidence type is invalid")
    if str(document.get("broker_order_status") or "").strip().lower() not in _TERMINAL_NO_FILL_BROKER_STATUSES:
        raise PaperOperationsReadModelConflict("zero-exposure broker order is not terminal no-fill")
    if _decimal(document.get("broker_filled_quantity"), "broker_filled_quantity", nonnegative=True) != 0:
        raise PaperOperationsReadModelConflict("zero-exposure terminal broker fill is not zero")
    if document.get("lifecycle_status") != CryptoLifecycleStatus.ENTRY_TERMINAL_NO_FILL.value:
        raise PaperOperationsReadModelConflict("zero-exposure lifecycle is not terminal no-fill")


def _read_hashed_attempt(
    attempt: FirstCanaryAttemptWorkspace,
    path: Path,
    *,
    hash_key: str,
    label: str,
) -> dict[str, object]:
    try:
        document = attempt.read(path=path)
        attempt.require_document_hash(document, hash_key=hash_key, label=label)
        return document
    except Exception as exc:
        raise PaperOperationsReadModelConflict(f"{label} is missing or invalid") from exc


def _workspace(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("workspace_path must be pathlib.Path")
    raw = path.expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise PaperOperationsReadModelMissing("existing non-symlink PAPER workspace is required")
    return raw.resolve()


def _read_json_file(path: Path, *, label: str) -> tuple[dict[str, object], bytes]:
    if path.is_symlink() or not path.is_file():
        raise PaperOperationsReadModelMissing(f"{label} is missing or unsafe")
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaperOperationsReadModelConflict(f"{label} is unreadable") from exc
    if not isinstance(document, dict):
        raise PaperOperationsReadModelConflict(f"{label} root must be an object")
    return document, raw


def _require_stable_sqlite(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise PaperOperationsReadModelMissing(f"{label} is missing or unsafe")
    for suffix in ("-wal", "-shm"):
        if Path(str(path) + suffix).exists():
            raise PaperOperationsReadModelConflict(
                f"{label} has active WAL/SHM sidecars; close/checkpoint writers before R7 Safety read"
            )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PaperOperationsReadModelMissing("cannot hash durable SQLite file") from exc
    return digest.hexdigest()


def _sqlite_flag(value: object, label: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
        raise PaperOperationsReadModelConflict(f"{label} flag is invalid")
    return bool(value)


def _plain_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PaperOperationsReadModelConflict(f"{label} must be text")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PaperOperationsReadModelConflict(f"{label} contains control characters")
    return value


def _text(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PaperOperationsReadModelConflict(f"{key} must be non-empty text")
    return value.strip()


def _hash_text(document: Mapping[str, object], key: str) -> str:
    value = _text(document, key)
    _require_hash(value, key)
    return value


def _decimal(value: object, label: str, *, nonnegative: bool) -> Decimal:
    if not isinstance(value, str) or not value:
        raise PaperOperationsReadModelConflict(f"{label} must be decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise PaperOperationsReadModelConflict(f"{label} is invalid decimal") from exc
    if not number.is_finite() or (nonnegative and number < 0):
        raise PaperOperationsReadModelConflict(f"{label} is outside allowed decimal domain")
    return number


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise PaperOperationsReadModelConflict(f"{label} must be datetime text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PaperOperationsReadModelConflict(f"{label} is invalid datetime") from exc
    return _aware_utc(parsed, label)


def _aware_utc(value: datetime, label: str) -> datetime:
    _require_aware(value, label)
    return value.astimezone(timezone.utc)


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _hash_json(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


__all__ = [
    "AccountBoundPaperCloseSource",
    "FirstCanaryCloseSourceDiscovery",
    "PaperOperationsReadModel",
    "PaperOperationsReadModelConflict",
    "PaperOperationsReadModelError",
    "PaperOperationsReadModelMissing",
    "PaperOperationsSnapshot",
    "PaperSafetyReadSnapshot",
    "PaperWorkspaceAccountAnchor",
    "read_paper_safety_snapshot",
    "read_workspace_paper_account",
]
