from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/mac_doctor.py"
SPEC = importlib.util.spec_from_file_location("mac_doctor_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
doctor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(doctor)


def test_build_source_head_accepts_certified_sha(tmp_path) -> None:
    sha = "a" * 40
    (tmp_path / "MAC_BUILD_INFO.txt").write_text(f"source_head={sha}\n", encoding="utf-8")
    assert doctor._build_source_head(tmp_path) == sha


def test_packaged_mode_counts_as_r6_provenance(monkeypatch, capsys) -> None:
    sha = "b" * 40
    monkeypatch.setattr(doctor, "_git_branch", lambda root: None)
    monkeypatch.setattr(doctor, "_build_source_head", lambda root: sha)
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(doctor, "_env_file_mode", lambda root: "ABSENT")
    monkeypatch.delenv(doctor.WRITE_ENV, raising=False)
    monkeypatch.delenv(doctor.KEY_ENV, raising=False)
    monkeypatch.delenv(doctor.SECRET_ENV, raising=False)
    assert doctor.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["branch_matches_r6"] is True
    assert report["provenance_mode"] == "CERTIFIED_PACKAGE"
    assert report["package_source_head"] == sha
    assert report["package_source_verified"] is True
    assert report["doctor_status"] == "PASS"
