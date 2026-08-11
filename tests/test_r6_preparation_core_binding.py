from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_preparation_core_binding.py"
PREPARER = ROOT / "src/autotrade/brokers/alpaca_paper_operational_prepare.py"
ISSUER = ROOT / "scripts/r6_issue_operator_decision.py"


def namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def test_current_preparation_core_binding_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "preparation core-binding boundary: PASS" in result.stdout


def test_checker_rejects_operator_context_before_core_provenance() -> None:
    ns = namespace()
    source = PREPARER.read_text(encoding="utf-8")
    early = "package_path, context_path, manifest_path = self._workspace.write_prepared_canary("
    provenance = "provenance = PaperOperationalCoreProvenanceReader(self._workspace).verify(now=now)"
    source = source.replace(early, "# moved-context-placeholder", 1)
    source = source.replace(
        provenance,
        early + "\n            result.package, result.bracket\n        )\n        " + provenance,
        1,
    )
    errors = ns["_check_preparer_source"](source)
    assert any("only after fresh core provenance" in error for error in errors)


def test_checker_rejects_missing_core_provenance_persistence() -> None:
    ns = namespace()
    source = PREPARER.read_text(encoding="utf-8").replace(
        "_write_json_idempotent(core_provenance_path, provenance_document)",
        "pass  # provenance persistence removed",
        1,
    )
    errors = ns["_check_preparer_source"](source)
    assert any("persist" in error for error in errors)


def test_checker_rejects_single_operator_core_check() -> None:
    ns = namespace()
    source = ISSUER.read_text(encoding="utf-8").replace(
        "second_provenance_hash = _verify_current_core(",
        "second_provenance_hash = provenance_hash  # removed second read\n    # _verify_current_core_removed(",
        1,
    )
    errors = ns["_check_issuer_source"](source)
    assert any("invoke it twice" in error or "same-core anchor missing" in error for error in errors)
