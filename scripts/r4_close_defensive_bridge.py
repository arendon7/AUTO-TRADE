from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "knowledge/00_CANON/debt_register.json"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
HUMAN = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
EVIDENCE = "knowledge/60_EVIDENCE/R4_DEFENSIVE_HEALTH_BRIDGE_CERTIFICATION.json"


def main() -> None:
    if not (ROOT / EVIDENCE).exists():
        raise SystemExit(f"missing evidence: {EVIDENCE}")

    data = json.loads(DEBT.read_text())
    target = None
    for item in data["items"]:
        if item["id"] == "TD-R4-006":
            target = item
            break
    if target is None:
        raise SystemExit("TD-R4-006 missing from debt register")
    if target["track"] != "R4" or target["severity"] != "P1":
        raise SystemExit("TD-R4-006 ownership/severity changed unexpectedly")
    target["status"] = "CLOSED"
    target["resolution"] = (
        "Durable Defensive Health Bridge reads authoritative health state and automatically "
        "maintains/tightens risk only. Strategy/portfolio stricter-state-wins, missing/stale "
        "fail-closed semantics, raw-row integrity, replay idempotency, safety-state version "
        "invalidation, submit-time OMS recheck and explicit fresh-evidence one-level recovery "
        "are certified. The bridge never increases exposure or bypasses Safety/OMS."
    )
    target["evidence"] = [
        "src/autotrade/health_bridge.py",
        "src/autotrade/safety.py",
        "src/autotrade/oms.py",
        "src/autotrade/bootstrap.py",
        "tests/test_r4_health_bridge.py",
        "tests/test_r4_health_bridge_integration.py",
        EVIDENCE,
    ]
    target["next_action"] = ""
    if "R4" in data["certified_tracks"]:
        raise SystemExit("slice closure must not certify R4")
    DEBT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    matrix = MATRIX.read_text()
    old = "| R4 | Defensive Health Bridge | v0.20 | TODO | automatic actions reduce/block only + explicit human/policy recovery |"
    new = "| R4 | Defensive Health Bridge | v0.20 | PASS | durable reduce/block-only bridge + safety-version invalidation + submit-time OMS recheck + explicit fresh-evidence recovery (`TD-R4-006`) |"
    if old in matrix:
        matrix = matrix.replace(old, new, 1)
    elif new not in matrix:
        raise SystemExit("Defensive Health Bridge matrix row not found")
    MATRIX.write_text(matrix)

    human = HUMAN.read_text()
    closed_marker = "- `TD-R4-006` — Defensive Health Bridge durable, reduce/block-only, integrado con Safety/OMS.\n"
    anchor = "- `TD-R4-005` — Strategy/Portfolio Health & Drift durable.\n"
    if closed_marker not in human:
        if anchor not in human:
            raise SystemExit("human debt closed-slice anchor not found")
        human = human.replace(anchor, anchor + closed_marker, 1)
    open_row = "| `TD-R4-006` | P1 | R4 | Defensive Health Bridge | automatización solo puede reducir/bloquear/quarantinar riesgo; stricter-state-wins; recuperación explícita con evidencia fresca; jamás aumenta exposición ni omite Safety/OMS |\n"
    human = human.replace(open_row, "", 1)
    human = human.replace(
        "1. `TD-R4-006` Defensive Health Bridge.\n2. `TD-R4-007` deterministic bounded sizing.\n3. auditoría adversarial R4 completa.\n4. cerrar cualquier deuda nueva encontrada.\n5. sincronizar canon y certificar PR #11.\n6. merge solo con CI verde y recertificar el SHA exacto de `main` antes de R5.\n",
        "1. `TD-R4-007` deterministic bounded sizing.\n2. auditoría adversarial R4 completa.\n3. cerrar cualquier deuda nueva encontrada.\n4. sincronizar canon y certificar PR #11.\n5. merge solo con CI verde y recertificar el SHA exacto de `main` antes de R5.\n",
        1,
    )
    HUMAN.write_text(human)


if __name__ == "__main__":
    main()
