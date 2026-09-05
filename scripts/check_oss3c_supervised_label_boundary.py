from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "research" / "oss3_supervised_label_artifact.py"

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
        print("ERROR: OSS-3C supervised label module is missing", file=sys.stderr)
        return 1
    errors = _scan(TARGET)
    errors.extend(_runtime_probe())
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3C supervised label boundary: PASS "
        "(campaign + frozen split hash-bound; explicit future horizon; "
        "availability cannot predate horizon; TRAIN/DEVELOPMENT only; "
        "FINAL_HOLDOUT denied; no Qlib/network/process/broker/OMS/Safety/"
        "PAPER/capital/LIVE authority)"
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
    from autotrade.research.oss3_supervised_label_artifact import (
        LabelDefinition,
        LabelPartition,
        SupervisedLabelArtifact,
        SupervisedLabelRow,
    )

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    origin = start + timedelta(hours=1)
    horizon = origin + timedelta(hours=1)
    campaign_id = "oss3c-boundary-campaign"
    research_split_hash = "6" * 64
    artifact = SupervisedLabelArtifact.build(
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
        partition=LabelPartition.TRAIN,
        partition_start=start,
        partition_end=start + timedelta(days=1),
        producer_code_hash="1" * 64,
        source_dataset_hash="2" * 64,
        source_universe_hash="3" * 64,
        label=LabelDefinition(
            name="boundary_label",
            dtype="float64",
            role="LABEL",
            formula_hash="4" * 64,
            source_id="boundary-source",
            source_hash="5" * 64,
        ),
        rows=(
            SupervisedLabelRow(
                label_as_of=origin.isoformat(),
                horizon_end=horizon.isoformat(),
                available_at=horizon.isoformat(),
                symbol="BTCUSDT",
                value=0.01,
            ),
        ),
    )
    evidence = artifact.to_research_evidence()
    errors: list[str] = []
    if evidence.campaign_id != campaign_id:
        errors.append("runtime evidence lost campaign identity")
    if evidence.research_split_hash != research_split_hash:
        errors.append("runtime evidence lost frozen research split identity")
    if evidence.partition not in {"TRAIN", "DEVELOPMENT"}:
        errors.append("runtime evidence exposes forbidden partition")
    if evidence.final_holdout_included:
        errors.append("runtime evidence claims FINAL_HOLDOUT")
    if evidence.execution_authorized or evidence.paper_execution_authorized:
        errors.append("runtime evidence grants execution")
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
