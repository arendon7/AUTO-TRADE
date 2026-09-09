from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src/autotrade/research/oss3_market_collection.py"
ACQUISITION = ROOT / "labs/oss3_market_data/archive_acquisition.py"


class BoundaryFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BoundaryFailure(message)


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def function_source(source: str, name: str, next_name: str) -> str:
    start = source.index(f"def {name}")
    end = source.index(f"def {next_name}", start)
    return source[start:end]


def main() -> int:
    require(CORE.is_file(), "D2U core module is missing")
    require(ACQUISITION.is_file(), "D2U acquisition lab is missing")
    core = CORE.read_text(encoding="utf-8")
    acquisition = ACQUISITION.read_text(encoding="utf-8")
    core_tree = ast.parse(core, filename=str(CORE))
    acquisition_tree = ast.parse(acquisition, filename=str(ACQUISITION))

    for marker in (
        "OSS3D2U_HISTORICAL_COLLECTION_PLAN_V2",
        "FINITE_MONTHLY_ARCHIVE_FAMILY_PREREGISTERED_BEFORE_NETWORK_V1",
        "EXACT_MONTH_TO_MONTH_NO_GAP_NO_OVERLAP_NO_FILL_V1",
        "WARMUP20_THEN_TRAIN_THEN_DEVELOPMENT_NO_HOLDOUT_VALUES_V1",
        "oss3d2u_historical_collection_plans",
        "OSS3D2U_APPEND_ONLY",
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        'CANONICAL_INTERVAL = "1h"',
        'CANONICAL_FIRST_MONTH = "2023-04"',
        'CANONICAL_LAST_MONTH = "2025-12"',
        "CANONICAL_WARMUP_BARS = 20",
        "final_holdout_descriptors_included=False",
        "label_values_included=False",
        "prediction_values_included=False",
        "final_holdout_values_loaded=False",
        "network_used_by_assembly=False",
        "execution_authorized=False",
        "paper_execution_authorized=False",
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
    ):
        require(marker in core, f"missing D2U core boundary marker: {marker}")

    # Core assembly is fully offline and model/evaluator-free.
    for node in ast.walk(core_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                require(root not in {"socket", "urllib", "requests", "httpx", "aiohttp", "subprocess"}, f"forbidden D2U core network/process import: {alias.name}")
                require(root != "qlib", "forbidden D2U core Qlib import")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            require("external_data" not in module, "D2U core may not import network-capable external_data")
            require("qlib" not in module, f"forbidden D2U core Qlib import: {module}")
            require("supervised_label" not in module, f"forbidden D2U core label import: {module}")
            require("development_evaluation" not in module, f"forbidden D2U core evaluator import: {module}")
            require("final_holdout" not in module, f"forbidden D2U core holdout import: {module}")
            require(not any(fragment in module for fragment in ("broker", "oms", "safety", "order_intent")), f"forbidden D2U core authority import: {module}")
        elif isinstance(node, ast.Call):
            called = dotted_name(node.func)
            require(
                called not in {
                    "urlopen", "urllib.request.urlopen", "requests.get", "requests.request",
                    "httpx.get", "httpx.request", "socket.socket", "subprocess.run",
                    "subprocess.Popen", "os.system", "os.popen",
                },
                f"forbidden D2U core network/process call: {called}",
            )

    core_forbidden = (
        "model.fit",
        "model.predict",
        "run_isolated_qlib_family_candidate",
        "SupervisedLabelArtifact",
        "evaluate_development_predictions",
        "profit_factor",
        "max_drawdown",
        "realized_pnl",
        "execution_authorized=True",
        "paper_execution_authorized=True",
        'capital_authority="PAPER"',
        'capital_authority="LIVE"',
        'live_trading="ENABLED"',
    )
    for marker in core_forbidden:
        require(marker not in core, f"forbidden D2U core capability/state: {marker}")

    plan_phase = core[core.index("class HistoricalCollectionPlan"):core.index("class SQLiteHistoricalCollectionPlanRegistry")]
    require('self.granularity != "monthly"' in plan_phase, "D2U v1 must freeze monthly granularity")
    require("_months_are_contiguous" in plan_phase, "D2U must validate contiguous months")
    require("one exact descriptor per symbol and month" in plan_phase, "D2U must require full symbol-month grid")
    require("development_end != last_period_end" in plan_phase, "D2U must end collection exactly at DEVELOPMENT end")
    require("self.warmup_bars * timeframe" in plan_phase, "D2U must bind exact warmup/train geometry")

    registry_phase = core[core.index("class SQLiteHistoricalCollectionPlanRegistry"):core.index("class AcquiredSnapshotBinding")]
    require("BEFORE UPDATE" in registry_phase and "BEFORE DELETE" in registry_phase, "D2U plan registry must be append-only")
    require("require_exact" in registry_phase, "D2U registry must expose durable exact gate")

    assemble_phase = function_source(core, "assemble_historical_collection", "canonical_oss3d2u_collection_plan")
    require("set(by_descriptor) != planned" in assemble_phase, "D2U assembly must require exact planned descriptor family")
    require("dataset.started_at != previous_end" in assemble_phase, "D2U assembly must reject cross-file gap/overlap")
    require("AlignedMarketUniverse.from_datasets" in assemble_phase, "D2U assembly must require cross-asset exact support")
    require("development_index - plan.warmup_bars" in assemble_phase, "D2U must derive DEVELOPMENT warmup only from prior TRAIN bars")

    canonical_phase = function_source(core, "canonical_oss3d2u_collection_plan", "_slice_universe")
    require("CANONICAL_FIRST_MONTH" in canonical_phase and "CANONICAL_LAST_MONTH" in canonical_phase, "D2U canonical plan must freeze month range")
    require("final_holdout_descriptors_included=False" in canonical_phase, "D2U canonical plan must exclude holdout descriptors")

    # Acquisition lab is the sole network surface and may use only the existing
    # public GET-only transport abstraction. It must not import broker/trading or
    # model/evaluator layers. Every explicit ReadOnlyRequest must hard-code GET.
    allowed_external_names = {
        "HttpResponse",
        "PublicDataPolicy",
        "ReadOnlyHttpTransport",
        "ReadOnlyRequest",
        "UrllibReadOnlyTransport",
    }
    request_call_count = 0
    for node in ast.walk(acquisition_tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            lowered = module.lower()
            if module == "autotrade.research.external_data":
                require({alias.name for alias in node.names} == allowed_external_names, "D2U acquisition external_data imports drifted")
            require("qlib" not in lowered, f"forbidden D2U acquisition Qlib import: {module}")
            require("supervised_label" not in lowered, f"forbidden D2U acquisition label import: {module}")
            require("final_holdout" not in lowered, f"forbidden D2U acquisition holdout import: {module}")
            require(not any(fragment in lowered for fragment in ("broker", "oms", "safety", "order_intent")), f"forbidden D2U acquisition authority import: {module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                require(alias.name.split(".", 1)[0] not in {"requests", "httpx", "aiohttp", "socket", "subprocess"}, f"D2U acquisition bypasses canonical transport: {alias.name}")
        elif isinstance(node, ast.Call) and dotted_name(node.func) == "ReadOnlyRequest":
            request_call_count += 1
            method_keywords = [keyword for keyword in node.keywords if keyword.arg == "method"]
            require(len(method_keywords) == 1, "D2U ReadOnlyRequest must specify exactly one method keyword")
            method_node = method_keywords[0].value
            require(isinstance(method_node, ast.Constant) and method_node.value == "GET", "D2U acquisition ReadOnlyRequest method must be literal GET")
    require(request_call_count == 2, "D2U acquisition must define exactly the checksum and archive GET requests")

    for marker in (
        "CHECKSUM_A_THEN_ZIP_THEN_IDENTICAL_CHECKSUM_B_V1",
        "EXACT_PREREGISTERED_PATH_GET_ONLY_NO_REDIRECT_NO_RETRY_V1",
        'PROVIDER_HOST = "data.binance.vision"',
        "registry.require_exact(plan)",
        "response.final_url != request.url",
        "checksum_a.body != checksum_b.body",
        "request_count=3",
        "retries_performed=0",
        "network_used=True",
        "provider_credentials_used=False",
        "trading_endpoints_used=False",
        "final_holdout_values_requested=False",
        "execution_authorized=False",
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
    ):
        require(marker in acquisition, f"missing D2U acquisition boundary marker: {marker}")

    acquire_phase = function_source(acquisition, "acquire_preregistered_archive", "build_real_archive_transport")
    prereg_index = acquire_phase.index("registry.require_exact(plan)")
    membership_index = acquire_phase.index("descriptor.fingerprint not in set(plan.descriptor_fingerprints)")
    checksum_a_index = acquire_phase.index("checksum_a = _send_exact")
    archive_index = acquire_phase.index("archive = _send_exact")
    checksum_b_index = acquire_phase.index("checksum_b = _send_exact")
    normalize_index = acquire_phase.index("build_binance_spot_archive_snapshot")
    require(prereg_index < membership_index < checksum_a_index < archive_index < checksum_b_index < normalize_index, "D2U acquisition ordering drifted")
    require(acquire_phase.count("_send_exact(") == 3, "D2U acquisition attempt must contain exactly three network sends")
    require("for " not in acquire_phase and "while " not in acquire_phase, "D2U acquisition attempt may not contain retry loops")

    real_transport_phase = function_source(acquisition, "build_real_archive_transport", "_send_exact")
    require("descriptor.relative_archive_path" in real_transport_phase, "D2U transport allowlist must derive from frozen descriptors")
    require("descriptor.relative_checksum_path" in real_transport_phase, "D2U transport must allowlist frozen checksum paths")
    require("PublicDataPolicy" in real_transport_phase and "UrllibReadOnlyTransport" in real_transport_phase, "D2U real transport must reuse canonical GET-only policy")

    forbidden_acquisition_text = (
        "api-key",
        "x-mbx-apikey",
        "/api/v3/order",
        "/api/v3/account",
        "model.fit",
        "model.predict",
        "SupervisedLabelArtifact",
        "execution_authorized=True",
        "paper_execution_authorized=True",
        'capital_authority="PAPER"',
        'live_trading="ENABLED"',
    )
    lowered_acquisition = acquisition.lower()
    for marker in forbidden_acquisition_text:
        require(marker.lower() not in lowered_acquisition, f"forbidden D2U acquisition capability/text: {marker}")

    print(
        "AUTO-TRADE OSS-3D2U HISTORICAL COLLECTION/ACQUISITION BOUNDARY: PASS — "
        "finite monthly BTC/ETH/SOL 2023-2025 family is durably preregistered before any GET; acquisition is exactly "
        "CHECKSUM-A -> ZIP -> identical CHECKSUM-B with exact final URLs and zero retries; D2T snapshots stitch with "
        "no gaps/overlaps into WARMUP/TRAIN/DEVELOPMENT only; FINAL_HOLDOUT/Qlib/labels/broker/OMS/Safety/PAPER/capital/LIVE denied"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryFailure as exc:
        print(f"AUTO-TRADE OSS-3D2U HISTORICAL COLLECTION/ACQUISITION BOUNDARY: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
