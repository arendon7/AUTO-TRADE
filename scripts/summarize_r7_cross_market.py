from __future__ import annotations

import argparse
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from statistics import median
from typing import Any


class CrossMarketError(RuntimeError):
    pass


_FAMILY = {
    "tsmom": "time_series_momentum",
    "donchian": "donchian_breakout",
    "meanrev": "mean_reversion_zscore",
    "volmom": "volatility_managed_momentum",
    "b-dma": "dual_moving_average_trend",
    "b-atr": "atr_impulse_breakout",
    "b-pullback": "trend_pullback",
}
_SIZE_PARAMETERS = {"order_quantity", "base_quantity"}
_EXPECTED_CAMPAIGNS = 6


def _hash(payload: object) -> str:
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _load(path: Path, *, experiment: str) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrossMarketError(f"cannot read report {path}: {exc}") from exc
    if not isinstance(report, dict):
        raise CrossMarketError(f"report root must be object: {path}")
    required = {
        "campaign_id",
        "symbol",
        "interval",
        "candidate_count",
        "candidates",
        "robustness_eligible_trial_ids",
        "protected_holdout",
    }
    missing = required - set(report)
    if missing:
        raise CrossMarketError(f"report missing {sorted(missing)}: {path}")
    holdout = report["protected_holdout"]
    if not isinstance(holdout, dict) or holdout.get("checked_out") is not False:
        raise CrossMarketError(f"HOLDOUT proof invalid: {path}")
    if experiment == "B":
        if report.get("experiment") != "B" or report.get("program_id") != "r7-experiment-b":
            raise CrossMarketError(f"invalid Experiment B identity: {path}")
    return report


def _family(strategy_id: str) -> str:
    for prefix in ("b-pullback", "b-atr", "b-dma", "donchian", "meanrev", "volmom", "tsmom"):
        if strategy_id.startswith(prefix + "-"):
            return _FAMILY[prefix]
    raise CrossMarketError(f"unknown strategy family: {strategy_id}")


def _normalized_parameters(parameters: object) -> dict[str, object]:
    if not isinstance(parameters, dict):
        raise CrossMarketError("candidate parameters must be object")
    return {
        str(name): value
        for name, value in sorted(parameters.items())
        if name not in _SIZE_PARAMETERS
    }


