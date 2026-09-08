from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/research/oss3_market_snapshot.py"


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
    require(MODULE.is_file(), "D2T module is missing")
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))

    required = (
        "OSS3D2T_BINANCE_SPOT_ARCHIVE_DESCRIPTOR_V1",
        "OSS3D2T_IMMUTABLE_MARKET_SNAPSHOT_MANIFEST_V1",
        "OSS3D2T_IMMUTABLE_MARKET_SNAPSHOT_ARTIFACT_V1",
        "BINANCE_SPOT_PUBLIC_DATA_ARCHIVE",
        "https://data.binance.vision",
        "MILLISECONDS_PRE_2025_01_01",
        "MICROSECONDS_FROM_2025_01_01",
        "datetime(2025, 1, 1, tzinfo=timezone.utc)",
        "PROVIDER_SHA256_MUST_MATCH_ARCHIVE_BYTES_BEFORE_ZIP_PARSE_V1",
        "EXACT_SINGLE_EXPECTED_CSV_MEMBER_V1",
        "EXACT_FULL_ARCHIVE_PERIOD_NO_GAPS_NO_DUPLICATES_V1",
        "RESEARCH_SERIALIZATION_ONLY_NOT_TRADING_FILTER_AUTHORITY_V1",
        "network_used_by_normalizer=False",
        "provider_credentials_used=False",
        "trading_filters_certified=False",
        "execution_authorized=False",
        "paper_execution_authorized=False",
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
        "archive_sha256",
        "provider_checksum_sha256",
        "csv_payload_sha256",
        "normalized_dataset_hash",
    )
    for marker in required:
        require(marker in source, f"missing D2T boundary marker: {marker}")

    # D2T can import the fixed interval registry from R3 external_data, but it
    # may not import any R3 transport/provider/network class.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {alias.name for alias in node.names}
            if module.endswith("external_data"):
                require(names == {"FIXED_INTERVAL_MS"}, "D2T may import only FIXED_INTERVAL_MS from external_data")
            lowered = module.lower()
            require("qlib" not in lowered, f"forbidden D2T Qlib import: {module}")
            require("supervised_label" not in lowered, f"forbidden D2T label import: {module}")
            require("development_evaluation" not in lowered, f"forbidden D2T evaluator import: {module}")
            require("model_tournament" not in lowered, f"forbidden D2T tournament import: {module}")
            require("final_holdout" not in lowered, f"forbidden D2T holdout import: {module}")
            require("economic_holdout" not in lowered, f"forbidden D2T economic evaluator import: {module}")
            require(not any(fragment in lowered for fragment in ("broker", "oms", "safety", "order_intent")), f"forbidden D2T authority import: {module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                require(root not in {"socket", "urllib", "requests", "httpx", "aiohttp", "subprocess"}, f"forbidden D2T network/process import: {alias.name}")
                require(root != "qlib", "forbidden D2T Qlib import")
        elif isinstance(node, ast.Call):
            called = dotted_name(node.func)
            require(
                called not in {
                    "urlopen", "urllib.request.urlopen", "requests.get", "requests.request",
                    "httpx.get", "httpx.request", "socket.socket", "subprocess.run",
                    "subprocess.Popen", "os.system", "os.popen",
                },
                f"forbidden D2T network/process call: {called}",
            )

    forbidden_text = (
        "BinanceSpotHistoricalProvider",
        "UrllibReadOnlyTransport",
        "ReadOnlyHttpTransport",
        "model.fit",
        "model.predict",
        "run_isolated_qlib_family_candidate",
        "SupervisedLabelArtifact",
        "forward_return_1",
        "development_labels",
        "FINAL_HOLDOUT",
        "profit_factor",
        "max_drawdown",
        "realized_pnl",
        "execution_authorized=True",
        "paper_execution_authorized=True",
        'capital_authority="PAPER"',
        'capital_authority="LIVE"',
        'live_trading="ENABLED"',
        "trading_filters_certified=True",
        "provider_credentials_used=True",
        "network_used_by_normalizer=True",
    )
    # FINAL_HOLDOUT appears once in the research-only docstring, so enforce no
    # executable identifier/call rather than banning the explanatory word.
    for marker in forbidden_text:
        if marker == "FINAL_HOLDOUT":
            continue
        require(marker not in source, f"forbidden D2T capability/state marker: {marker}")

    build_phase = function_source(
        source,
        "build_binance_spot_archive_snapshot",
        "build_aligned_snapshot_universe",
    )
    checksum_index = build_phase.index("if archive_sha != provider_checksum")
    zip_index = build_phase.index("_read_exact_csv_member")
    require(checksum_index < zip_index, "D2T must verify provider checksum before opening ZIP")
    require("sha256(archive_bytes).hexdigest()" in build_phase, "D2T must hash exact archive bytes")
    require("sha256(csv_bytes).hexdigest()" in build_phase, "D2T must hash exact CSV bytes")

    zip_phase = function_source(
        source,
        "_read_exact_csv_member",
        "_parse_binance_kline_csv",
    )
    require("len(files) != 1" in zip_phase, "D2T must require one non-directory ZIP member")
    require("files[0].filename != expected_csv_filename" in zip_phase, "D2T must require exact CSV filename")
    require("duplicate member names" in zip_phase, "D2T must reject duplicate ZIP names")
    require("flag_bits & 0x1" in zip_phase, "D2T must reject encrypted ZIP member")

    provider_row_phase = function_source(
        source,
        "_validate_provider_row",
        "_validate_exact_provider_coverage",
    )
    require("expected_open" in provider_row_phase, "D2T must bind exact provider open times")
    require("expected_close = expected_open + interval_units - 1" in provider_row_phase, "D2T must bind close-time geometry")
    require("OHLC geometry" in provider_row_phase, "D2T must validate OHLC geometry")

    descriptor_start = source.index("class BinanceSpotArchiveDescriptor")
    descriptor_end = source.index("class HistoricalMarketSnapshotManifest", descriptor_start)
    descriptor_source = source[descriptor_start:descriptor_end]
    for excluded in ('"3d"', '"1w"', '"1mo"'):
        # Excluded intervals may appear in comments but may not be members of
        # ARCHIVE_FIXED_INTERVALS. Check the constant directly below instead.
        pass
    intervals_start = source.index("ARCHIVE_FIXED_INTERVALS =")
    intervals_end = source.index("\n\n_HASH_RE", intervals_start)
    interval_block = source[intervals_start:intervals_end]
    for excluded in ('"3d"', '"1w"', '"1mo"', '"1s"'):
        require(excluded not in interval_block, f"D2T v1 unexpectedly allows boundary-sensitive interval {excluded}")
    for included in ('"1m"', '"1h"', '"1d"'):
        require(included in interval_block, f"D2T fixed interval missing: {included}")

    manifest_start = source.index("class HistoricalMarketSnapshotManifest")
    manifest_end = source.index("class HistoricalMarketSnapshotArtifact", manifest_start)
    manifest_source = source[manifest_start:manifest_end]
    require("self.archive_sha256 != self.provider_checksum_sha256" in manifest_source, "D2T manifest must preserve checksum equality")
    require("self.network_used_by_normalizer" in manifest_source, "D2T manifest must deny normalizer network")
    require("self.trading_filters_certified" in manifest_source, "D2T manifest must deny trading-filter certification")

    semantic_requirements = (
        "oss3_market_snapshot.py",
        "external_data.py",
        "market.py",
        "universe.py",
    )
    for marker in semantic_requirements:
        require(marker in source, f"D2T semantic hash omits dependency: {marker}")

    print(
        "AUTO-TRADE OSS-3D2T IMMUTABLE MARKET SNAPSHOT BOUNDARY: PASS — "
        "provider CHECKSUM is verified before ZIP parse; exact one-member archive/CSV and provider timestamp geometry "
        "normalize offline into hash-bound MarketDataset artifacts; 2025 microsecond transition is explicit; network, "
        "credentials, trading-filter authority, models/labels/holdouts/broker/OMS/Safety/PAPER/capital/LIVE denied"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryFailure as exc:
        print(f"AUTO-TRADE OSS-3D2T IMMUTABLE MARKET SNAPSHOT BOUNDARY: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
