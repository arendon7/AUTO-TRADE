from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "knowledge/00_CANON/debt_register.json"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
HUMAN = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"


def main() -> None:
    data = json.loads(DEBT.read_text())
    if not any(item["id"] == "TD-R4-013" for item in data["items"]):
        data["items"].append(
            {
                "area": "Defensive Health Bridge authoritative-state synchronization gap",
                "evidence": [
                    "src/autotrade/research/health.py",
                    "src/autotrade/health_bridge.py",
                    "src/autotrade/safety.py",
                    "src/autotrade/oms.py"
                ],
                "id": "TD-R4-013",
                "next_action": "Make effective_control compare the durable bridge projection with the current authoritative HealthControlState on every Safety/OMS read. Missing/stale/future/conflicting/backward authoritative state must fail closed; a newer stricter Health state must tighten immediately even before sync; a newer less-restrictive state must not relax until explicit bridge recovery/sync policy permits it.",
                "resolution": "",
                "severity": "P1",
                "status": "OPEN",
                "track": "R4"
            }
        )
    DEBT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    matrix = MATRIX.read_text()
    old = "| R4 | Defensive Health Bridge | v0.20 | PARTIAL | reduce/block-only bridge remains certified; recovery request idempotency is open as `TD-R4-012` |"
    new = "| R4 | Defensive Health Bridge | v0.20 | PARTIAL | reduce/block-only core remains certified; recovery idempotency and authoritative unsynced-health overlay are open as `TD-R4-012`,`TD-R4-013` |"
    if old in matrix:
        matrix = matrix.replace(old, new, 1)
    elif new not in matrix:
        raise SystemExit("Defensive Health Bridge PARTIAL row not found")
    MATRIX.write_text(matrix)

    human = HUMAN.read_text()
    row = "| `TD-R4-013` | P1 | R4 | Authoritative Health overlay | every effective bridge read must compare current authoritative Health; unsynced worsening tightens immediately, unsynced recovery never relaxes |\n"
    anchor = "| `TD-OPS-001` | P3 | OPS | Graphify | generar graph semántico/deep real en runtime soportado y vincularlo a `SOURCE_SHA`; nunca fabricar artefactos |\n"
    if row not in human:
        if anchor not in human:
            raise SystemExit("human debt anchor missing")
        human = human.replace(anchor, row + anchor, 1)
    HUMAN.write_text(human)


if __name__ == "__main__":
    main()
