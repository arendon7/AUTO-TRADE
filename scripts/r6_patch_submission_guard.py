from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/brokers/alpaca_paper_submission.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {text.count(old)}")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '''            if by_order is not None or by_client is not None:\n                if by_order is None or by_client is None:\n                    raise PaperSubmissionIntegrityError("submission binding indexes disagree")\n                existing = _binding_from_row(by_order)\n                if str(by_client["order_id"]) != existing.order_id:\n                    raise PaperSubmissionConflict(\n                        "client_order_id is already bound to another local order"\n                    )\n                if existing.fingerprint != binding.fingerprint:\n                    raise PaperSubmissionConflict(\n                        "order/client_order_id is already bound to different immutable data"\n                    )\n                _, state, _ = self._verify_locked(conn, binding.order_id)\n                conn.execute("COMMIT")\n                return state\n''',
        '''            if by_order is not None:\n                existing = _binding_from_row(by_order)\n                if (\n                    existing.client_order_id != binding.client_order_id\n                    or existing.fingerprint != binding.fingerprint\n                ):\n                    raise PaperSubmissionConflict(\n                        "local order is already bound to different immutable submission data"\n                    )\n                if by_client is None or str(by_client["order_id"]) != existing.order_id:\n                    raise PaperSubmissionIntegrityError("submission binding indexes disagree")\n                _, state, _ = self._verify_locked(conn, binding.order_id)\n                conn.execute("COMMIT")\n                return state\n\n            if by_client is not None:\n                raise PaperSubmissionConflict(\n                    "client_order_id is already bound to another local order"\n                )\n''',
        "binding collision classification",
    )

    text = replace_once(
        text,
        '''        if event.event_type is PaperSubmissionEventType.PREPARED:\n            if event.sequence != 1 or status is not None:\n                raise PaperSubmissionIntegrityError("PREPARED must be first event")\n            if set(event.payload) != {"binding_hash"}:\n''',
        '''        if event.event_type is PaperSubmissionEventType.PREPARED:\n            if event.sequence != 1 or status is not None:\n                raise PaperSubmissionIntegrityError("PREPARED must be first event")\n            if event.occurred_at.astimezone(timezone.utc) != binding.created_at.astimezone(timezone.utc):\n                raise PaperSubmissionIntegrityError(\n                    "PREPARED timestamp must equal immutable binding creation time"\n                )\n            if set(event.payload) != {"binding_hash"}:\n''',
        "prepared timestamp binding",
    )

    text = replace_once(
        text,
        '''def _update_control(conn: sqlite3.Connection, state: PaperSubmissionState) -> None:\n    cursor = conn.execute(\n''',
        '''def _update_control(conn: sqlite3.Connection, state: PaperSubmissionState) -> None:\n    current = conn.execute(\n        "SELECT updated_at FROM alpaca_paper_submission_control WHERE order_id = ?",\n        (state.order_id,),\n    ).fetchone()\n    if current is None:\n        raise PaperSubmissionIntegrityError("submission control anchor is missing")\n    current_updated_at = _parse_datetime(current["updated_at"], "updated_at")\n    if state.updated_at.astimezone(timezone.utc) < current_updated_at.astimezone(timezone.utc):\n        raise ValueError("submission control time cannot move backwards")\n\n    cursor = conn.execute(\n''',
        "monotonic control update",
    )

    TARGET.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
