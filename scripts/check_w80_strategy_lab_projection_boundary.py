from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
READ_MODEL = ROOT / "src/autotrade/strategy_lab_read_model.py"
ASSESSMENT_READER = ROOT / "src/autotrade/strategy_promotion_assessment_read_model.py"
SERVER = ROOT / "scripts/mac_dashboard.py"
HTML = ROOT / "web/mac_strategy_lab.html"
TEST = ROOT / "tests/test_w80_strategy_lab_projection.py"
W80_WORKFLOW = ROOT / ".github/workflows/w80-promotion-assessment.yml"
CORE_WORKFLOW = ROOT / ".github/workflows/core-tests.yml"


def main() -> int:
    errors: list[str] = []
    for path in (READ_MODEL, ASSESSMENT_READER, SERVER, HTML, TEST, W80_WORKFLOW, CORE_WORKFLOW):
        if not path.is_file():
            errors.append(f"missing W80 Strategy Lab projection file: {path.relative_to(ROOT)}")

    if READ_MODEL.is_file():
        source = READ_MODEL.read_text(encoding="utf-8")
        for anchor in (
            "from autotrade.strategy_promotion_assessment_read_model import (",
            "PromotionAssessmentReadModel",
            "PromotionAssessmentReadSnapshot",
            '"gate_evidence_state": "NOT_PERSISTED_BY_W79"',
            '"promotion_assessments": self.promotion_assessments.to_dict()',
            "durable W80 assessment evidence failed independent verification",
            "PromotionAssessmentReadModel(self._path).snapshot(now=observed_at)",
            '"paper_candidate_authorized": False',
            '"external_execution_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
        ):
            if anchor not in source:
                errors.append(f"Strategy Lab W80 projection anchor missing: {anchor}")
        if "from autotrade.strategy_promotion_assessment import" in source:
            errors.append("Strategy Lab may import W80 independent reader but never W80 writer")
        upper = source.upper()
        for mutation in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE", "DROP TABLE", "ALTER TABLE"):
            if mutation in upper:
                errors.append(f"Strategy Lab W80 projection contains SQL mutation surface: {mutation.strip()}")

    if SERVER.is_file():
        source = SERVER.read_text(encoding="utf-8")
        for anchor in (
            'if parsed.path == "/api/strategy-lab":',
            "StrategyLabPromotionReadModel(core_db).snapshot()",
            '"paper_candidate_authorized": False',
            '"credentials_used": False',
            '"external_execution_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
        ):
            if anchor not in source:
                errors.append(f"Control Center W80 Strategy Lab anchor missing: {anchor}")
        safe_actions = _literal_safe_actions(source, errors)
        if any("strategy" in key.lower() for key in safe_actions):
            errors.append("W80 Strategy Lab must remain absent from SAFE_ACTIONS")
        post_start = source.find("def do_POST")
        if post_start < 0:
            errors.append("Control Center do_POST missing")
        elif '"/api/strategy-lab"' in source[post_start:]:
            errors.append("W80 Strategy Lab API must remain GET-only")

    if HTML.is_file():
        html = HTML.read_text(encoding="utf-8")
        for anchor in (
            "W79 governance · W80 durable assessment",
            "Promotion Assessments W80 · durable",
            "Assessment ≠ autorización",
            "NO_DURABLE_W80_ASSESSMENT",
            "EVIDENCE_QUALIFIED",
            "NOT_PERSISTED_BY_W79",
            "los resultados de gates NO se sintetizan",
            "W79 snapshot",
            "W80 assessments",
            "PAPER CANDIDATE · FALSE",
            "CAPITAL · NONE",
            "LIVE · BLOCKED",
            "Broker POST: NO",
            'fetch("/api/strategy-lab?workspace="',
            'method:"GET"',
        ):
            if anchor not in html:
                errors.append(f"W80 Strategy Lab UI anchor missing: {anchor}")
        for forbidden in (
            'method:"POST"',
            "/api/action",
            "/api/rehearsal",
            "/api/canary-preview",
            "localStorage",
            "sessionStorage",
            'type="password"',
            "APCA_API_KEY_ID",
            "APCA_API_SECRET_KEY",
            '<script src=',
            '<link rel="stylesheet" href=',
        ):
            if forbidden in html:
                errors.append(f"W80 Strategy Lab UI contains forbidden surface: {forbidden}")

    workflow_marker = "python scripts/check_w80_strategy_lab_projection_boundary.py"
    for workflow, label in ((W80_WORKFLOW, "W80 workflow"), (CORE_WORKFLOW, "Core Safety")):
        if workflow.is_file() and workflow_marker not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: W80 Strategy Lab projection boundary not wired")

    if W80_WORKFLOW.is_file():
        workflow = W80_WORKFLOW.read_text(encoding="utf-8")
        if "tests/test_w80_strategy_lab_projection.py" not in workflow:
            errors.append("W80 workflow does not run Strategy Lab projection tests")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "W80 STRATEGY LAB DURABLE PROJECTION BOUNDARY PASS — W79 governance remains separate; "
        "W80 journal independently verified and GET-only; dual provenance exposed; no writer/broker/credentials/OMS/Safety/POST authority; "
        "PAPER candidate false; capital NONE; LIVE blocked"
    )
    return 0


def _literal_safe_actions(source: str, errors: list[str]) -> set[str]:
    tree = ast.parse(source, filename=str(SERVER.relative_to(ROOT)))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "SAFE_ACTIONS":
            if not isinstance(node.value, ast.Dict):
                errors.append("SAFE_ACTIONS must remain a literal dictionary")
                return set()
            result: set[str] = set()
            for key in node.value.keys:
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    errors.append("SAFE_ACTIONS keys must remain literal strings")
                    return set()
                result.add(key.value)
            return result
    errors.append("SAFE_ACTIONS dictionary not found")
    return set()


if __name__ == "__main__":
    raise SystemExit(main())
