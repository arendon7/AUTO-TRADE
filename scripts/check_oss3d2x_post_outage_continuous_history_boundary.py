from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autotrade.research.oss3_market_collection import (  # noqa: E402
    CANONICAL_FIRST_MONTH,
    CANONICAL_INTERVAL,
    CANONICAL_LAST_MONTH,
    CANONICAL_SYMBOLS,
    canonical_oss3d2u_collection_plan,
)


BASE_D2W_CERTIFIED_HEAD = "7d522e422137b87432e65d5b1ab7756a982483d5"
EXPECTED_PLAN_VERSION = "OSS3D2U_HISTORICAL_COLLECTION_PLAN_V2"
EXPECTED_COLLECTION_ID = "oss3d2u-binance-spot-btc-eth-sol-1h-2023apr-2025-v2"
EXPECTED_PLAN_FINGERPRINT = "f43abdcd532f07d37b54f93abafbd3b29a3c3ee07afeedbae95b7aa15153c59d"
EXPECTED_PERIOD_COUNT = 33
EXPECTED_DESCRIPTOR_COUNT = 99
EXPECTED_TRAIN_START = "2023-04-01T20:00:00+00:00"
EXPECTED_DEVELOPMENT_START = "2025-01-01T00:00:00+00:00"
EXPECTED_DEVELOPMENT_END = "2026-01-01T00:00:00+00:00"
EXCLUDED_OUTAGE_MONTH = "2023-03"
OUTAGE_ARCHIVE_SHA256 = {
    "BTCUSDT": "7f2afb8e0179a57ac31eab5205660298ba5eb77039ac2e21aef9b715ff3d06ce",
    "ETHUSDT": "90d268be1e0d39f7411f88ca3c0f21fe110fee7ff3c1dd054555e1fea6e22d8d",
    "SOLUSDT": "7345fc8807e3e73e11595b5b371bf96d414048f77d1498964fa5c5f8fe3f02e7",
}

CORE = ROOT / "src/autotrade/research/oss3_market_collection.py"
D2T = ROOT / "src/autotrade/research/oss3_market_snapshot.py"
DOC = ROOT / "knowledge/20_RESEARCH/OSS3D2X_POST_OUTAGE_CONTINUOUS_HISTORY.md"


class BoundaryFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BoundaryFailure(message)


