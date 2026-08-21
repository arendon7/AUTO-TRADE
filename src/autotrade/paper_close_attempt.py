from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Mapping

from autotrade.paper_close_lifecycle import (
    PaperCloseLifecycleIntegrityError,
    PaperCloseLifecycleStatus,
    _event_from_row,
    _state_from_row,
    _verify_event_chain,
)
from autotrade.paper_close_plan import PaperCloseMode, PaperCryptoClosePlan


CLOSE_ATTEMPT_DIR = "r7_paper_close"
PLAN_FILENAME = "plan.json"
DATABASE_FILENAME = "close.sqlite3"
WRITE_RECEIPT_FILENAME = "write_receipt.json"
_ATTEMPT_RE = re.compile(r"^r7-close-[0-9a-f]{32}$")


class PaperCloseAttemptError(RuntimeError):
    pass


class PaperCloseAttemptConflict(PaperCloseAttemptError):
    pass


class PaperCloseAttemptIntegrityError(PaperCloseAttemptError):
    pass


@dataclass(frozen=True, slots=True)
class PaperCloseAttemptWorkspace:
    workspace_root: Path
    attempt_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_root, Path):
            raise TypeError("workspace_root must be pathlib.Path")
        if not isinstance(self.attempt_id, str) or not _ATTEMPT_RE.fullmatch(self.attempt_id):
            raise ValueError("R7 close attempt_id is invalid")

    @property
    def root(self) -> Path:
        return self.workspace_root / CLOSE_ATTEMPT_DIR / self.attempt_id

    @property
    def plan_path(self) -> Path:
        return self.root / PLAN_FILENAME

    @property
    def database_path(self) -> Path:
        return self.root / DATABASE_FILENAME

    @property
    def write_receipt_path(self) -> Path:
        return self.root / WRITE_RECEIPT_FILENAME

    @classmethod
    def create(cls, *, workspace_path: Path, attempt_id: str) -> "PaperCloseAttemptWorkspace":
        root = _workspace(workspace_path)
        close_root = root / CLOSE_ATTEMPT_DIR
        _mkdir_private(close_root)
        attempt = cls(workspace_root=root, attempt_id=attempt_id)
        if attempt.root.exists():
            if attempt.root.is_symlink() or not attempt.root.is_dir():
                raise PaperCloseAttemptIntegrityError("R7 close attempt path is unsafe")
        else:
            attempt.root.mkdir(mode=0o700)
        _chmod_private(attempt.root)
        return attempt

    @classmethod
    def open(cls, *, workspace_path: Path, attempt_id: str) -> "PaperCloseAttemptWorkspace":
        root = _workspace(workspace_path)
        attempt = cls(workspace_root=root, attempt_id=attempt_id)
        if attempt.root.is_symlink() or not attempt.root.is_dir():
            raise PaperCloseAttemptIntegrityError("R7 close attempt directory is missing or unsafe")
        return attempt

    def write_plan(self, plan: PaperCryptoClosePlan) -> None:
        if not isinstance(plan, PaperCryptoClosePlan):
            raise TypeError("PaperCryptoClosePlan is required")
        self._write_once(self.plan_path, plan.to_dict())

    def read_plan(self) -> PaperCryptoClosePlan:
        document = self._read_json(self.plan_path, "R7 close plan")
        try:
            return paper_close_plan_from_dict(document)
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise PaperCloseAttemptIntegrityError("R7 close plan artifact is invalid") from exc

    def write_receipt(self, document: Mapping[str, object]) -> None:
        if not isinstance(document, Mapping):
            raise TypeError("receipt document must be a mapping")
        forbidden = {"paper_key", "paper_secret", "credentials", "secret", "api_key", "api_secret"}
        if forbidden & {str(key).lower() for key in document}:
            raise PaperCloseAttemptIntegrityError("R7 close receipt may not persist credentials")
        self._write_once(self.write_receipt_path, dict(document))

    def _write_once(self, path: Path, document: Mapping[str, object]) -> None:
        if path.parent != self.root or self.root.is_symlink() or not self.root.is_dir():
            raise PaperCloseAttemptIntegrityError("R7 close write destination is unsafe")
        raw = _canonical_json(document)
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise PaperCloseAttemptIntegrityError("R7 close artifact path is unsafe")
            try:
                existing = path.read_bytes()
            except OSError as exc:
                raise PaperCloseAttemptIntegrityError("cannot read existing R7 close artifact") from exc
            if existing != raw:
                raise PaperCloseAttemptConflict("R7 close artifact already exists with different content")
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(fd)
        _chmod_file_private(path)

    @staticmethod
    def _read_json(path: Path, label: str) -> dict[str, object]:
        if path.is_symlink() or not path.is_file():
            raise PaperCloseAttemptIntegrityError(f"{label} is missing or unsafe")
        try:
            value = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, InvalidOperation) as exc:
            raise PaperCloseAttemptIntegrityError(f"{label} is unreadable") from exc
        if not isinstance(value, dict):
            raise PaperCloseAttemptIntegrityError(f"{label} root must be object")
        return value


