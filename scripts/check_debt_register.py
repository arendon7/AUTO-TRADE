from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "knowledge" / "00_CANON" / "debt_register.json"
ID_RE = re.compile(r"^TD-[A-Z0-9]+-[0-9]{3}$")
VALID_SEVERITIES = {"P0", "P1", "P2", "P3"}
VALID_STATUSES = {"OPEN", "CLOSED"}
BLOCKING_SEVERITIES = {"P0", "P1", "P2"}


def _error(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> int:
    errors: list[str] = []
    try:
        document = json.loads(REGISTER.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - checker must fail closed
        print(f"ERROR: cannot read debt register: {exc}", file=sys.stderr)
        return 1

    if not isinstance(document, dict):
        _error(errors, "register root must be an object")
        document = {}

    version = document.get("registry_version")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        _error(errors, "registry_version must be integer > 0")

    certified_raw = document.get("certified_tracks")
    if not isinstance(certified_raw, list) or any(
        not isinstance(track, str) or not track.strip() for track in certified_raw or []
    ):
        _error(errors, "certified_tracks must be a list of non-empty strings")
        certified_tracks: set[str] = set()
    else:
        certified_tracks = set(certified_raw)
        if len(certified_tracks) != len(certified_raw):
            _error(errors, "certified_tracks contains duplicates")

    items = document.get("items")
    if not isinstance(items, list):
        _error(errors, "items must be an array")
        items = []

    seen_ids: set[str] = set()
    for index, raw in enumerate(items):
        prefix = f"items[{index}]"
        if not isinstance(raw, dict):
            _error(errors, f"{prefix} must be an object")
            continue

        debt_id = raw.get("id")
        if not isinstance(debt_id, str) or not ID_RE.fullmatch(debt_id):
            _error(errors, f"{prefix}.id is invalid")
            debt_id = f"<invalid-{index}>"
        elif debt_id in seen_ids:
            _error(errors, f"duplicate debt id: {debt_id}")
        seen_ids.add(debt_id)

        track = raw.get("track")
        area = raw.get("area")
        severity = raw.get("severity")
        status = raw.get("status")
        if not isinstance(track, str) or not track.strip():
            _error(errors, f"{debt_id}: track is required")
            track = ""
        if not isinstance(area, str) or not area.strip():
            _error(errors, f"{debt_id}: area is required")
        if severity not in VALID_SEVERITIES:
            _error(errors, f"{debt_id}: invalid severity {severity!r}")
        if status not in VALID_STATUSES:
            _error(errors, f"{debt_id}: invalid status {status!r}")

        evidence = raw.get("evidence")
        if not isinstance(evidence, list) or any(
            not isinstance(path, str) or not path.strip() for path in evidence or []
        ):
            _error(errors, f"{debt_id}: evidence must be an array of non-empty paths")
            evidence = []
        elif len(set(evidence)) != len(evidence):
            _error(errors, f"{debt_id}: evidence contains duplicates")

        if status == "CLOSED":
            resolution = raw.get("resolution")
            if not isinstance(resolution, str) or not resolution.strip():
                _error(errors, f"{debt_id}: CLOSED item requires resolution")
            if not evidence:
                _error(errors, f"{debt_id}: CLOSED item requires evidence")
            for rel in evidence:
                evidence_path = (ROOT / rel).resolve()
                try:
                    evidence_path.relative_to(ROOT.resolve())
                except ValueError:
                    _error(errors, f"{debt_id}: evidence escapes repository: {rel}")
                    continue
                if not evidence_path.exists():
                    _error(errors, f"{debt_id}: evidence path missing: {rel}")
        elif status == "OPEN":
            next_action = raw.get("next_action")
            if not isinstance(next_action, str) or not next_action.strip():
                _error(errors, f"{debt_id}: OPEN item requires next_action")

        if (
            track in certified_tracks
            and severity in BLOCKING_SEVERITIES
            and status != "CLOSED"
        ):
            _error(
                errors,
                f"{debt_id}: certified track {track} cannot retain open {severity} debt",
            )

    if errors:
        for message in errors:
            print(f"ERROR: {message}", file=sys.stderr)
        return 1

    open_items = [item for item in items if isinstance(item, dict) and item.get("status") == "OPEN"]
    blocking_open = [
        item
        for item in open_items
        if item.get("severity") in BLOCKING_SEVERITIES
    ]
    print(
        "AUTO-TRADE debt register: PASS "
        f"({len(items)} items, {len(open_items)} open, {len(blocking_open)} blocking on uncertified tracks)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
