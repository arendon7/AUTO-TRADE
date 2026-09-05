from __future__ import annotations

from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/research/oss2_final_holdout_evaluation.py"
TEST = ROOT / "tests/test_research_oss2_final_holdout_evaluation.py"
WORKFLOW = ROOT / ".github/workflows/oss2h-final-holdout-evaluation.yml"


FORBIDDEN_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "urllib",
    "socket",
    "subprocess",
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
)

FORBIDDEN_TEXT = (
    "paper_execution_authorized=True",
    'capital_authority="',
    'live_trading="ENABLED"',
    "OrderIntent(",
    "submit_order",
    "cancel_order",
    "replace_order",
    "api.alpaca",
    "paper-api",
    "trading-api",
    "requests.",
    "httpx.",
)

REQUIRED_TEXT = (
    'OSS2H_CONTRACT_VERSION = "OSS2H_FINAL_HOLDOUT_EVALUATION_V1"',
    '_FINAL_VALIDATION = "final_validation"',
    "max_evaluations != 1",
    "protocol.retuning_allowed",
    "protocol.reselection_allowed",
    "protocol.second_attempt_allowed",
    "INSERT INTO holdout_permits",
    "BEGIN IMMEDIATE",
    "oss2_final_holdout_evaluation_starts",
    "oss2_final_holdout_evaluations",
    "mode=ro",
    "PRAGMA query_only = ON",
    "CrossSectionalBacktestEngine().run",
    "backtest_config_from_oss2_trial",
    "FINAL_NET_RETURN_MIN",
    "FINAL_SHARPE_MIN",
    "FINAL_DRAWDOWN_MAX",
    'paper_execution_authorized=False',
    'capital_authority="NONE"',
    'live_trading="BLOCKED"',
)


def _imports(tree: ast.AST) -> tuple[str, ...]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    return tuple(found)


def main() -> None:
    for path in (MODULE, TEST, WORKFLOW):
        if not path.is_file():
            raise SystemExit(f"OSS-2H boundary missing required file: {path.relative_to(ROOT)}")

    text = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imports = _imports(tree)
    for imported in imports:
        if imported.startswith(FORBIDDEN_IMPORT_PREFIXES):
            raise SystemExit(f"OSS-2H forbidden import: {imported}")

    for token in FORBIDDEN_TEXT:
        if token in text:
            if token == 'capital_authority="' and 'capital_authority="NONE"' in text:
                continue
            raise SystemExit(f"OSS-2H forbidden authority/network token: {token}")

    for token in REQUIRED_TEXT:
        if token not in text:
            raise SystemExit(f"OSS-2H missing structural invariant: {token}")

    public_methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    forbidden_public = {
        "checkout",
        "retune",
        "reselect",
        "retry",
        "submit",
        "execute_order",
        "enable_live",
    }
    overlap = public_methods & forbidden_public
    if overlap:
        raise SystemExit(f"OSS-2H exposes forbidden public method(s): {sorted(overlap)}")

    workflow = WORKFLOW.read_text(encoding="utf-8").lower()
    for token in ("curl ", "wget ", "alpaca", "api_key", "secret", "workflow_dispatch"):
        if token in workflow:
            raise SystemExit(f"OSS-2H workflow may not access external holdout/network material: {token}")
    if "test_research_oss2_final_holdout_evaluation.py" not in workflow:
        raise SystemExit("OSS-2H workflow does not execute dedicated tests")
    if "check_oss2h_final_holdout_evaluation_boundary.py" not in workflow:
        raise SystemExit("OSS-2H workflow does not execute its authority boundary")

    print(
        "AUTO-TRADE OSS-2H FINAL_HOLDOUT evaluation boundary: PASS "
        "(single-use final_validation consumption; exact frozen candidate/config; "
        "three preregistered gates; append-only terminal PASS/FAIL; "
        "no broker/network/OMS/Safety/PAPER/capital/LIVE authority)"
    )


if __name__ == "__main__":
    main()
