from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "knowledge/00_CANON/debt_register.json"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
HUMAN = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
SIZING_EVIDENCE = "knowledge/60_EVIDENCE/R4_PORTFOLIO_MANAGER_CERTIFICATION.json"
NORMALIZATION_EVIDENCE = "knowledge/60_EVIDENCE/R4_EXACT_NORMALIZATION_CERTIFICATION.json"


def main() -> None:
    for evidence in (SIZING_EVIDENCE, NORMALIZATION_EVIDENCE):
        if not (ROOT / evidence).exists():
            raise SystemExit(f"missing evidence: {evidence}")

    data = json.loads(DEBT.read_text())
    updates = {
        "TD-R4-007": {
            "resolution": (
                "Deterministic Portfolio Manager emits advisory bounded capacity only. It recomputes "
                "base/post-Health/post-venue diversification and robustness, binds strategy identity "
                "to Health identity, requires fresh authoritative instrument rules and quotes, rounds "
                "quantity down, never upsizes to venue minima, and is protected by a permanent CI "
                "authority boundary forbidding OMS/broker/execution creep."
            ),
            "evidence": [
                "src/autotrade/portfolio_manager.py",
                "scripts/check_research_authority.py",
                "tests/test_r4_portfolio_manager.py",
                "tests/test_r4_portfolio_manager_binding.py",
                "tests/test_r4_portfolio_manager_authority_gate.py",
                SIZING_EVIDENCE,
            ],
        },
        "TD-R4-011": {
            "resolution": (
                "Allocation robustness now preserves exact Decimal sum(weights)==1 for repeating "
                "ratios using deterministic canonical remainder normalization across baseline, "
                "leave-one-out and perturbation scenarios; the invariant was preserved rather than "
                "replaced with a tolerance."
            ),
            "evidence": [
                "src/autotrade/research/allocation_robustness.py",
                "tests/test_r4_allocation_exact_normalization.py",
                "tests/test_r4_allocation_robustness.py",
                NORMALIZATION_EVIDENCE,
            ],
        },
    }
    found = set()
    for item in data["items"]:
        debt_id = item["id"]
        if debt_id in updates:
            found.add(debt_id)
            if item["track"] != "R4" or item["severity"] != "P1":
                raise SystemExit(f"unexpected ownership/severity for {debt_id}")
            item["status"] = "CLOSED"
            item["resolution"] = updates[debt_id]["resolution"]
            item["evidence"] = updates[debt_id]["evidence"]
            item["next_action"] = ""
    missing = set(updates) - found
    if missing:
        raise SystemExit(f"missing debt ids: {sorted(missing)}")
    if "R4" in data["certified_tracks"]:
        raise SystemExit("slice closure must not certify R4")
    DEBT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    matrix = MATRIX.read_text()
    replacements = {
        "| R4 | allocation perturbation + leave-one-out | v0.18 | PARTIAL | core evidence remains valid, but repeating-decimal exact normalization regression is open as `TD-R4-011` |":
            "| R4 | allocation perturbation + leave-one-out | v0.18 | PASS | deterministic exact-sum Decimal normalization + complete recomputable perturbation/leave-one-out gate (`TD-R4-003`,`TD-R4-011`) |",
        "| R4 | deterministic Portfolio Manager / sizing + cross-strategy budgets | reconstructed strengthening | TODO | bounded advisory/control-plane output; no OMS bypass or capital authority |":
            "| R4 | deterministic Portfolio Manager / sizing + cross-strategy budgets | reconstructed strengthening | PASS | advisory-only sizing + base/Health/venue budget+robustness recomputation + authoritative metadata + CI authority gate (`TD-R4-007`) |",
    }
    for old, new in replacements.items():
        if old in matrix:
            matrix = matrix.replace(old, new, 1)
        elif new not in matrix:
            raise SystemExit(f"matrix row not found: {old}")
    MATRIX.write_text(matrix)

    human = HUMAN.read_text()
    closed_anchor = "- `TD-R4-006` — Defensive Health Bridge durable, reduce/block-only, integrado con Safety/OMS.\n"
    closed_lines = (
        "- `TD-R4-007` — Portfolio Manager determinista, acotado y advisory-only; sin autoridad OMS/broker.\n"
        "- `TD-R4-011` — normalización Decimal exacta de robustez; `sum(weights)==1` preservado para ratios periódicos.\n"
    )
    if closed_lines not in human:
        if closed_anchor not in human:
            raise SystemExit("human closed-slice anchor missing")
        human = human.replace(closed_anchor, closed_anchor + closed_lines, 1)
    for row in (
        "| `TD-R4-007` | P1 | R4 | Portfolio Manager / sizing | sizing determinista y acotado bajo budgets de estrategia/cluster/portfolio; output sin autoridad de broker/OMS |\n",
        "| `TD-R4-011` | P1 | R4 | Allocation robustness Decimal normalization | normalized weights must sum exactly to 1 even for repeating Decimal ratios; preserve exact-sum invariant rather than weakening it |\n",
    ):
        human = human.replace(row, "", 1)
    human = human.replace(
        "1. `TD-R4-007` deterministic bounded sizing.\n2. auditoría adversarial R4 completa.\n3. cerrar cualquier deuda nueva encontrada.\n4. sincronizar canon y certificar PR #11.\n5. merge solo con CI verde y recertificar el SHA exacto de `main` antes de R5.\n",
        "1. auditoría adversarial R4 completa.\n2. cerrar cualquier deuda nueva encontrada.\n3. sincronizar canon y certificar PR #11.\n4. merge solo con CI verde y recertificar el SHA exacto de `main` antes de R5.\n",
        1,
    )
    HUMAN.write_text(human)


if __name__ == "__main__":
    main()
