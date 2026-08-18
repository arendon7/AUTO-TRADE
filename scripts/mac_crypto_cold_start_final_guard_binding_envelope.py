from __future__ import annotations

import json
from pathlib import Path

import mac_crypto_cold_start_final_guard_binding as core


_ENVELOPE_KEYS = frozenset(
    {
        "status",
        "mode",
        "workspace",
        "preparation_path",
        "next_action",
    }
)
_EXPECTED_STATUS = "CRYPTO_COLD_START_FINAL_GUARD_BINDING_PREPARED_NO_EXECUTION"
_EXPECTED_MODE = "PAPER_READ_LOCAL_BINDING_NO_POST"
_EXPECTED_NEXT_ACTION = "TYPE_EXACT_CHALLENGE_TO_SEAL_UAT_BINDING_WITH_CANONICAL_ISSUER"


def _canonical_preparation(
    *,
    workspace_path: Path,
    preparation: dict[str, object],
) -> dict[str, object]:
    if not isinstance(preparation, dict):
        raise TypeError("binding preparation must be a dict")

    # Direct material remains supported for unit-level core verification. The
    # actual Mac dashboard path always carries the canonical prepare envelope.
    if not any(key in preparation for key in _ENVELOPE_KEYS):
        return dict(preparation)

    missing = _ENVELOPE_KEYS.difference(preparation)
    if missing:
        raise core.CryptoColdStartFinalGuardBindingError(
            f"binding preparation envelope is incomplete: {sorted(missing)}"
        )
    if preparation.get("status") != _EXPECTED_STATUS:
        raise core.CryptoColdStartFinalGuardBindingError("binding preparation envelope status mismatch")
    if preparation.get("mode") != _EXPECTED_MODE:
        raise core.CryptoColdStartFinalGuardBindingError("binding preparation envelope mode mismatch")
    if preparation.get("next_action") != _EXPECTED_NEXT_ACTION:
        raise core.CryptoColdStartFinalGuardBindingError("binding preparation envelope next_action mismatch")

    root = core._root(workspace_path)
    workspace_raw = preparation.get("workspace")
    if not isinstance(workspace_raw, str) or not workspace_raw:
        raise core.CryptoColdStartFinalGuardBindingError("binding preparation envelope workspace is missing")
    workspace = Path(workspace_raw).expanduser()
    if workspace.is_symlink() or not workspace.is_dir() or workspace.resolve() != root:
        raise core.CryptoColdStartFinalGuardBindingError(
            "binding preparation envelope workspace does not match seal workspace"
        )

    path_raw = preparation.get("preparation_path")
    if not isinstance(path_raw, str) or not path_raw:
        raise core.CryptoColdStartFinalGuardBindingError("binding preparation path is missing")
    path = Path(path_raw).expanduser()
    expected_dir = root / core.BINDING_DIR
    if expected_dir.is_symlink() or not expected_dir.is_dir():
        raise core.CryptoColdStartFinalGuardBindingError("binding preparation directory is unsafe")
    if path.is_symlink() or not path.is_file():
        raise core.CryptoColdStartFinalGuardBindingError("binding preparation document is unavailable or unsafe")
    if path.resolve().parent != expected_dir.resolve():
        raise core.CryptoColdStartFinalGuardBindingError(
            "binding preparation document is outside the canonical binding directory"
        )

    material = {key: value for key, value in preparation.items() if key not in _ENVELOPE_KEYS}
    try:
        persisted = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise core.CryptoColdStartFinalGuardBindingError(
            "persisted binding preparation cannot be verified"
        ) from exc
    if not isinstance(persisted, dict) or persisted != material:
        raise core.CryptoColdStartFinalGuardBindingError(
            "binding preparation envelope differs from persisted canonical material"
        )

    supplied_hash = material.get("binding_preparation_hash")
    if not isinstance(supplied_hash, str) or supplied_hash != core._hash_payload(
        material,
        hash_key="binding_preparation_hash",
    ):
        raise core.CryptoColdStartFinalGuardBindingError(
            "binding preparation hash is invalid or tampered"
        )
    expected_name = f"prepared_{supplied_hash[:24]}.json"
    if path.name != expected_name:
        raise core.CryptoColdStartFinalGuardBindingError(
            "binding preparation filename does not match canonical hash"
        )
    return material


def seal_binding(
    *,
    workspace_path: Path,
    preparation: dict[str, object],
    approval_receipt: dict[str, object],
    now,
) -> dict[str, object]:
    material = _canonical_preparation(
        workspace_path=workspace_path,
        preparation=preparation,
    )
    return core.seal_binding(
        workspace_path=workspace_path,
        preparation=material,
        approval_receipt=approval_receipt,
        now=now,
    )
