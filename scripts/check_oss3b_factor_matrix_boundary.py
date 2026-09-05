from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "research" / "oss3_factor_matrix_artifact.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "qlib",
    "mlflow",
    "redis",
    "subprocess",
    "socket",
    "urllib",
    "http",
    "requests",
    "aiohttp",
    "pickle",
    "cloudpickle",
    "dill",
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
)
FORBIDDEN_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "import_module",
    "system",
    "popen",
    "run",
    "Popen",
    "urlopen",
    "open_connection",
    "create_connection",
    "submit_order",
    "place_order",
    "send_order",
    "execute_order",
}
FORBIDDEN_NAMES = {"OrderIntent", "RiskDecision", "CapitalSafetyKernel"}


def main() -> int:
    if not TARGET.is_file():
        print("ERROR: OSS-3B factor matrix module is missing", file=sys.stderr)
        return 1
    errors = _scan(TARGET)
    errors.extend(_runtime_probe())
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3B factor matrix boundary: PASS "
        "(TRAIN/DEVELOPMENT only; FINAL_HOLDOUT denied; labels absent; "
        "point-in-time availability enforced; no Qlib/network/process/broker/OMS/"
        "Safety/PAPER/capital/LIVE authority)"
    )
    return 0


def _scan(path: Path) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path.relative_to(ROOT)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_module(alias.name):
                    errors.append(f"line {node.lineno}: forbidden import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_module(module):
                errors.append(f"line {node.lineno}: forbidden import {module}")
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    errors.append(f"line {node.lineno}: forbidden authority symbol {alias.name}")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS and not _is_allowlisted_safe_call(node.func):
                errors.append(f"line {node.lineno}: forbidden call {name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"line {node.lineno}: forbidden authority symbol {node.id}")
    return errors


def _runtime_probe() -> list[str]:
    from autotrade.research.oss3_factor_matrix_artifact import (
        FactorDefinition,
        FactorMatrixArtifact,
        FactorMatrixPartition,
        FactorMatrixRow,
    )

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    as_of = start + timedelta(days=1)
    artifact = FactorMatrixArtifact.build(
        partition=FactorMatrixPartition.TRAIN,
        partition_start=start,
        partition_end=start + timedelta(days=2),
        producer_code_hash="1" * 64,
        source_dataset_hash="2" * 64,
        source_universe_hash="3" * 64,
        features=(
            FactorDefinition(
                name="boundary_feature",
                dtype="float64",
                role="FEATURE",
                formula_hash="4" * 64,
                source_id="boundary-source",
                source_hash="5" * 64,
                lookback_bars=1,
            ),
        ),
        rows=(
            FactorMatrixRow(
                as_of=as_of.isoformat(),
                available_at=as_of.isoformat(),
                symbol="BTCUSDT",
                values=(0.5,),
            ),
        ),
    )
    evidence = artifact.to_research_evidence()
    errors: list[str] = []
    if evidence.partition not in {"TRAIN", "DEVELOPMENT"}:
        errors.append("runtime evidence exposes forbidden partition")
    if evidence.labels_included:
        errors.append("runtime evidence claims labels")
    if evidence.final_holdout_included:
        errors.append("runtime evidence claims FINAL_HOLDOUT")
    if evidence.execution_authorized:
        errors.append("runtime evidence grants execution")
    if evidence.paper_execution_authorized:
        errors.append("runtime evidence grants PAPER execution")
    if evidence.capital_authority != "NONE":
        errors.append("runtime evidence grants capital authority")
    if evidence.live_trading != "BLOCKED":
        errors.append("runtime evidence does not block LIVE")
    return errors


def _forbidden_module(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES)


def _is_allowlisted_safe_call(func: ast.expr) -> bool:
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "compile"
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
    )


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
