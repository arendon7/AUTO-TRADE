from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_coverage_floor.py COVERAGE_JSON MIN_PERCENT", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        minimum = Decimal(sys.argv[2])
    except InvalidOperation:
        print("ERROR: MIN_PERCENT must be a decimal", file=sys.stderr)
        return 2
    if not minimum.is_finite() or not Decimal("0") <= minimum <= Decimal("100"):
        print("ERROR: MIN_PERCENT must be finite in [0,100]", file=sys.stderr)
        return 2
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        raw_percent = document["totals"]["percent_covered"]
        percent = Decimal(str(raw_percent))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, InvalidOperation) as exc:
        print(f"ERROR: coverage JSON is invalid: {exc}", file=sys.stderr)
        return 2
    if not percent.is_finite() or not Decimal("0") <= percent <= Decimal("100"):
        print("ERROR: coverage percent is invalid", file=sys.stderr)
        return 2
    print(f"AUTO-TRADE coverage floor: measured={percent}% required={minimum}%")
    if percent < minimum:
        print(
            f"ERROR: coverage {percent}% is below required {minimum}%",
            file=sys.stderr,
        )
        return 1
    print("AUTO-TRADE coverage floor: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
