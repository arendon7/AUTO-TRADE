from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "knowledge/00_CANON/debt_register.json"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
HUMAN = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
ACK_EVIDENCE = "knowledge/60_EVIDENCE/R4_RECOVERY_IDEMPOTENCY_CERTIFICATION.json"
OVERLAY_EVIDENCE = "knowledge/60_EVIDENCE/R4_AUTHORITATIVE_HEALTH_OVERLAY_CERTIFICATION.json"


def main() -> None:
    for evidence in (ACK_EVIDENCE, OVERLAY_EVIDENCE):
        if not (ROOT / evidence).exists():
            raise SystemExit(f"missing evidence: {evidence}")

    data = json.loads(DEBT.read_text())
    updates = {
        "TD-R4-012": {
            "resolution": (
                "Health and Defensive Health Bridge recovery acknowledgements now require durable "
                "recovery_id binding. Same-request retries are no-ops, conflicting reuse fails closed, "
                "and a duplicate bridge retry cannot relax twice, bump safety_state twice or append a "
                "second recovery ledger event."
            ),
            "evidence": [
                "src/autotrade/research/health.py",
                "src/autotrade/health_bridge.py",
                "tests/test_r4_recovery_ack_idempotency.py",
                ACK_EVIDENCE,
            ],
        },
        "TD-R4-013": {
            "resolution": (
                "Every effective bridge read now overlays the current authoritative Health state. "
                "Missing/stale/future/backward/conflicting authoritative evidence fails closed, newer "
                "worsening tightens immediately before sync, and newer recovery evidence never relaxes "
                "the durable bridge without explicit recovery/synchronization. Safety and OMS inherit "
                "the overlay on every control read."
            ),
            "evidence": [
                "src/autotrade/health_bridge.py",
                "src/autotrade/safety.py",
                "src/autotrade/oms.py",
                "tests/test_r4_authoritative_health_overlay.py",
                OVERLAY_EVIDENCE,
            ],
        },
    }
    found = set()
    for item in data["items"]:
        if item["id"] in updates:
            found.add(item["id"])
            if item["track"] != "R4" or item["severity"] != "P1":
                raise SystemExit(f"unexpected ownership/severity for {item['id']}")
            item["status"] = "CLOSED"
            item["resolution"] = updates[item["id"]]["resolution"]
            item["evidence"] = updates[item["id"]]["evidence"]
            item["next_action"] = ""
    missing = set(updates) - found
    if missing:
        raise SystemExit(f"missing debt ids: {sorted(missing)}")
    if "R4" in data["certified_tracks"]:
        raise SystemExit("repair closure must not certify R4")
    DEBT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    matrix = MATRIX.read_text()
    replacements = {
        "| R4 | Strategy/Portfolio Health & Drift | v0.19 | PARTIAL | health/drift core remains certified; retry-safe recovery acknowledgement is open as `TD-R4-012` |":
            "| R4 | Strategy/Portfolio Health & Drift | v0.19 | PASS | baseline/policy-bound durable health + monotone worsening + retry-safe explicit fresh-evidence recovery (`TD-R4-005`,`TD-R4-012`) |",
        "| R4 | Defensive Health Bridge | v0.20 | PARTIAL | reduce/block-only core remains certified; recovery idempotency and authoritative unsynced-health overlay are open as `TD-R4-012`,`TD-R4-013` |":
            "| R4 | Defensive Health Bridge | v0.20 | PASS | reduce/block-only bridge + retry-safe recovery + authoritative unsynced-worsening overlay + Safety/OMS rechecks (`TD-R4-006`,`TD-R4-012`,`TD-R4-013`) |",
    }
    for old, new in replacements.items():
        if old in matrix:
            matrix = matrix.replace(old, new, 1)
        elif new not in matrix:
            raise SystemExit(f"matrix row not found: {old}")
    MATRIX.write_text(matrix)

    human = HUMAN.read_text()
    anchor = "- `TD-R4-011` — normalización Decimal exacta de robustez; `sum(weights)==1` preservado para ratios periódicos.\n"
    closed = (
        "- `TD-R4-012` — acknowledgements de recovery retry-safe mediante `recovery_id` durable.\n"
        "- `TD-R4-013` — overlay de Health autoritativo en cada lectura Safety/OMS; worsening no sincronizado endurece inmediatamente.\n"
    )
    if closed not in human:
        if anchor not in human:
            raise SystemExit("human closed-slice anchor missing")
        human = human.replace(anchor, anchor + closed, 1)
    for row in (
        "| `TD-R4-012` | P1 | R4 | Health recovery idempotency | require durable recovery_id binding in Health + Defensive Bridge so a retried acknowledgement cannot relax two levels or bump safety state twice |\n",
        "| `TD-R4-013` | P1 | R4 | Authoritative Health overlay | every effective bridge read must compare current authoritative Health; unsynced worsening tightens immediately, unsynced recovery never relaxes |\n",
    ):
        human = human.replace(row, "", 1)
    HUMAN.write_text(human)


if __name__ == "__main__":
    main()
