from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "src" / "autotrade" / "brokers" / "paper_execution.py",
    ROOT / "src" / "autotrade" / "paper_execution_scenarios.py",
    ROOT / "src" / "autotrade" / "paper_execution_evidence.py",
    ROOT / "src" / "autotrade" / "paper_execution_qualification.py",
)
FORBIDDEN_IMPORT_ROOTS = {
    "http",
    "socket",
    "urllib",
    "requests",
    "aiohttp",
    "websockets",
}
FORBIDDEN_AUTOTRADE_IMPORT_FRAGMENTS = {
    "alpaca",
    "paper_close_writer",
    "external_paper",
    "real_paper",
}
FORBIDDEN_TEXT = (
    "HTTPSConnection",
    "http://",
    "https://",
    "APCA-API-KEY-ID",
    "APCA-API-SECRET-KEY",
    "R7_CLOSE_PAPER_WRITE",
    "ALPACA_PAPER_TRADING_HOST",
)


def fail(message: str) -> None:
    raise SystemExit(f"W78 PAPER EXECUTION BOUNDARY FAIL: {message}")


def _scan(path: Path) -> str:
    if not path.is_file():
        fail(f"required W78 module is missing: {path.relative_to(ROOT)}")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in FORBIDDEN_IMPORT_ROOTS:
                    fail(f"{path.name}: forbidden network import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".", 1)[0] in FORBIDDEN_IMPORT_ROOTS:
                fail(f"{path.name}: forbidden network import: {module}")
            lowered = module.lower()
            if module.startswith("autotrade") and any(
                fragment in lowered for fragment in FORBIDDEN_AUTOTRADE_IMPORT_FRAGMENTS
            ):
                fail(f"{path.name}: imported broker-write authority: {module}")

    for text in FORBIDDEN_TEXT:
        if text in source:
            fail(f"{path.name}: forbidden execution-authority marker present: {text}")
    return source


def main() -> None:
    sources = {path.name: _scan(path) for path in TARGETS}
    required_markers = {
        "paper_execution.py": (
            "class DeterministicPaperExecutionBroker",
            "class PaperExecutionConfig",
            "no-network PAPER execution broker",
        ),
        "paper_execution_scenarios.py": (
            "class PaperExecutionScenario",
            "class PaperExecutionScenarioMatrix",
            "scenario_hash",
            "matrix_hash",
        ),
        "paper_execution_evidence.py": (
            "class PaperExecutionEvidence",
            "evidence_hash",
            "UNKNOWN execution requires reconciliation",
        ),
        "paper_execution_qualification.py": (
            "class PaperExecutionQualificationContract",
            "external_execution_authorized",
            'live_trading": value.live_trading',
            "may not grant external/LIVE authority",
        ),
    }
    for filename, markers in required_markers.items():
        source = sources[filename]
        for marker in markers:
            if marker not in source:
                fail(f"{filename}: required fail-closed marker missing: {marker}")

    qualification = sources["paper_execution_qualification.py"]
    if '"external_execution_authorized": False' not in qualification:
        fail("qualification contract does not hard-code zero external authority")
    if '"live_trading": "BLOCKED"' not in qualification:
        fail("qualification contract does not hard-code LIVE BLOCKED")

    print("W78 PAPER EXECUTION BOUNDARY PASS")


if __name__ == "__main__":
    try:
        main()
    except SyntaxError as exc:
        print(f"W78 PAPER EXECUTION BOUNDARY FAIL: syntax error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
