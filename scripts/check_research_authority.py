from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_ROOT = ROOT / "src" / "autotrade" / "research"

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


def main() -> int:
    errors: list[str] = []
    for path in sorted(RESEARCH_ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _forbidden_module(alias.name):
                        errors.append(
                            f"{rel}:{node.lineno}: research imports execution module {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _forbidden_module(module):
                    errors.append(
                        f"{rel}:{node.lineno}: research imports execution module {module}"
                    )
                if module == "autotrade.domain":
                    for alias in node.names:
                        if alias.name in FORBIDDEN_DOMAIN_SYMBOLS:
                            errors.append(
                                f"{rel}:{node.lineno}: research imports execution-capable domain symbol {alias.name}"
                            )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("AUTO-TRADE research authority boundary: PASS")
    return 0


def _forbidden_module(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_MODULE_PREFIXES
    )


if __name__ == "__main__":
    raise SystemExit(main())
