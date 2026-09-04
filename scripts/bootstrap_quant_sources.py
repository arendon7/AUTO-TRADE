from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Iterable


@dataclass(frozen=True, slots=True)
class QuantSource:
    name: str
    repository: str
    commit: str
    license_id: str
    integration_mode: str
    clone_by_default: bool
    purpose: str


SOURCES: tuple[QuantSource, ...] = (
    QuantSource(
        name="lean",
        repository="https://github.com/QuantConnect/Lean.git",
        commit="cfc7e8ac451e384b08b697465e33016ab26c1263",
        license_id="Apache-2.0",
        integration_mode="external_validation_engine",
        clone_by_default=True,
        purpose="Independent backtest/live-engine validation and brokerage architecture reference.",
    ),
    QuantSource(
        name="qlib",
        repository="https://github.com/microsoft/qlib.git",
        commit="79633dd9506ea689e5400dea0197717b5b3d74b7",
        license_id="MIT",
        integration_mode="isolated_research_ml",
        clone_by_default=True,
        purpose="ML research workflow, model experimentation and portfolio research reference.",
    ),
    QuantSource(
        name="hummingbot",
        repository="https://github.com/hummingbot/hummingbot.git",
        commit="2bfaccc48dd49e71a5b6d9b3011808e127dd00cd",
        license_id="Apache-2.0",
        integration_mode="connector_execution_reference",
        clone_by_default=True,
        purpose="Crypto connector, market-making and execution lifecycle architecture reference.",
    ),
    QuantSource(
        name="ccxt",
        repository="https://github.com/ccxt/ccxt.git",
        commit="420f367bcfbbe8a125b006b0025dce43301cc0dc",
        license_id="MIT",
        integration_mode="crypto_market_data_connector_reference",
        clone_by_default=True,
        purpose="Broad crypto exchange API normalization for research/data connector design.",
    ),
    QuantSource(
        name="zipline-reloaded",
        repository="https://github.com/stefan-jansen/zipline-reloaded.git",
        commit="943010b9da848e317fc520de87edade2b884d329",
        license_id="Apache-2.0",
        integration_mode="independent_backtest_reference",
        clone_by_default=True,
        purpose="Independent event-driven backtesting reference and regression comparison.",
    ),
    QuantSource(
        name="gs-quant",
        repository="https://github.com/goldmansachs/gs-quant.git",
        commit="ccbd4ae780f51be4e01ecbf834c7b93583fec57f",
        license_id="Apache-2.0",
        integration_mode="analytics_reference",
        clone_by_default=True,
        purpose="Risk, statistics and quantitative analytics reference.",
    ),
    QuantSource(
        name="pyportfolioopt",
        repository="https://github.com/PyPortfolio/PyPortfolioOpt.git",
        commit="a6638d2e06dae6f444fd022cfd4b3c528902a85b",
        license_id="MIT",
        integration_mode="portfolio_allocation_reference",
        clone_by_default=True,
        purpose="Portfolio optimization, covariance estimation and risk-aware allocation reference.",
    ),
    QuantSource(
        name="numerapi",
        repository="https://github.com/numerai/numerapi.git",
        commit="ab54eef18f54d0244199cb8bffd4da647621191f",
        license_id="MIT",
        integration_mode="optional_external_signal_source",
        clone_by_default=True,
        purpose="Optional Numerai data/model integration; never an execution engine.",
    ),
)

REFERENCE_ONLY: tuple[tuple[str, str, str, str], ...] = (
    (
        "freqtrade",
        "https://github.com/freqtrade/freqtrade",
        "GPL-3.0",
        "Architecture/strategy ideas only; do not vendor source into AUTO-TRADE core.",
    ),
    (
        "vectorbt",
        "https://github.com/polakowo/vectorbt",
        "Apache-2.0 + Commons-Clause",
        "Reference only for commercial core; Commons Clause restricts selling derived functionality.",
    ),
    (
        "stocksharp",
        "https://github.com/StockSharp/StockSharp",
        "StockSharp-Custom/EULA",
        "Metadata/reference only; repository is not general-purpose open source.",
    ),
    (
        "awesome-quant",
        "https://github.com/wilsonfreitas/awesome-quant",
        "curated-index",
        "Discovery index only; every downstream project requires an independent license review.",
    ),
)


def _run(args: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def _origin(target: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(target), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _checkout_source(source: QuantSource, destination: Path) -> None:
    target = destination / source.name
    if not target.exists():
        _run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                source.repository,
                str(target),
            ]
        )
    elif not (target / ".git").exists():
        raise RuntimeError(f"destination exists but is not a git repository: {target}")

    if _origin(target) != source.repository:
        raise RuntimeError(f"unexpected origin for {source.name}: {_origin(target)}")

    _run(["git", "-C", str(target), "fetch", "--depth", "1", "origin", source.commit])
    _run(["git", "-C", str(target), "checkout", "--detach", source.commit])
    result = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    actual = result.stdout.strip()
    if actual != source.commit:
        raise RuntimeError(
            f"source pin mismatch for {source.name}: expected {source.commit}, got {actual}"
        )


def _selected_sources(names: Iterable[str] | None) -> tuple[QuantSource, ...]:
    if not names:
        return tuple(source for source in SOURCES if source.clone_by_default)
    requested = set(names)
    known = {source.name for source in SOURCES}
    unknown = requested - known
    if unknown:
        raise ValueError(f"unknown source names: {sorted(unknown)}")
    return tuple(source for source in SOURCES if source.name in requested)


def _print_catalog() -> None:
    print("Permissive/pinned sources:")
    for source in SOURCES:
        print(
            f"- {source.name}: {source.license_id} @ {source.commit[:12]} "
            f"[{source.integration_mode}]"
        )
    print("\nReference-only/restricted sources:")
    for name, repository, license_id, note in REFERENCE_ONLY:
        print(f"- {name}: {license_id} | {repository} | {note}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clone audited quant research sources at immutable commits."
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(".external/quant"),
        help="Local non-versioned source directory.",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Clone only this named permissive source. Repeatable.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the audited source catalog without cloning.",
    )
    args = parser.parse_args()

    if args.list:
        _print_catalog()
        return 0

    selected = _selected_sources(args.sources)
    args.destination.mkdir(parents=True, exist_ok=True)
    for source in selected:
        print(
            f"[quant-source] {source.name} {source.license_id} "
            f"{source.commit[:12]} -> {args.destination / source.name}"
        )
        _checkout_source(source, args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