def _finite_number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CrossMarketError(f"{name} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise CrossMarketError(f"{name} must be finite")
    return result


def _experiment_reports(root: Path, experiment: str) -> list[dict[str, Any]]:
    reports = [_load(path, experiment=experiment) for path in sorted(root.rglob("*-report.json"))]
    if len(reports) != _EXPECTED_CAMPAIGNS:
        raise CrossMarketError(
            f"Experiment {experiment} requires exactly {_EXPECTED_CAMPAIGNS} reports; got {len(reports)}"
        )
    keys = {(item["symbol"], item["interval"]) for item in reports}
    if len(keys) != _EXPECTED_CAMPAIGNS:
        raise CrossMarketError(f"Experiment {experiment} market keys are not unique")
    expected = {
        (symbol, interval)
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
        for interval in ("1h", "4h")
    }
    if keys != expected:
        raise CrossMarketError(
            f"Experiment {experiment} market matrix differs from frozen 3x2 universe"
        )
    return reports


def _summarize_experiment(
    reports: list[dict[str, Any]], *, experiment: str
) -> dict[str, Any]:
    by_hypothesis: dict[str, dict[str, Any]] = {}
    candidate_counts = {int(report["candidate_count"]) for report in reports}
    if len(candidate_counts) != 1:
        raise CrossMarketError(f"Experiment {experiment} candidate counts differ by campaign")
    expected_candidates = candidate_counts.pop()

    for report in reports:
        robust = set(report["robustness_eligible_trial_ids"])
        market = f"{report['symbol']}:{report['interval']}"
        for candidate in report["candidates"]:
            if not isinstance(candidate, dict):
                raise CrossMarketError("candidate entry must be object")
            strategy_id = candidate.get("strategy_id")
            trial_id = candidate.get("trial_id")
            metrics = candidate.get("metrics")
            if not isinstance(strategy_id, str) or not isinstance(trial_id, str):
                raise CrossMarketError("candidate identity is invalid")
            if not isinstance(metrics, dict):
                raise CrossMarketError("candidate metrics must be object")
            family = _family(strategy_id)
            normalized = _normalized_parameters(candidate.get("parameters"))
            identity_payload = {
                "experiment": experiment,
                "family": family,
                "parameters_without_size": normalized,
            }
            hypothesis_id = _hash(identity_payload)[:20]
            item = by_hypothesis.setdefault(
                hypothesis_id,
                {
                    "hypothesis_id": hypothesis_id,
                    "family": family,
                    "parameters_without_size": normalized,
                    "observations": [],
                },
            )
            if item["family"] != family or item["parameters_without_size"] != normalized:
                raise CrossMarketError("hypothesis hash collision")
            item["observations"].append(
                {
                    "market": market,
                    "symbol": report["symbol"],
                    "interval": report["interval"],
                    "trial_id": trial_id,
                    "strategy_id": strategy_id,
                    "eligible": bool(candidate.get("eligible", False)),
                    "robust": trial_id in robust,
                    "net_return": _finite_number(metrics.get("net_return"), name="net_return"),
                    "sharpe": _finite_number(metrics.get("sharpe"), name="sharpe"),
                    "max_drawdown": _finite_number(
                        metrics.get("max_drawdown"), name="max_drawdown"
                    ),
                    "fills": int(metrics.get("fills", 0)),
                }
            )

    if len(by_hypothesis) != expected_candidates:
        raise CrossMarketError(
            f"Experiment {experiment} normalized hypothesis count {len(by_hypothesis)} "
            f"does not equal campaign candidate count {expected_candidates}"
        )

    summaries: list[dict[str, Any]] = []
    for hypothesis_id, item in sorted(by_hypothesis.items()):
        observations = sorted(item["observations"], key=lambda x: x["market"])
        if len(observations) != _EXPECTED_CAMPAIGNS:
            raise CrossMarketError(
                f"hypothesis {hypothesis_id} lacks full six-market coverage"
            )
        markets = [obs["market"] for obs in observations]
        if len(markets) != len(set(markets)):
            raise CrossMarketError(f"hypothesis {hypothesis_id} duplicates a market")
        returns = [obs["net_return"] for obs in observations]
        sharpes = [obs["sharpe"] for obs in observations]
        drawdowns = [obs["max_drawdown"] for obs in observations]
        summaries.append(
            {
                "hypothesis_id": hypothesis_id,
                "family": item["family"],
                "parameters_without_size": item["parameters_without_size"],
                "positive_return_ratio": sum(value > 0 for value in returns) / 6,
                "positive_sharpe_ratio": sum(value > 0 for value in sharpes) / 6,
                "eligible_ratio": sum(obs["eligible"] for obs in observations) / 6,
                "robust_ratio": sum(obs["robust"] for obs in observations) / 6,
                "median_net_return": median(returns),
                "worst_net_return": min(returns),
                "best_net_return": max(returns),
                "median_sharpe": median(sharpes),
                "worst_sharpe": min(sharpes),
                "best_sharpe": max(sharpes),
                "max_drawdown_across_markets": max(drawdowns),
                "total_fills": sum(obs["fills"] for obs in observations),
                "observations": observations,
            }
        )

    summaries.sort(
        key=lambda item: (
            -item["robust_ratio"],
            -item["eligible_ratio"],
            -item["positive_return_ratio"],
            -item["median_sharpe"],
            -item["worst_net_return"],
            item["hypothesis_id"],
        )
    )
    family_rollup: list[dict[str, Any]] = []
    for family in sorted({item["family"] for item in summaries}):
        members = [item for item in summaries if item["family"] == family]
        family_rollup.append(
            {
                "family": family,
                "hypothesis_count": len(members),
                "best_robust_ratio": max(item["robust_ratio"] for item in members),
                "best_eligible_ratio": max(item["eligible_ratio"] for item in members),
                "best_positive_return_ratio": max(
                    item["positive_return_ratio"] for item in members
                ),
                "best_median_sharpe": max(item["median_sharpe"] for item in members),
                "median_of_hypothesis_median_sharpes": median(
                    item["median_sharpe"] for item in members
                ),
            }
        )

    return {
        "experiment": experiment,
        "campaign_count": len(reports),
        "candidate_count_per_campaign": expected_candidates,
        "normalized_hypothesis_count": len(summaries),
        "top_hypotheses": summaries[:10],
        "hypotheses": summaries,
        "family_rollup": family_rollup,
    }


def summarize(*, experiment_a: Path, experiment_b: Path) -> dict[str, Any]:
    a_reports = _experiment_reports(experiment_a, "A")
    b_reports = _experiment_reports(experiment_b, "B")
    return {
        "summary_version": 1,
        "scope": "DEVELOPMENT_ONLY_DIAGNOSTIC",
        "diagnostic_only": True,
        "promotion_authority": False,
        "normalization": {
            "removed_size_parameters": sorted(_SIZE_PARAMETERS),
            "market_matrix": [
                "BTCUSDT:1h",
                "BTCUSDT:4h",
                "ETHUSDT:1h",
                "ETHUSDT:4h",
                "SOLUSDT:1h",
                "SOLUSDT:4h",
            ],
        },
        "experiment_a": _summarize_experiment(a_reports, experiment="A"),
        "experiment_b": _summarize_experiment(b_reports, experiment="B"),
        "all_holdouts_untouched": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare frozen R7 hypotheses across BTC/ETH/SOL and 1h/4h. "
            "Diagnostic only; cannot authorize HOLDOUT/PAPER/LIVE."
        )
    )
    parser.add_argument("--experiment-a", required=True, type=Path)
    parser.add_argument("--experiment-b", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = summarize(experiment_a=args.experiment_a, experiment_b=args.experiment_b)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
