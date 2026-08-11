from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "knowledge/00_CANON/debt_register.json"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
HUMAN = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"


def main() -> None:
    data = json.loads(DEBT.read_text())
    if not any(item["id"] == "TD-R4-012" for item in data["items"]):
        data["items"].append(
            {
                "area": "Health recovery acknowledgement idempotency",
                "evidence": [
                    "src/autotrade/research/health.py",
                    "src/autotrade/health_bridge.py"
                ],
                "id": "TD-R4-012",
                "next_action": "Require an explicit recovery_id/idempotency key for Health and Defensive Health Bridge acknowledgements; persist request identity and binding, make same-request replay a no-op, reject conflicting reuse, and prove duplicate retries cannot relax more than one level or bump safety_state twice.",
                "resolution": "",
                "severity": "P1",
                "status": "OPEN",
                "track": "R4"
            }
        )
    DEBT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    matrix = MATRIX.read_text()
    replacements = {
        "| R4 | Strategy/Portfolio Health & Drift | v0.19 | PASS | immutable baseline/policy-bound durable state + monotone automatic worsening + acknowledged fresh-evidence recovery (`TD-R4-005`) |":
            "| R4 | Strategy/Portfolio Health & Drift | v0.19 | PARTIAL | health/drift core remains certified; retry-safe recovery acknowledgement is open as `TD-R4-012` |",
        "| R4 | Defensive Health Bridge | v0.20 | PASS | durable reduce/block-only bridge + safety-version invalidation + submit-time OMS recheck + explicit fresh-evidence recovery (`TD-R4-006`) |":
            "| R4 | Defensive Health Bridge | v0.20 | PARTIAL | reduce/block-only bridge remains certified; recovery request idempotency is open as `TD-R4-012` |",
    }
    for old, new in replacements.items():
        if old in matrix:
            matrix = matrix.replace(old, new, 1)
        elif new not in matrix:
            raise SystemExit(f"matrix row not found: {old}")
    MATRIX.write_text(matrix)

    human = HUMAN.read_text()
    row = "| `TD-R4-012` | P1 | R4 | Health recovery idempotency | require durable recovery_id binding in Health + Defensive Bridge so a retried acknowledgement cannot relax two levels or bump safety state twice |\n"
    anchor = "| `TD-OPS-001` | P3 | OPS | Graphify | generar graph semántico/deep real en runtime soportado y vincularlo a `SOURCE_SHA`; nunca fabricar artefactos |\n"
    if row not in human:
        if anchor not in human:
            raise SystemExit("human debt anchor missing")
        human = human.replace(anchor, row + anchor, 1)
    HUMAN.write_text(human)


if __name__ == "__main__":
    main()
