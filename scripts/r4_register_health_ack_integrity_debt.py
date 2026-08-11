from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "knowledge/00_CANON/debt_register.json"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
HUMAN = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"


def main() -> None:
    data = json.loads(DEBT.read_text())
    if not any(item["id"] == "TD-R4-014" for item in data["items"]):
        data["items"].append(
            {
                "area": "Health recovery acknowledgement tamper-evidence",
                "evidence": [
                    "src/autotrade/research/health.py",
                    "tests/test_r4_recovery_ack_idempotency.py"
                ],
                "id": "TD-R4-014",
                "next_action": "Anchor the complete Health recovery-acknowledgement history into the hash-protected HealthControlState using a deterministic append-only acknowledgement chain/digest. Deletion, mutation or reordering of ACK rows must fail closed before recovery, and the same recovery_id must remain replay-safe after durable tamper attempts.",
                "resolution": "",
                "severity": "P1",
                "status": "OPEN",
                "track": "R4"
            }
        )
    DEBT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    matrix = MATRIX.read_text()
    old = "| R4 | Strategy/Portfolio Health & Drift | v0.19 | PASS | baseline/policy-bound durable health + monotone worsening + retry-safe explicit fresh-evidence recovery (`TD-R4-005`,`TD-R4-012`) |"
    new = "| R4 | Strategy/Portfolio Health & Drift | v0.19 | PARTIAL | health/drift + retry-safe recovery remain certified; tamper-evident ACK-history anchoring is open as `TD-R4-014` |"
    if old in matrix:
        matrix = matrix.replace(old, new, 1)
    elif new not in matrix:
        raise SystemExit("Health matrix PASS row not found")
    MATRIX.write_text(matrix)

    human = HUMAN.read_text()
    row = "| `TD-R4-014` | P1 | R4 | Health recovery ACK integrity | anchor complete recovery ACK history into hash-protected Health state so deletion/mutation/reordering cannot re-enable a replayed recovery |\n"
    anchor = "| `TD-OPS-001` | P3 | OPS | Graphify | generar graph semántico/deep real en runtime soportado y vincularlo a `SOURCE_SHA`; nunca fabricar artefactos |\n"
    if row not in human:
        if anchor not in human:
            raise SystemExit("human debt anchor missing")
        human = human.replace(anchor, row + anchor, 1)
    HUMAN.write_text(human)


if __name__ == "__main__":
    main()
