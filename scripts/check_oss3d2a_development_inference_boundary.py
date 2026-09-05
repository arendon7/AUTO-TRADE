from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "research" / "oss3_development_inference.py"

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
    "autotrade.research.oss3_supervised_label_artifact",
    "oss3_supervised_label_artifact",
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
FORBIDDEN_NAMES = {
    "OrderIntent",
    "RiskDecision",
    "CapitalSafetyKernel",
    "SupervisedLabelArtifact",
    "LabelEvidence",
}
REQUIRED_SOURCE_SNIPPETS = (
    'LABEL_ACCESS_POLICY = "FORBID_DEVELOPMENT_LABELS_V1"',
    'PREDICTION_KEY_POLICY = "EXACT_TIMESTAMP_SYMBOL_KEYSET_V1"',
    'development_partition != "DEVELOPMENT"',
    "development_labels_loaded: bool = False",
    "final_holdout_loaded: bool = False",
    "external_runtime_invoked: bool = False",
    "qlib_imported: bool = False",
    "prediction_artifact_created: bool = False",
)


def main() -> int:
    if not TARGET.is_file():
        print("ERROR: OSS-3D2A DEVELOPMENT inference module is missing", file=sys.stderr)
        return 1
    source = TARGET.read_text(encoding="utf-8")
    errors = _scan(source)
    errors.extend(_source_contract(source))
    errors.extend(_runtime_probe())
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D2A DEVELOPMENT inference boundary: PASS "
        "(TRAIN bundle + DEVELOPMENT features only; DEVELOPMENT labels absent; exact "
        "campaign/frozen-split/universe/schema/payload/keyset binding; concrete artifacts "
        "revalidated before dry-run/receipt; no fabricated Qlib provenance; no external "
        "runtime/network/process/broker/OMS/Safety/PAPER/capital/LIVE authority)"
    )
    return 0


def _scan(source: str) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(source, filename=str(TARGET.relative_to(ROOT)))
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
                    errors.append(f"line {node.lineno}: forbidden authority/label symbol {alias.name}")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS and not _safe_regex_compile(node.func):
                errors.append(f"line {node.lineno}: forbidden call {name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"line {node.lineno}: forbidden authority/label symbol {node.id}")
    return errors


def _source_contract(source: str) -> list[str]:
    errors: list[str] = []
    for snippet in REQUIRED_SOURCE_SNIPPETS:
        if snippet not in source:
            errors.append(f"required fail-closed contract is missing: {snippet}")
    return errors


def _runtime_probe() -> list[str]:
    from autotrade.research.oss3_development_inference import DevelopmentInferenceRequest
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

    train_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    train_end = train_start + timedelta(days=2)
    train_as_of = train_start + timedelta(hours=1)
    dev_start = train_end
    dev_end = dev_start + timedelta(hours=2)
    feature_definition = FactorDefinition(
        name="boundary_feature",
        dtype="float64",
        role="FEATURE",
        formula_hash="1" * 64,
        source_id="boundary-source",
        source_hash="2" * 64,
        lookback_bars=1,
    )
    train_feature = FactorMatrixArtifact.build(
        campaign_id="oss3d2a-boundary-campaign",
        research_split_hash="3" * 64,
        partition=FactorMatrixPartition.TRAIN,
        partition_start=train_start,
        partition_end=train_end,
        producer_code_hash="4" * 64,
        source_dataset_hash="5" * 64,
        source_universe_hash="6" * 64,
        features=(feature_definition,),
        rows=(
            FactorMatrixRow(
                as_of=train_as_of.isoformat(),
                available_at=train_as_of.isoformat(),
                symbol="BTCUSDT",
                values=(0.5,),
            ),
        ),
    )
    horizon_end = train_as_of + timedelta(hours=1)
    train_label = SupervisedLabelArtifact.build(
        campaign_id="oss3d2a-boundary-campaign",
        research_split_hash="3" * 64,
        partition=LabelPartition.TRAIN,
        partition_start=train_start,
        partition_end=train_end,
        producer_code_hash="7" * 64,
        source_dataset_hash="8" * 64,
        source_universe_hash="6" * 64,
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
                label_as_of=train_as_of.isoformat(),
                horizon_end=horizon_end.isoformat(),
                available_at=horizon_end.isoformat(),
                symbol="BTCUSDT",
                value=0.01,
            ),
        ),
    )
    bundle = TrainingBundleArtifact.build(features=train_feature, labels=train_label)
    dev_feature = FactorMatrixArtifact.build(
        campaign_id="oss3d2a-boundary-campaign",
        research_split_hash="3" * 64,
        partition=FactorMatrixPartition.DEVELOPMENT,
        partition_start=dev_start,
        partition_end=dev_end,
        producer_code_hash="4" * 64,
        source_dataset_hash="b" * 64,
        source_universe_hash="6" * 64,
        features=(feature_definition,),
        rows=(
            FactorMatrixRow(
                as_of=dev_start.isoformat(),
                available_at=dev_start.isoformat(),
                symbol="BTCUSDT",
                values=(0.7,),
            ),
        ),
    )
    request = DevelopmentInferenceRequest.build(
        training_bundle=bundle,
        development_features=dev_feature,
        model_family="boundary_model",
        model_config_hash="c" * 64,
        required_qlib_version="0.9.7",
        expected_runner_code_hash="d" * 64,
    )
    dry_run = request.dry_run(training_bundle=bundle, development_features=dev_feature)
    prediction = QlibPredictionArtifact.build(
        qlib_version="0.9.7",
        model_family="boundary_model",
        model_config_hash="c" * 64,
        training_dataset_hash=bundle.artifact_hash,
        feature_schema_hash=bundle.manifest.feature_schema_hash,
        producer_code_hash="d" * 64,
        train_start=train_start,
        train_end=train_end,
        inference_start=dev_start,
        inference_end=dev_end,
        rows=(
            QlibPredictionRow(
                timestamp=dev_start.isoformat(),
                symbol="BTCUSDT",
                score=0.5,
            ),
        ),
    )
    receipt = request.bind_prediction(
        prediction=prediction,
        training_bundle=bundle,
        development_features=dev_feature,
    )
    errors: list[str] = []
    if request.manifest.development_partition != "DEVELOPMENT":
        errors.append("runtime request is not DEVELOPMENT-only")
    if request.manifest.training_bundle_hash != bundle.artifact_hash:
        errors.append("runtime request lost TRAIN bundle identity")
    if request.manifest.development_feature_artifact_hash != dev_feature.artifact_hash:
        errors.append("runtime request lost DEVELOPMENT feature identity")
    if dry_run.development_labels_loaded or dry_run.final_holdout_loaded:
        errors.append("runtime dry run accessed forbidden labels/holdout")
    if dry_run.external_runtime_invoked or dry_run.qlib_imported:
        errors.append("runtime dry run invoked external ML runtime")
    if dry_run.prediction_artifact_created:
        errors.append("runtime dry run fabricated prediction provenance")
    if receipt.development_labels_loaded or receipt.final_holdout_loaded:
        errors.append("runtime receipt claims forbidden labels/holdout")
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
