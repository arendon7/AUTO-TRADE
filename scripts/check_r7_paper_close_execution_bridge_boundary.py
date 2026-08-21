from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/paper_close_execution_bridge.py"
SRC = ROOT / "src/autotrade"
REQUIRED = (
    "class PaperCloseExecutionBridge:",
    "bind_paper_close_execution_authority",
    "PreparedPaperCloseControlPlane",
    "ExternalSubmissionHandoff",
    "control_plane.decision.risk_reducing is not True",
    "oms_handoff.safety_state_version != control_plane.decision.safety_state_version",
    "oms_handoff.risk_decision_id != control_plane.decision.decision_id",
    "self._writer.submit_once(",
)
FORBIDDEN = (".post(", "http.client", "requests", "api.alpaca.markets", "OpenAI", "Anthropic")


def fail(message: str) -> None:
    print(f"R7 close execution bridge boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if not MODULE.is_file():
        fail("missing R7 close execution bridge")
    text = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(MODULE))
    for anchor in REQUIRED:
        if anchor not in text:
            fail(f"missing human/Safety/OMS chain anchor: {anchor}")
    for token in FORBIDDEN:
        if token in text:
            fail(f"execution bridge contains forbidden direct broker/AI authority: {token}")
    writer_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "submit_once"
    ]
    if len(writer_calls) != 1:
        fail(f"execution bridge must invoke low-level writer exactly once, found {len(writer_calls)}")

    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name in {"paper_close_writer.py", "paper_close_execution_bridge.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        if "PaperCloseWriter" in source or "paper_close_writer import" in source:
            offenders.append(str(path.relative_to(ROOT)))
    if offenders:
        fail(f"low-level close writer imported outside certified bridge: {offenders}")
    print(
        "R7 close execution bridge boundary: PASS — human decision + broker plan + Capital Safety + OMS handoff "
        "are bound before the only production low-level writer invocation"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
