from __future__ import annotations

import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
register_path = ROOT / "knowledge" / "00_CANON" / "debt_register.json"
doc = json.loads(register_path.read_text(encoding="utf-8"))
updates = {
    "TD-R4-008": {
        "status": "CLOSED",
        "resolution": (
            "Existing R0/R2 versioned Portfolio State, reconciliation and reservation infrastructure was audited and hardened for R4: "
            "persistence and Safety share one semantic snapshot validator; in-memory stores detach nested mappings, validate CAS/reconciliation writes, "
            "and publish fill batches atomically; durable state integrity is covered by the companion R4 fill/portfolio commitments."
        ),
        "evidence": [
            "src/autotrade/portfolio_integrity.py",
            "src/autotrade/state.py",
            "src/autotrade/persistence.py",
            "src/autotrade/execution_state.py",
            "tests/test_r4_portfolio_state_audit.py",
            "tests/test_r4_durable_state_integrity.py",
            "knowledge/60_EVIDENCE/R4_PORTFOLIO_STATE_AUDIT.json",
        ],
    },
    "TD-R4-009": {
        "status": "CLOSED",
        "resolution": (
            "Every durable fill read/replay now verifies canonical payload against fill_hash plus independent fill_id/order_id/occurred_at columns; malformed, "
            "rehashed-conflicting or corrupted rows fail closed before OMS or portfolio projection can consume them."
        ),
        "evidence": [
            "src/autotrade/execution_state.py",
            "tests/test_r4_durable_state_integrity.py",
            "knowledge/60_EVIDENCE/R4_DURABLE_STATE_INTEGRITY_CERTIFICATION.json",
            "knowledge/60_EVIDENCE/R4_PORTFOLIO_STATE_AUDIT.json",
        ],
    },
    "TD-R4-010": {
        "status": "CLOSED",
        "resolution": (
            "portfolio_state now carries an independent SHA-256 commitment over exact canonical snapshot bytes; every read/write/CAS/reservation validates hash and "
            "shared semantic integrity, with conservative legacy migration that refuses to bless invalid prior rows."
        ),
        "evidence": [
            "src/autotrade/portfolio_integrity.py",
            "src/autotrade/persistence.py",
            "src/autotrade/execution_state.py",
            "tests/test_r4_durable_state_integrity.py",
            "tests/test_r4_portfolio_state_audit.py",
            "knowledge/60_EVIDENCE/R4_DURABLE_STATE_INTEGRITY_CERTIFICATION.json",
            "knowledge/60_EVIDENCE/R4_PORTFOLIO_STATE_AUDIT.json",
        ],
    },
}
seen = set()
for item in doc["items"]:
    debt_id = item.get("id")
    if debt_id in updates:
        item.update(updates[debt_id])
        item["next_action"] = ""
        seen.add(debt_id)
missing = set(updates) - seen
if missing:
    raise SystemExit(f"missing debt ids: {sorted(missing)}")
register_path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")

matrix_path = ROOT / "knowledge" / "00_CANON" / "RECONSTRUCTION_V028R_MATRIX.md"
matrix = matrix_path.read_text(encoding="utf-8")
replacements = {
    "| R4 | authoritative instrument master | reconstructed safety prerequisite | TODO |":
        "| R4 | authoritative instrument master | reconstructed safety prerequisite | PASS |",
    "| R4 | versioned Portfolio State / reconciliation infrastructure | v0.18 lineage | PARTIAL |":
        "| R4 | versioned Portfolio State / reconciliation infrastructure | v0.18 lineage | PASS |",
}
for old, new in replacements.items():
    if old not in matrix:
        raise SystemExit(f"matrix marker missing: {old}")
    matrix = matrix.replace(old, new, 1)
matrix_path.write_text(matrix, encoding="utf-8")

shutil.rmtree(ROOT / ".r4closure", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r4-closure-one-shot.yml"
if workflow.exists():
    workflow.unlink()
print("R4 certified debt and matrix closures applied")
