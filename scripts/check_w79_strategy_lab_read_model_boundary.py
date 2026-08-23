from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
READ_MODEL = ROOT / "src/autotrade/strategy_lab_read_model.py"
SERVER = ROOT / "scripts/mac_dashboard.py"
HTML = ROOT / "web/mac_strategy_lab.html"
HUB = ROOT / "web/mac_multi_asset.html"
W79_WORKFLOW = ROOT / ".github/workflows/w79-strategy-promotion.yml"
CORE_WORKFLOW = ROOT / ".github/workflows/core-tests.yml"

FORBIDDEN_READ_MODEL_MODULES = (
    "autotrade.brokers",
    "autotrade.engine",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.domain",
    "autotrade.persistence",
    "autotrade.research.trials",
    "requests",
    "httpx",
    "urllib.request",
    "socket",
    "websockets",
)
FORBIDDEN_READ_MODEL_NAMES = {
    "SQLiteRuntime",
    "SQLiteTrialLedger",
    "OrderIntent",
    "TradingPipeline",
    "CapitalSafetyKernel",
    "OrderManagementSystem",
}
FORBIDDEN_MUTATION_CALLS = {
    "execute_order",
    "place_order",
    "submit",
    "submit_order",
    "cancel_order",
    "replace_order",
    "send_order",
}


def main() -> int:
    errors: list[str] = []
    for path in (READ_MODEL, SERVER, HTML, HUB, W79_WORKFLOW, CORE_WORKFLOW):
        if not path.is_file():
            errors.append(f"missing W79 Strategy Lab contract file: {path.relative_to(ROOT)}")

    if READ_MODEL.is_file():
        source = READ_MODEL.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(READ_MODEL.relative_to(ROOT)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _forbidden_module(alias.name):
                        errors.append(f"read model line {node.lineno}: forbidden import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _forbidden_module(module):
                    errors.append(f"read model line {node.lineno}: forbidden import {module}")
                for alias in node.names:
                    if alias.name in FORBIDDEN_READ_MODEL_NAMES:
                        errors.append(f"read model line {node.lineno}: forbidden symbol {alias.name}")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_READ_MODEL_NAMES:
                errors.append(f"read model line {node.lineno}: forbidden symbol {node.id}")
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in FORBIDDEN_MUTATION_CALLS:
                    errors.append(f"read model line {node.lineno}: forbidden authority call {name}")
        for anchor in (
            'f"file:{encoded}?mode=ro"',
            'conn.execute("PRAGMA query_only=ON")',
            '"gate_evidence_state": "NOT_PERSISTED_BY_W79"',
            '"paper_candidate_authorized": False',
            '"external_execution_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
            '"broker_network_used": False',
            '"broker_write_performed": False',
            '"credentials_used": False',
        ):
            if anchor not in source:
                errors.append(f"read model fail-closed anchor missing: {anchor}")
        upper = source.upper()
        for mutation in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE", "DROP TABLE", "ALTER TABLE"):
            if mutation in upper:
                errors.append(f"read model contains SQL mutation surface: {mutation.strip()}")

    if SERVER.is_file():
        source = SERVER.read_text(encoding="utf-8")
        for anchor in (
            'STRATEGY_HTML_PATH = ROOT / "web/mac_strategy_lab.html"',
            '"strategy_lab_route": "/strategy-lab"',
            '"strategy_lab_read_only": True',
            '"strategy_lab_paper_candidate_authorized": False',
            '"strategy_lab_gate_evidence": "NOT_PERSISTED_BY_W79"',
            '"/strategy-lab": STRATEGY_HTML_PATH',
            'if parsed.path == "/api/strategy-lab":',
            "StrategyLabPromotionReadModel(core_db).snapshot()",
            '"paper_candidate_authorized": False',
            '"credentials_used": False',
            '"external_execution_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
        ):
            if anchor not in source:
                errors.append(f"Control Center Strategy Lab anchor missing: {anchor}")
        safe_actions = _literal_safe_actions(source, errors)
        if any("strategy" in name.lower() for name in safe_actions):
            errors.append("Strategy Lab must not enter SAFE_ACTIONS POST command allowlist")
        post_block = source[source.find("def do_POST") :]
        if '"/api/strategy-lab"' in post_block:
            errors.append("Strategy Lab API must never be accepted by do_POST")

    if HTML.is_file():
        html = HTML.read_text(encoding="utf-8")
        for anchor in (
            "AUTO-TRADE · Strategy Lab",
            "READ ONLY",
            "PAPER CANDIDATE · FALSE",
            "CAPITAL · NONE",
            "LIVE · BLOCKED",
            "Broker POST: NO",
            "NOT_PERSISTED_BY_W79",
            "Actualizar evidencia · GET",
            'fetch("/api/strategy-lab?workspace="',
            'method:"GET"',
        ):
            if anchor not in html:
                errors.append(f"Strategy Lab UI anchor missing: {anchor}")
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
                errors.append(f"Strategy Lab UI contains forbidden surface: {forbidden}")

    if HUB.is_file():
        hub = HUB.read_text(encoding="utf-8")
        for anchor in (
            "Strategy Lab",
            'href="/strategy-lab"',
            "SQLite mode=ro + query_only",
            "PAPER candidate FALSE · CAPITAL NONE · LIVE BLOCKED",
        ):
            if anchor not in hub:
                errors.append(f"Hub Strategy Lab anchor missing: {anchor}")

    workflow_marker = "python scripts/check_w79_strategy_lab_read_model_boundary.py"
    for workflow, label in ((W79_WORKFLOW, "W79 workflow"), (CORE_WORKFLOW, "Core Safety")):
        if workflow.is_file() and workflow_marker not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: Strategy Lab read-only boundary not wired")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "W79 STRATEGY LAB READ MODEL BOUNDARY PASS — SQLite mode=ro/query_only; "
        "GET-only UI; no SAFE_ACTION/POST/broker/credentials/OMS/Safety/OrderIntent authority; "
        "gate results are not synthesized; PAPER candidate false; capital NONE; LIVE blocked"
    )
    return 0


def _forbidden_module(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_READ_MODEL_MODULES)


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _literal_safe_actions(source: str, errors: list[str]) -> set[str]:
    tree = ast.parse(source, filename=str(SERVER.relative_to(ROOT)))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "SAFE_ACTIONS":
            if not isinstance(node.value, ast.Dict):
                errors.append("SAFE_ACTIONS must remain a literal dictionary")
                return set()
            values: set[str] = set()
            for key in node.value.keys:
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    errors.append("SAFE_ACTIONS keys must remain literal strings")
                    return set()
                values.add(key.value)
            return values
    errors.append("SAFE_ACTIONS dictionary not found")
    return set()


if __name__ == "__main__":
    raise SystemExit(main())