def paper_close_plan_from_dict(document: Mapping[str, object]) -> PaperCryptoClosePlan:
    if not isinstance(document, Mapping):
        raise TypeError("close plan document must be a mapping")
    return PaperCryptoClosePlan(
        account_reference=_text(document, "account_reference"),
        credential_reference=_text(document, "credential_reference"),
        portfolio_fingerprint=_text(document, "portfolio_fingerprint"),
        broker_symbol=_text(document, "broker_symbol"),
        symbol=_text(document, "symbol"),
        asset_class=_text(document, "asset_class"),
        mode=PaperCloseMode(_text(document, "mode")),
        side=_text(document, "side"),
        quantity=_decimal(document, "quantity"),
        observed_position_quantity=_decimal(document, "observed_position_quantity"),
        observed_available_quantity=_decimal(document, "observed_available_quantity"),
        reference_price=_decimal(document, "reference_price"),
        limit_price=_decimal(document, "limit_price"),
        max_slippage_bps=_decimal(document, "max_slippage_bps", allow_zero=True),
        order_type=_text(document, "order_type"),
        time_in_force=_text(document, "time_in_force"),
        prepared_at=_datetime(document, "prepared_at"),
        expires_at=_datetime(document, "expires_at"),
        risk_reducing=_bool(document, "risk_reducing"),
        network_write_authorized=_bool(document, "network_write_authorized"),
        retry_post=_bool(document, "retry_post"),
        live_trading=_text(document, "live_trading"),
        plan_hash=_text(document, "plan_hash"),
    )


def pending_burned_close_attempts(*, workspace_path: Path) -> tuple[str, ...]:
    """Return exact close attempts whose sole POST authority has already been burned.

    Discovery is read-only: SQLite is opened mode=ro&immutable=1 and both the
    state control hash and event chain are verified. PREPARED attempts are not
    considered burned because submission_attempt_count is still zero.
    """

    root = _workspace(workspace_path)
    close_root = root / CLOSE_ATTEMPT_DIR
    if not close_root.exists():
        return ()
    if close_root.is_symlink() or not close_root.is_dir():
        raise PaperCloseAttemptIntegrityError("R7 close history root is unsafe")
    pending: list[str] = []
    for child in sorted(close_root.iterdir(), key=lambda item: item.name):
        if not _ATTEMPT_RE.fullmatch(child.name):
            continue
        if child.is_symlink() or not child.is_dir():
            raise PaperCloseAttemptIntegrityError("R7 close history contains unsafe attempt path")
        attempt = PaperCloseAttemptWorkspace(workspace_root=root, attempt_id=child.name)
        if not attempt.database_path.exists():
            continue
        state = _read_lifecycle_read_only(attempt.database_path, attempt.attempt_id)
        if state.submission_attempt_count == 0:
            if state.status is not PaperCloseLifecycleStatus.PREPARED:
                raise PaperCloseAttemptIntegrityError("zero-submit R7 close attempt has invalid lifecycle state")
            continue
        if state.submission_attempt_count != 1:
            raise PaperCloseAttemptIntegrityError("R7 close attempt violates one-shot submission count")
        if state.status not in {
            PaperCloseLifecycleStatus.FLAT_RECONCILED,
            PaperCloseLifecycleStatus.TERMINAL_RECONCILED,
        }:
            pending.append(attempt.attempt_id)
    return tuple(pending)


