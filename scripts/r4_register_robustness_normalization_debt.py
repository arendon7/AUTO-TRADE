from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "knowledge/00_CANON/debt_register.json"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
HUMAN = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"


def main() -> None:
    data = json.loads(DEBT.read_text())
    if any(item["id"] == "TD-R4-011" for item in data["items"]):
        return
    data["items"].append(
        {
            "area": "Allocation robustness exact Decimal normalization",
            "evidence": [
                "src/autotrade/research/allocation_robustness.py",
                "tests/test_r4_portfolio_manager.py"
            ],
            "id": "TD-R4-011",
            "next_action": "Replace independently rounded normalized weights with deterministic exact-sum normalization (last canonical component is the remainder to 1), apply it to baseline and leave-one-out scenarios, and add repeating-decimal regression tests before re-closing robustness.",
            "resolution": "",
            "severity": "P1",
            "status": "OPEN",
            "track": "R4"
        }
    )
    DEBT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    matrix = MATRIX.read_text()
    passed = "| R4 | allocation perturbation + leave-one-out | v0.18 | PASS | complete deterministic scenario set + recomputable robustness gate (`TD-R4-003`) |"
    partial = "| R4 | allocation perturbation + leave-one-out | v0.18 | PARTIAL | core evidence remains valid, but repeating-decimal exact normalization regression is open as `TD-R4-011` |"
    if passed in matrix:
        matrix = matrix.replace(passed, partial, 1)
    elif partial not in matrix:
        raise SystemExit("allocation robustness matrix row not found")
    MATRIX.write_text(matrix)

    human = HUMAN.read_text()
    row = "| `TD-R4-011` | P1 | R4 | Allocation robustness Decimal normalization | normalized weights must sum exactly to 1 even for repeating Decimal ratios; preserve exact-sum invariant rather than weakening it |\n"
    anchor = "| `TD-R4-007` | P1 | R4 | Portfolio Manager / sizing | sizing determinista y acotado bajo budgets de estrategia/cluster/portfolio; output sin autoridad de broker/OMS |\n"
    if row not in human:
        if anchor not in human:
            raise SystemExit("human debt anchor not found")
        human = human.replace(anchor, anchor + row, 1)
    HUMAN.write_text(human)


if __name__ == "__main__":
    main()
