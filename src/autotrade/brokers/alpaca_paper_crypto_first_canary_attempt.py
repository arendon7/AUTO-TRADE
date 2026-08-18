from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping


EXECUTION_DIR = "first_canary_execution"
ATTEMPT_DB_NAME = "attempt.sqlite3"
ATTEMPT_ID_RE = re.compile(r"^first-canary-[0-9a-f]{32}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class CryptoFirstCanaryAttemptError(RuntimeError):
    pass


class CryptoFirstCanaryAttemptConflict(CryptoFirstCanaryAttemptError):
    pass


class CryptoFirstCanaryAttemptIntegrityError(CryptoFirstCanaryAttemptError):
    pass


@dataclass(frozen=True, slots=True)
class FirstCanaryAttemptWorkspace:
    workspace_root: Path
    attempt_id: str

    def __post_init__(self) -> None:
        root = self.workspace_root
        if not isinstance(root, Path):
            raise TypeError("workspace_root must be pathlib.Path")
        if root.is_symlink() or not root.is_dir():
            raise CryptoFirstCanaryAttemptIntegrityError(
                "existing non-symlink workspace root is required"
            )
        if not ATTEMPT_ID_RE.fullmatch(self.attempt_id):
            raise CryptoFirstCanaryAttemptIntegrityError("execution attempt_id is invalid")

    @classmethod
    def open(cls, *, workspace_path: Path, attempt_id: str) -> "FirstCanaryAttemptWorkspace":
        if not isinstance(workspace_path, Path):
            raise TypeError("workspace_path must be pathlib.Path")
        raw = workspace_path.expanduser()
        if raw.is_symlink() or not raw.is_dir():
            raise CryptoFirstCanaryAttemptIntegrityError(
                "existing non-symlink workspace root is required"
            )
        value = cls(workspace_root=raw.resolve(), attempt_id=attempt_id)
        value._ensure_directory(value.execution_root)
        value._ensure_directory(value.attempt_root)
        if value.database_path.is_symlink():
            raise CryptoFirstCanaryAttemptIntegrityError(
                "execution attempt database may not be a symlink"
            )
        return value

    @property
    def execution_root(self) -> Path:
        return self.workspace_root / EXECUTION_DIR

    @property
    def attempt_root(self) -> Path:
        return self.execution_root / self.attempt_id

    @property
    def database_path(self) -> Path:
        return self.attempt_root / ATTEMPT_DB_NAME

    @property
    def preparation_path(self) -> Path:
        return self.attempt_root / "preparation.json"

    @property
    def approval_receipt_path(self) -> Path:
        return self.attempt_root / "approval.json"

    @property
    def execution_result_path(self) -> Path:
        return self.attempt_root / "execution_result.json"

    @property
    def reconciliation_path(self) -> Path:
        return self.attempt_root / "reconciliation.json"

    def write_once(self, *, path: Path, document: Mapping[str, object]) -> Path:
        self._require_child(path)
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise CryptoFirstCanaryAttemptIntegrityError(
                    f"existing attempt artifact is unsafe: {path.name}"
                )
            existing = self.read(path=path)
            if existing != dict(document):
                raise CryptoFirstCanaryAttemptConflict(
                    f"attempt artifact already exists with different content: {path.name}"
                )
            return path
        encoded = json.dumps(
            dict(document), sort_keys=True, indent=2, allow_nan=False
        ) + "\n"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
            path.chmod(0o600)
        except OSError as exc:
            raise CryptoFirstCanaryAttemptIntegrityError(
                f"cannot persist attempt artifact: {path.name}"
            ) from exc
        return path

    def read(self, *, path: Path) -> dict[str, object]:
        self._require_child(path)
        if path.is_symlink() or not path.is_file():
            raise CryptoFirstCanaryAttemptIntegrityError(
                f"attempt artifact is missing or unsafe: {path.name}"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CryptoFirstCanaryAttemptIntegrityError(
                f"attempt artifact is unreadable: {path.name}"
            ) from exc
        if not isinstance(payload, dict):
            raise CryptoFirstCanaryAttemptIntegrityError(
                f"attempt artifact root must be object: {path.name}"
            )
        return payload

    def assert_unexecuted(self) -> None:
        for path in (self.execution_result_path, self.reconciliation_path):
            if path.exists():
                raise CryptoFirstCanaryAttemptConflict(
                    "attempt already has execution/reconciliation evidence; POST replay is forbidden"
                )

    @staticmethod
    def document_hash(document: Mapping[str, object], *, hash_key: str) -> str:
        material = {key: value for key, value in dict(document).items() if key != hash_key}
        return sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        ).hexdigest()

    @staticmethod
    def require_document_hash(
        document: Mapping[str, object], *, hash_key: str, label: str
    ) -> str:
        supplied = document.get(hash_key)
        if not isinstance(supplied, str) or not _HASH_RE.fullmatch(supplied):
            raise CryptoFirstCanaryAttemptIntegrityError(f"{label} hash is missing or invalid")
        if supplied != FirstCanaryAttemptWorkspace.document_hash(document, hash_key=hash_key):
            raise CryptoFirstCanaryAttemptIntegrityError(f"{label} hash mismatch")
        return supplied

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        if path.exists() and (path.is_symlink() or not path.is_dir()):
            raise CryptoFirstCanaryAttemptIntegrityError(
                f"attempt directory is unsafe: {path.name}"
            )
        path.mkdir(mode=0o700, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError as exc:
            raise CryptoFirstCanaryAttemptIntegrityError(
                f"cannot restrict attempt directory permissions: {path.name}"
            ) from exc

    def _require_child(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("attempt artifact path must be pathlib.Path")
        if path.parent != self.attempt_root:
            raise CryptoFirstCanaryAttemptIntegrityError(
                "attempt artifact must be an immediate child of the exact attempt directory"
            )


__all__ = [
    "ATTEMPT_DB_NAME",
    "ATTEMPT_ID_RE",
    "EXECUTION_DIR",
    "CryptoFirstCanaryAttemptConflict",
    "CryptoFirstCanaryAttemptError",
    "CryptoFirstCanaryAttemptIntegrityError",
    "FirstCanaryAttemptWorkspace",
]
