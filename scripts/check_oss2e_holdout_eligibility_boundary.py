from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "research" / "oss2_holdout_eligibility.py"

FORBIDDEN_IMPORT_FRAGMENTS = (
    "broker",
    "oms",
    "safety",
    "execution",
    "order_intent",
    "holdout_data",
)

FORBIDDEN_CALL_NAMES = {
    "open",
    "connect",
    "urlopen",
    "request",
    "post",
    "submit",
    "execute",
}


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        for name in names:
            lowered = name.lower()
            if any(fragment in lowered for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                raise SystemExit(f"OSS-2E forbidden authority import: {name}")

        if isinstance(node, ast.Call):
            func = node.func
            call_name = None
            if isinstance(func, ast.Name):
                call_name = func.id
            elif isinstance(func, ast.Attribute):
                call_name = func.attr
            if call_name and call_name.lower() in FORBIDDEN_CALL_NAMES:
                raise SystemExit(f"OSS-2E forbidden I/O or execution call: {call_name}")

    if "FINAL_HOLDOUT" not in source:
        raise SystemExit("OSS-2E boundary documentation must state FINAL_HOLDOUT restriction")
    if "def evaluate_oss2e_holdout_eligibility" not in source:
        raise SystemExit("OSS-2E canonical evaluator missing")

    print(
        "AUTO-TRADE OSS-2E holdout eligibility boundary: PASS "
        "(DEVELOPMENT evidence only; no holdout input/I-O/broker/OMS/Safety/execution authority)"
    )


if __name__ == "__main__":
    main()
