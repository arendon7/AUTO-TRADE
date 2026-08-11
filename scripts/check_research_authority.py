from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_ROOT = ROOT / "src" / "autotrade" / "research"
PORTFOLIO_MANAGER = ROOT / "src" / "autotrade" / "portfolio_manager.py"

FORBIDDEN_MODULE_PREFIXES = (
    "autotrade.oms",
    "autotrade.engine",
    "autotrade.brokers",
    "autotrade.bootstrap",
    "autotrade.safety",
)
FORBIDDEN_DOMAIN_SYMBOLS = {
    "OrderIntent",
    "OrderRecord",
    "RiskDecision",
}
PORTFOLIO_MANAGER_FORBIDDEN_CALLS = {
    "submit",
    "submit_order",
    "place_order",
    "execute_order",
    "cancel_order",
    "replace_order",
    "send_order",
}


def main() -> int:
    errors: list[str] = []
    for path in sorted(RESEARCH_ROOT.rglob("*.py")):
        errors.extend(_scan_file(path, forbid_execution_calls=False))

    if not PORTFOLIO_MANAGER.exists():
        errors.append("src/autotrade/portfolio_manager.py: advisory Portfolio Manager is missing")
    else:
        errors.extend(_scan_file(PORTFOLIO_MANAGER, forbid_execution_calls=True))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("AUTO-TRADE research/advisory authority boundary: PASS")
    return 0


def _scan_file(path: Path, *, forbid_execution_calls: bool) -> list[str]:
    errors: list[str] = []
    try:
        rel: Path | str = path.relative_to(ROOT)
    except ValueError:
        # Synthetic probes used by the checker tests intentionally live outside
        # the repository. Path rendering must never change the security rules.
        rel = path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_module(alias.name):
                    errors.append(
                        f"{rel}:{node.lineno}: imports execution module {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_module(module):
                errors.append(
                    f"{rel}:{node.lineno}: imports execution module {module}"
                )
            if module == "autotrade.domain":
                for alias in node.names:
                    if alias.name in FORBIDDEN_DOMAIN_SYMBOLS:
                        errors.append(
                            f"{rel}:{node.lineno}: imports execution-capable domain symbol {alias.name}"
                        )
        elif forbid_execution_calls and isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name in PORTFOLIO_MANAGER_FORBIDDEN_CALLS:
                errors.append(
                    f"{rel}:{node.lineno}: advisory Portfolio Manager calls execution-like method {call_name}"
                )
        elif forbid_execution_calls and isinstance(node, ast.Name):
            if node.id in FORBIDDEN_DOMAIN_SYMBOLS:
                errors.append(
                    f"{rel}:{node.lineno}: advisory Portfolio Manager references execution-capable symbol {node.id}"
                )
    return errors


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _forbidden_module(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_MODULE_PREFIXES
    )


if __name__ == "__main__":
    raise SystemExit(main())
