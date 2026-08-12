from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PREPARER = ROOT / "src/autotrade/brokers/alpaca_paper_operational_prepare.py"
ISSUER = ROOT / "scripts/r6_issue_operator_decision.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_preparation_core_binding.py"
SELF_TEST = "tests/test_r6_preparation_core_binding.py"


def _check_preparer_source(source: str) -> list[str]:
    errors: list[str] = []
    required = (
        "core_provenance_path: Path",
        "PaperOperationalCoreProvenanceReader(self._workspace).verify(now=now)",
        "provenance_document = _core_provenance_document(",
        "_write_json_idempotent(core_provenance_path, provenance_document)",
        "verify_core_provenance_document(",
        "self._workspace.write_prepared_canary(",
        '"network_write_authorized": False',
        '"external_order_submitted": False',
        '"live_trading": "BLOCKED"',
        '"core_db_sha256"',
        '"risk_decision_fingerprint"',
        '"safety_version"',
        '"portfolio_snapshot_hash"',
        '"strategy_health_fingerprint"',
        '"health_bridge_fingerprint"',
    )
    for anchor in required:
        if anchor not in source:
            errors.append(f"preparation core-binding anchor missing: {anchor}")

    ordered = (
        "result = self._coordinator.prepare(",
        "_write_json_idempotent(\n            package_path,",
        "snapshot_path = write_preparation_snapshot(",
        "snapshot_decision, snapshot_market, snapshot_approval = read_preparation_snapshot(",
        "provenance = PaperOperationalCoreProvenanceReader(self._workspace).verify(now=now)",
        "_write_json_idempotent(core_provenance_path, provenance_document)",
        "verify_core_provenance_document(",
        "package_path, context_path, manifest_path = self._workspace.write_prepared_canary(",
    )
    positions: list[int] = []
    for anchor in ordered:
        position = source.find(anchor)
        if position < 0:
            errors.append(f"preparation ordering anchor missing: {anchor}")
        positions.append(position)
    if all(position >= 0 for position in positions) and positions != sorted(positions):
        errors.append(
            "operator context/manifest must be emitted only after fresh core provenance is persisted and verified"
        )

    if source.count("self._workspace.write_prepared_canary(") != 1:
        errors.append("preparer must emit operator context exactly once")
    if source.count("PaperOperationalCoreProvenanceReader(self._workspace).verify(now=now)") != 1:
        errors.append("preparer must perform exactly one same-workspace core provenance read")
    if source.count("_write_json_idempotent(core_provenance_path, provenance_document)") != 1:
        errors.append("preparer must persist exactly one core provenance document")
    return errors


def _check_issuer_source(source: str) -> list[str]:
    errors: list[str] = []
    for anchor in (
        "PaperOperationalCoreProvenanceReader(workspace).verify(now=now)",
        "verify_core_provenance_document(",
        "first_checked_at = datetime.now(timezone.utc)",
        "second_provenance_hash = _verify_current_core(",
        "SQLiteRuntime(workspace.operator_db_path)",
    ):
        if anchor not in source:
            errors.append(f"operator issuer same-core anchor missing: {anchor}")
    if source.count("_verify_current_core(") != 3:
        errors.append("operator issuer must define one core verifier and invoke it twice")
    return errors


def main() -> int:
    errors: list[str] = []
    if not PREPARER.is_file():
        errors.append("R6 operational preparer is missing")
    else:
        errors.extend(_check_preparer_source(PREPARER.read_text(encoding="utf-8")))
    if not ISSUER.is_file():
        errors.append("R6 human operator issuer is missing")
    else:
        errors.extend(_check_issuer_source(ISSUER.read_text(encoding="utf-8")))

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: preparation core-binding checker is not wired into permanent CI")
    if not R6.is_file() or SELF_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: preparation core-binding adversarial tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 preparation core-binding boundary: PASS "
        "(same core.sqlite3 provenance before operator context; fresh provenance around human approval)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
