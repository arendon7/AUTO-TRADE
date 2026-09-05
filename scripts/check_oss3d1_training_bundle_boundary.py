from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "research" / "oss3_training_bundle.py"

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
        print("ERROR: OSS-3D1 training bundle module is missing", file=sys.stderr)
        return 1
    errors = _scan(TARGET)
    errors.extend(_runtime_probe())
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D1 training bundle boundary: PASS "
        "(TRAIN-only exact feature/label pairing; campaign/frozen-split/universe/window bound; "
        "OSS-3A training hash + feature schema + train window bound; external Qlib runtime absent; "
        "no network/process/broker/OMS/Safety/PAPER/capital/LIVE authority)"
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
            if name in FORBIDDEN_CALLS and not _safe_regex_compile(node.func):
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
    from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
    from autotrade.research.oss3_supervised_label_artifact import (
        LabelDefinition,
        LabelPartition,
        SupervisedLabelArtifact,
        SupervisedLabelRow,
    )
    from autotrade.research.oss3_training_bundle import TrainingBundleArtifact

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=2)
    origin = start + timedelta(hours=1)
    feature = FactorMatrixArtifact.build(
        campaign_id="oss3d1-boundary-campaign",
        research_split_hash="1" * 64,
        partition=FactorMatrixPartition.TRAIN,
        partition_start=start,
        partition_end=end,
        producer_code_hash="2" * 64,
        source_dataset_hash="3" * 64,
        source_universe_hash="4" * 64,
        features=(
            FactorDefinition(
                name="boundary_feature",
                dtype="float64",
                role="FEATURE",
                formula_hash="5" * 64,
                source_id="boundary-source",
                source_hash="6" * 64,
                lookback_bars=1,
            ),
        ),
        rows=(
            FactorMatrixRow(
                as_of=origin.isoformat(),
                available_at=origin.isoformat(),
                symbol="BTCUSDT",
                values=(0.5,),
            ),
        ),
    )
    horizon = origin + timedelta(hours=1)
    label = SupervisedLabelArtifact.build(
        campaign_id="oss3d1-boundary-campaign",
        research_split_hash="1" * 64,
        partition=LabelPartition.TRAIN,
        partition_start=start,
        partition_end=end,
        producer_code_hash="7" * 64,
        source_dataset_hash="8" * 64,
        source_universe_hash="4" * 64,
        label=LabelDefinition(
            name="boundary_label",
            dtype="float64",
            role="LABEL",
            formula_hash="9" * 64,
            source_id="boundary-source",
            source_hash="a" * 64,
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
    bundle = TrainingBundleArtifact.build(features=feature, labels=label)
    inference_start = end + timedelta(hours=1)
    prediction = QlibPredictionArtifact.build(
        qlib_version="0.9.7",
        model_family="boundary_model",
        model_config_hash="b" * 64,
        training_dataset_hash=bundle.training_dataset_hash,
        feature_schema_hash=bundle.manifest.feature_schema_hash,
        producer_code_hash="c" * 64,
        train_start=start,
        train_end=end,
        inference_start=inference_start,
        inference_end=inference_start + timedelta(hours=2),
        rows=(
            QlibPredictionRow(
                timestamp=(inference_start + timedelta(hours=1)).isoformat(),
                symbol="BTCUSDT",
                score=0.5,
            ),
        ),
    )
    receipt = bundle.bind_prediction(prediction)
    errors: list[str] = []
    if bundle.manifest.partition != "TRAIN":
        errors.append("runtime bundle is not TRAIN-only")
    if bundle.manifest.sample_count != 1:
        errors.append("runtime bundle sample count drifted")
    if receipt.training_dataset_hash != bundle.artifact_hash:
        errors.append("runtime receipt lost training bundle identity")
    if receipt.execution_authorized or receipt.paper_execution_authorized:
        errors.append("runtime receipt grants execution")
    if receipt.capital_authority != "NONE":
        errors.append("runtime receipt grants capital")
    if receipt.live_trading != "BLOCKED":
        errors.append("runtime receipt does not block LIVE")
    return errors


def _forbidden_module(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES)


def _safe_regex_compile(func: ast.expr) -> bool:
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