def _read_lifecycle_read_only(path: Path, attempt_id: str):
    if path.is_symlink() or not path.is_file():
        raise PaperCloseAttemptIntegrityError("R7 close SQLite path is missing or unsafe")
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            raise PaperCloseAttemptIntegrityError("R7 close SQLite is not checkpoint-stable")
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error as exc:
        raise PaperCloseAttemptIntegrityError("cannot open R7 close lifecycle read-only") from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute(
            "SELECT state_json, control_hash FROM r7_paper_close_control WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise PaperCloseAttemptIntegrityError("R7 close lifecycle state is missing")
        state = _state_from_row(row)
        rows = conn.execute(
            "SELECT * FROM r7_paper_close_events WHERE attempt_id = ? ORDER BY sequence",
            (attempt_id,),
        ).fetchall()
        events = tuple(_event_from_row(item) for item in rows)
        _verify_event_chain(state, events)
        return state
    except PaperCloseLifecycleIntegrityError as exc:
        raise PaperCloseAttemptIntegrityError("R7 close lifecycle integrity verification failed") from exc
    except sqlite3.Error as exc:
        raise PaperCloseAttemptIntegrityError("R7 close lifecycle read failed") from exc
    finally:
        conn.close()


def _workspace(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("workspace_path must be pathlib.Path")
    expanded = path.expanduser()
    if expanded.is_symlink() or not expanded.is_dir():
        raise PaperCloseAttemptIntegrityError("existing non-symlink PAPER workspace is required")
    return expanded.resolve()


def _mkdir_private(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise PaperCloseAttemptIntegrityError("R7 close directory is unsafe")
    else:
        path.mkdir(mode=0o700)
    _chmod_private(path)


def _chmod_private(path: Path) -> None:
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise PaperCloseAttemptIntegrityError("cannot restrict R7 close directory permissions") from exc


def _chmod_file_private(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise PaperCloseAttemptIntegrityError("cannot restrict R7 close artifact permissions") from exc


def _canonical_json(document: Mapping[str, object]) -> bytes:
    try:
        return (json.dumps(dict(document), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PaperCloseAttemptIntegrityError("R7 close artifact is not canonical JSON serializable") from exc


def _text(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"R7 close plan {key} must be canonical non-empty text")
    return value


def _decimal(document: Mapping[str, object], key: str, *, allow_zero: bool = False) -> Decimal:
    value = document.get(key)
    if isinstance(value, bool) or value is None:
        raise ValueError(f"R7 close plan {key} must be decimal")
    parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    if not parsed.is_finite() or parsed < 0 or (parsed == 0 and not allow_zero):
        raise ValueError(f"R7 close plan {key} is outside allowed range")
    return parsed


def _datetime(document: Mapping[str, object], key: str) -> datetime:
    value = _text(document, key)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"R7 close plan {key} must be timezone-aware")
    return parsed


def _bool(document: Mapping[str, object], key: str) -> bool:
    value = document.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"R7 close plan {key} must be boolean")
    return value


__all__ = [
    "CLOSE_ATTEMPT_DIR",
    "DATABASE_FILENAME",
    "PLAN_FILENAME",
    "WRITE_RECEIPT_FILENAME",
    "PaperCloseAttemptConflict",
    "PaperCloseAttemptError",
    "PaperCloseAttemptIntegrityError",
    "PaperCloseAttemptWorkspace",
    "paper_close_plan_from_dict",
    "pending_burned_close_attempts",
]
