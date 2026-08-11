from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]

oms_path = ROOT / "src" / "autotrade" / "oms.py"
oms = oms_path.read_text(encoding="utf-8")
old = '''            if (
                existing.event_type != event.event_type
                or existing.occurred_at != event.occurred_at
                or dict(existing.payload) != dict(event.payload)
            ):
                raise BrokerStateConflict(f"ledger event identity conflict: {event.event_id}")
            return
'''
new = '''            if (
                existing.event_type != event.event_type
                or dict(existing.payload) != dict(event.payload)
            ):
                raise BrokerStateConflict(f"ledger event identity conflict: {event.event_id}")
            # Same semantic event may be retried after restart at a later wall
            # clock time. The timestamp of the first durable occurrence wins.
            return
'''
if old in oms:
    oms = oms.replace(old, new, 1)
elif "existing.occurred_at != event.occurred_at" in oms:
    raise SystemExit("unexpected OMS idempotent-event shape")
oms_path.write_text(oms, encoding="utf-8")

shutil.copy2(
    ROOT / ".r2final2" / "test_r2_idempotent_events.py",
    ROOT / "tests" / "test_r2_idempotent_events.py",
)
shutil.copy2(
    ROOT / ".r2final2" / "test_r2_reservation_risk_matrix.py",
    ROOT / "tests" / "test_r2_reservation_risk_matrix.py",
)

shutil.rmtree(ROOT / ".r2final2", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r2-finalize2-patch.yml"
if workflow.exists():
    workflow.unlink()

print("R2 semantic-idempotency patch applied")
