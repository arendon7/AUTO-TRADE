from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "knowledge/00_CANON/debt_register.json"
HUMAN = ROOT / "knowledge/00_CANON/DEBT_REGISTER.md"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
EVIDENCE = ROOT / "knowledge/60_EVIDENCE/R4_HEALTH_ACK_CHAIN_CERTIFICATION.json"

SOURCE_COMMIT = "fb6c5f252819b0aaff66588f4008c6509791afff"
TARGETED_RUN_ID = 31460443912
TARGETED_JOB_ID = 93682726848


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    if old not in text:
        raise SystemExit(f"{label} not found")
    path.write_text(text.replace(old, new, 1))


def close_machine_debt() -> None:
    data = json.loads(DEBT.read_text())
    items = [item for item in data["items"] if item["id"] == "TD-R4-014"]
    if len(items) != 1:
        raise SystemExit("TD-R4-014 must exist exactly once")
    item = items[0]
    if item["status"] not in {"OPEN", "CLOSED"}:
        raise SystemExit(f"unexpected TD-R4-014 status: {item['status']}")
    item["evidence"] = [
        "src/autotrade/research/health.py",
        "tests/test_r4_recovery_ack_idempotency.py",
        "tests/test_r4_health_ack_chain_integrity.py",
        "knowledge/60_EVIDENCE/R4_HEALTH_ACK_CHAIN_CERTIFICATION.json",
    ]
    item["next_action"] = ""
    item["resolution"] = (
        "Health recovery acknowledgements are anchored into the hash-protected "
        "HealthControlState by a deterministic append-only SHA-256 chain with strict "
        "sequence and previous-hash linkage. Durable reads and Health mutations verify "
        "the complete chain; deletion, payload mutation, reordering, sequence gaps or "
        "state-head mismatch fail closed. recovery_id idempotency remains durable, and "
        "non-empty pre-chain state/ack history requires explicit migration/rebaseline."
    )
    item["status"] = "CLOSED"
    DEBT.write_text(json.dumps(data, indent=2) + "\n")


def write_evidence() -> None:
    evidence = {
        "track": "R4",
        "control": "Health recovery acknowledgement tamper-evidence",
        "debt_id": "TD-R4-014",
        "status": "PASS",
        "source_commit": SOURCE_COMMIT,
        "targeted_certification": {
            "workflow_run_id": TARGETED_RUN_ID,
            "job_id": TARGETED_JOB_ID,
            "conclusion": "success",
            "tests_passed": 63,
            "suite": [
                "tests/test_r4_health_ack_chain_integrity.py",
                "tests/test_r4_recovery_ack_idempotency.py",
                "tests/test_r4_health_drift.py",
                "tests/test_r4_health_binding_integrity.py",
                "tests/test_r4_health_bridge.py",
                "tests/test_r4_health_bridge_integration.py",
                "tests/test_r4_authoritative_health_overlay.py",
            ],
        },
        "certified_invariants": [
            "HealthControlState commits recovery_ack_head inside its SHA-256 state fingerprint.",
            "Recovery acknowledgements use an append-only chain with strict ack_seq and previous_ack_hash linkage.",
            "Every durable Health read verifies the complete acknowledgement chain before returning authority-bearing state.",
            "Health assessment and recovery mutations verify the existing chain before advancing state.",
            "ACK deletion, payload mutation, reordering, sequence gaps and state-head mismatch fail closed.",
            "The same recovery_id remains request-bound and replay-safe after durable persistence.",
            "A HEALTHY acknowledgement is severity-neutral but versions durable evidence so its recovery_id is chain-anchored.",
            "Non-empty pre-chain Health state or acknowledgement history is never silently blessed; explicit migration/rebaseline is required.",
        ],
        "authority_boundary": (
            "This control changes Health evidence integrity only and introduces no OMS, broker, "
            "external PAPER or LIVE execution authority."
        ),
        "live_trading": "BLOCKED",
        "note": (
            "The 63-test targeted workflow is permanent certification evidence for TD-R4-014. "
            "Repository-wide Core Safety and Knowledge Contract are recertified separately on "
            "the subsequent human-triggered PR head."
        ),
    }
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n")


def update_human_views() -> None:
    replace_once(
        HUMAN,
        "- `TD-R4-013` — overlay de Health autoritativo en cada lectura Safety/OMS; worsening no sincronizado endurece inmediatamente.\n",
        "- `TD-R4-013` — overlay de Health autoritativo en cada lectura Safety/OMS; worsening no sincronizado endurece inmediatamente.\n"
        "- `TD-R4-014` — historial de recovery ACK anclado en cadena SHA-256 dentro del Health state; borrado, mutación, reordenamiento o gaps fallan cerrado.\n",
        "human closed-slice insertion",
    )
    replace_once(
        HUMAN,
        "| `TD-R4-014` | P1 | R4 | Health recovery ACK integrity | anchor complete recovery ACK history into hash-protected Health state so deletion/mutation/reordering cannot re-enable a replayed recovery |\n",
        "",
        "human open-debt removal",
    )
    replace_once(
        MATRIX,
        "| R4 | Strategy/Portfolio Health & Drift | v0.19 | PARTIAL | health/drift + retry-safe recovery remain certified; tamper-evident ACK-history anchoring is open as `TD-R4-014` |",
        "| R4 | Strategy/Portfolio Health & Drift | v0.19 | PASS | health/drift + retry-safe recovery + hash-protected append-only ACK-chain anchoring; deletion/mutation/reordering, sequence gaps and unsafe pre-chain migration fail closed (`TD-R4-005`,`TD-R4-012`,`TD-R4-014`) |",
        "R4 Health matrix closure",
    )


def main() -> None:
    close_machine_debt()
    write_evidence()
    update_human_views()


if __name__ == "__main__":
    main()
