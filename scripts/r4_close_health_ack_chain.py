from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "knowledge/00_CANON/debt_register.json"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
HUMAN = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
EVIDENCE = "knowledge/60_EVIDENCE/R4_HEALTH_ACK_CHAIN_CERTIFICATION.json"


def main() -> None:
    if not (ROOT / EVIDENCE).exists():
        raise SystemExit(f"missing evidence: {EVIDENCE}")
    data = json.loads(DEBT.read_text())
    target = next((item for item in data["items"] if item["id"] == "TD-R4-014"), None)
    if target is None:
        raise SystemExit("TD-R4-014 missing")
    if target["track"] != "R4" or target["severity"] != "P1":
        raise SystemExit("TD-R4-014 ownership/severity changed unexpectedly")
    target["status"] = "CLOSED"
    target["resolution"] = (
        "Health recovery acknowledgements are now an append-only per-entity hash chain whose head "
        "is included in the hash-protected HealthControlState. Reads, assessments and recoveries "
        "verify the complete chain; deletion, mutation, sequence/reorder, previous-hash and head "
        "mismatches fail closed. HEALTHY no-op acknowledgements still version durable evidence."
    )
    target["evidence"] = [
        "src/autotrade/research/health.py",
        "tests/test_r4_health_ack_chain_integrity.py",
        "tests/test_r4_recovery_ack_idempotency.py",
        EVIDENCE,
    ]
    target["next_action"] = ""
    if "R4" in data["certified_tracks"]:
        raise SystemExit("slice closure must not certify R4")
    DEBT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    matrix = MATRIX.read_text()
    old = "| R4 | Strategy/Portfolio Health & Drift | v0.19 | PARTIAL | health/drift + retry-safe recovery remain certified; tamper-evident ACK-history anchoring is open as `TD-R4-014` |"
    new = "| R4 | Strategy/Portfolio Health & Drift | v0.19 | PASS | baseline/policy-bound durable health + retry-safe recovery + tamper-evident ACK-chain anchored in Health state (`TD-R4-005`,`TD-R4-012`,`TD-R4-014`) |"
    if old in matrix:
        matrix = matrix.replace(old, new, 1)
    elif new not in matrix:
        raise SystemExit("Health matrix row not found")
    MATRIX.write_text(matrix)

    human = HUMAN.read_text()
    anchor = "- `TD-R4-013` — overlay de Health autoritativo en cada lectura Safety/OMS; worsening no sincronizado endurece inmediatamente.\n"
    closed = "- `TD-R4-014` — recovery ACK hash-chain anclado al fingerprint durable de Health state.\n"
    if closed not in human:
        if anchor not in human:
            raise SystemExit("human closed-slice anchor missing")
        human = human.replace(anchor, anchor + closed, 1)
    open_row = "| `TD-R4-014` | P1 | R4 | Health recovery ACK integrity | anchor complete recovery ACK history into hash-protected Health state so deletion/mutation/reordering cannot re-enable a replayed recovery |\n"
    human = human.replace(open_row, "", 1)
    HUMAN.write_text(human)


if __name__ == "__main__":
    main()