def main() -> int:
    require(CORE.is_file(), "D2X requires the D2U market-collection contract")
    require(D2T.is_file(), "D2X requires the certified D2T immutable snapshot implementation")
    require(DOC.is_file(), "D2X post-outage evidence contract is missing")

    plan = canonical_oss3d2u_collection_plan()

    require(plan.plan_version == EXPECTED_PLAN_VERSION, "D2X plan version drifted")
    require(plan.collection_id == EXPECTED_COLLECTION_ID, "D2X collection id drifted")
    require(plan.fingerprint == EXPECTED_PLAN_FINGERPRINT, "D2X plan fingerprint drifted")
    require(CANONICAL_FIRST_MONTH == "2023-04", "D2X first canonical month must remain 2023-04")
    require(CANONICAL_LAST_MONTH == "2025-12", "D2X last canonical month must remain 2025-12")
    require(CANONICAL_INTERVAL == "1h", "D2X interval must remain 1h")
    require(tuple(CANONICAL_SYMBOLS) == ("BTCUSDT", "ETHUSDT", "SOLUSDT"), "D2X symbol universe drifted")

    require(len(plan.periods) == EXPECTED_PERIOD_COUNT, "D2X must contain exactly 33 monthly periods")
    require(len(plan.descriptors) == EXPECTED_DESCRIPTOR_COUNT, "D2X must contain exactly 99 descriptors")
    require(plan.periods[0] == "2023-04" and plan.periods[-1] == "2025-12", "D2X month endpoints drifted")
    require(EXCLUDED_OUTAGE_MONTH not in plan.periods, "March 2023 outage month must remain excluded")
    require(all(descriptor.period != EXCLUDED_OUTAGE_MONTH for descriptor in plan.descriptors), "outage descriptor re-entered canonical family")

    expected_pairs = {(period, symbol) for period in plan.periods for symbol in CANONICAL_SYMBOLS}
    actual_pairs = {(descriptor.period, descriptor.instrument.symbol) for descriptor in plan.descriptors}
    require(actual_pairs == expected_pairs, "D2X must contain exactly one descriptor for every canonical month/symbol pair")

    require(plan.train_start == EXPECTED_TRAIN_START, "D2X TRAIN start drifted")
    require(plan.development_start == EXPECTED_DEVELOPMENT_START, "D2X DEVELOPMENT start drifted")
    require(plan.development_end == EXPECTED_DEVELOPMENT_END, "D2X DEVELOPMENT end drifted")
    require(plan.warmup_bars == 20, "D2X must preserve exactly 20 warmup bars")

    for flag in (
        "final_holdout_descriptors_included",
        "label_values_included",
        "prediction_values_included",
        "execution_authorized",
        "paper_execution_authorized",
    ):
        require(getattr(plan, flag) is False, f"D2X authority/data flag must remain false: {flag}")
    require(plan.capital_authority == "NONE", "D2X capital authority must remain NONE")
    require(plan.live_trading == "BLOCKED", "D2X LIVE trading must remain BLOCKED")

    core = CORE.read_text(encoding="utf-8")
    d2t = D2T.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")

    for marker in (
        "OSS3D2U_HISTORICAL_COLLECTION_PLAN_V2",
        "OSS3D2U_ACQUIRED_SNAPSHOT_BINDING_V2",
        "OSS3D2U_HISTORICAL_COLLECTION_ASSEMBLY_EVIDENCE_V2",
        "OSS3D2U_HISTORICAL_RESEARCH_PARTITION_MATERIAL_V2",
        "FINITE_MONTHLY_ARCHIVE_FAMILY_POST_20230324_OUTAGE_PREREGISTERED_BEFORE_NETWORK_V2",
        "EXACT_MONTH_TO_MONTH_NO_GAP_NO_OVERLAP_NO_FILL_V2",
        "POST_OUTAGE_WARMUP20_THEN_TRAIN_THEN_DEVELOPMENT_NO_HOLDOUT_VALUES_V2",
    ):
        require(marker in core, f"missing D2X/D2U V2 marker: {marker}")

    # D2X deliberately has no gap-repair surface. A provider outage is excluded by
    # re-preregistering the historical root, never by manufacturing market data.
    forbidden_fill_tokens = (
        ".fillna(",
        ".ffill(",
        ".bfill(",
        ".interpolate(",
        "forward_fill",
        "backward_fill",
        "synthetic_bar",
        "impute_missing",
    )
    combined = (core + "\n" + d2t).lower()
    for token in forbidden_fill_tokens:
        require(token.lower() not in combined, f"D2X forbids market-data fill/interpolation surface: {token}")

    require("2023-03" not in core, "D2U production contract must not special-case the excluded outage month")
    require(importlib.util.find_spec("qlib") is None, "D2X boundary must remain Qlib-free")

    for symbol, archive_sha in OUTAGE_ARCHIVE_SHA256.items():
        require(symbol in doc and archive_sha in doc, f"D2X evidence contract missing {symbol} outage archive SHA-256")
    for marker in (
        "105 passed D2T exactly",
        "There is no 13:00 UTC bar",
        "D2T remains unchanged and strict",
        EXPECTED_COLLECTION_ID,
        EXPECTED_PLAN_FINGERPRINT,
        "FINAL_HOLDOUT values: not loaded",
        "capital authority: `NONE`",
        "LIVE trading: `BLOCKED`",
    ):
        require(marker in doc, f"D2X evidence contract missing marker: {marker}")

    print(
        "AUTO-TRADE OSS-3D2X POST-OUTAGE CONTINUOUS-HISTORY BOUNDARY: PASS — "
        "Binance 2023-03 outage remains excluded by preregistered window V2; "
        "99 BTC/ETH/SOL monthly descriptors span 2023-04..2025-12; no fill/imputation; "
        "DEVELOPMENT 2025 unchanged; FINAL_HOLDOUT/Qlib/PAPER/capital/LIVE denied; "
        f"D2T byte identity is pinned by Dedicated CI against {BASE_D2W_CERTIFIED_HEAD}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
