from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import mac_crypto_cold_start_final_guard_binding as core
import mac_crypto_cold_start_final_guard_binding_envelope as envelope
import mac_dashboard_cold_start_final_guard_binding_fixed as fixed_dashboard

_HELPER_PATH = Path(__file__).with_name("test_mac_crypto_cold_start_final_guard_binding.py")
_SPEC = importlib.util.spec_from_file_location("binding_core_test_helpers", _HELPER_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load binding core test helpers")
helpers = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(helpers)


def _dashboard_envelope(tmp_path: Path, now: datetime) -> tuple[dict[str, object], dict[str, object]]:
    material = helpers._preparation(now)
    hash_value = str(material["binding_preparation_hash"])
    path = core._persist(
        tmp_path,
        prefix="prepared",
        hash_value=hash_value,
        document=material,
    )
    wrapped: dict[str, object] = {
        "status": "CRYPTO_COLD_START_FINAL_GUARD_BINDING_PREPARED_NO_EXECUTION",
        "mode": "PAPER_READ_LOCAL_BINDING_NO_POST",
        "workspace": str(tmp_path.resolve()),
        "preparation_path": str(path),
        **material,
        "next_action": "TYPE_EXACT_CHALLENGE_TO_SEAL_UAT_BINDING_WITH_CANONICAL_ISSUER",
    }
    return material, wrapped


def test_actual_mac_prepare_envelope_seals_without_false_tamper(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, 3, 20, tzinfo=timezone.utc)
    material, wrapped = _dashboard_envelope(tmp_path, now)
    result = envelope.seal_binding(
        workspace_path=tmp_path,
        preparation=wrapped,
        approval_receipt=helpers._receipt(now, material),
        now=now + timedelta(seconds=1),
    )
    assert result["status"] == "CRYPTO_COLD_START_FINAL_GUARD_BINDING_SEALED_UAT_NO_EXECUTION"
    assert result["operator_decision_status"] == "ISSUED"
    assert result["operator_decision_consumed"] is False
    assert result["normal_final_guard_opened"] is False
    assert result["final_guard_pre_consume_authorized"] is False
    assert result["health_override_authorized"] is False
    assert result["kill_switch_active"] is True
    assert result["external_post_authorized"] is False
    assert result["execution_authority"] == "NONE"
    assert result["live_trading"] == "BLOCKED"


def test_dashboard_envelope_rejects_unknown_or_modified_material(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, 3, 20, tzinfo=timezone.utc)
    material, wrapped = _dashboard_envelope(tmp_path, now)
    wrapped["unexpected_field"] = "must-not-be-ignored"
    with pytest.raises(
        core.CryptoColdStartFinalGuardBindingError,
        match="differs from persisted canonical material",
    ):
        envelope.seal_binding(
            workspace_path=tmp_path,
            preparation=wrapped,
            approval_receipt=helpers._receipt(now, material),
            now=now + timedelta(seconds=1),
        )


def test_dashboard_envelope_requires_exact_persisted_document(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, 3, 20, tzinfo=timezone.utc)
    material, wrapped = _dashboard_envelope(tmp_path, now)
    path = Path(str(wrapped["preparation_path"]))
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(
        core.CryptoColdStartFinalGuardBindingError,
        match="differs from persisted canonical material",
    ):
        envelope.seal_binding(
            workspace_path=tmp_path,
            preparation=wrapped,
            approval_receipt=helpers._receipt(now, material),
            now=now + timedelta(seconds=1),
        )


def test_fixed_dashboard_routes_only_seal_through_canonical_adapter() -> None:
    assert fixed_dashboard.base.binding.seal_binding is envelope.seal_binding
    prepare = fixed_dashboard.base.binding.prepare_binding
    assert prepare.__name__ == "prepare_binding"
    assert Path(prepare.__code__.co_filename).name == "mac_crypto_cold_start_final_guard_binding.py"
