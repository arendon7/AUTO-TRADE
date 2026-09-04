from __future__ import annotations

import argparse
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Any

from autotrade.research.cross_market_policy import (
    CrossMarketObservation,
    DEFAULT_EXPERIMENT_C_BREADTH_POLICY,
    evaluate_cross_market_breadth,
)


_EXPECTED_MARKETS = {
    (symbol, interval)
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    for interval in ("1h", "4h")
}
_SIZE_PARAMETERS = {"order_quantity", "base_quantity"}


class ExperimentCBreadthError(RuntimeError):
    pass


def _hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentCBreadthError(f"cannot read {path}: {exc}") from exc
    if not isinstance(report, dict):
        raise ExperimentCBreadthError(f"report root must be object: {path}")
    required = {
        "experiment",
        "program_id",
        "campaign_id",
        "symbol",
        "interval",
        "candidate_count",
        "policy_eligible_trial_ids",
        "robustness_eligible_trial_ids",
        "per_campaign_development_ready_trial_id",
        "candidates",
        "protected_holdout",
        "regime_stability_gate",
    }
    missing = required - set(report)
    if missing:
        raise ExperimentCBreadthError(f"report missing {sorted(missing)}: {path}")
    if report["experiment"] != "C" or report["program_id"] != "r7-experiment-c":
        raise ExperimentCBreadthError(f"invalid Experiment C identity: {path}")
    if int(report["candidate_count"]) != 12:
        raise ExperimentCBreadthError(f"Experiment C candidate count changed: {path}")
    holdout = report["protected_holdout"]
    if not isinstance(holdout, dict) or holdout.get("checked_out") is not False:
        raise ExperimentCBreadthError(f"HOLDOUT proof invalid: {path}")
    return report


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExperimentCBreadthError(f"{name} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise ExperimentCBreadthError(f"{name} must be finite")
    return result


def _normalized_parameters(parameters: object) -> dict[str, object]:
    if not isinstance(parameters, dict):
        raise ExperimentCBreadthError("parameters must be object")
    return {
        str(name): value
        for name, value in sorted(parameters.items())
        if name not in _SIZE_PARAMETERS
    }


def _hypothesis_identity(candidate: dict[str, Any]) -> tuple[str, dict[str, object], str]:
    kind = candidate.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise ExperimentCBreadthError("candidate kind is required")
    parameters = _normalized_parameters(candidate.get("parameters"))
    hypothesis_id = _hash(
        {
            "experiment": "C",
            "kind": kind,
            "parameters_without_size": parameters,
        }
    )[:20]
    return kind, parameters, hypothesis_id


def summarize(paths: list[Path]) -> dict[str, Any]:
    reports = [_load(path) for path in sorted(paths)]
    if len(reports) != 6:
        raise ExperimentCBreadthError(f"Experiment C requires six reports; got {len(reports)}")
    market_keys = {(report["symbol"], report["interval"]) for report in reports}
    if market_keys != _EXPECTED_MARKETS:
        raise ExperimentCBreadthError("Experiment C market matrix changed")

    grouped: dict[str, dict[str, Any]] = {}
    ready_trial_to_hypothesis: dict[tuple[str, str], tuple[str, str]] = {}

    for report in reports:
        policy_eligible = set(report["policy_eligible_trial_ids"])
        robust = set(report["robustness_eligible_trial_ids"])
        candidates_by_trial: dict[str, dict[str, Any]] = {}
        for candidate in report["candidates"]:
            if not isinstance(candidate, dict):
                raise ExperimentCBreadthError("candidate entry must be object")
            trial_id = candidate.get("trial_id")
            if not isinstance(trial_id, str):
                raise ExperimentCBreadthError("trial_id is required")
            candidates_by_trial[trial_id] = candidate
            kind, parameters, hypothesis_id = _hypothesis_identity(candidate)
            metrics = candidate.get("metrics")
            if not isinstance(metrics, dict):
                raise ExperimentCBreadthError("candidate metrics must be object")
            item = grouped.setdefault(
                hypothesis_id,
                {
                    "hypothesis_id": hypothesis_id,
                    "kind": kind,
                    "parameters_without_size": parameters,
                    "observations": [],
                },
            )
            if item["kind"] != kind or item["parameters_without_size"] != parameters:
                raise ExperimentCBreadthError("hypothesis identity collision")
            item["observations"].append(
                CrossMarketObservation(
                    market=f"{report['symbol']}:{report['interval']}",
                    symbol=str(report["symbol"]),
                    interval=str(report["interval"]),
                    net_return=_finite(metrics.get("net_return"), "net_return"),
                    sharpe=_finite(metrics.get("sharpe"), "sharpe"),
                    max_drawdown=_finite(metrics.get("max_drawdown"), "max_drawdown"),
                    policy_eligible=trial_id in policy_eligible,
                    robustness_passed=trial_id in robust,
                )
            )

        per_campaign_ready = str(report.get("per_campaign_development_ready_trial_id") or "")
        if per_campaign_ready:
            candidate = candidates_by_trial.get(per_campaign_ready)
            if candidate is None:
                raise ExperimentCBreadthError("per-campaign ready trial missing from candidates")
            _, _, hypothesis_id = _hypothesis_identity(candidate)
            ready_trial_to_hypothesis[(report["symbol"], report["interval"])] = (
                per_campaign_ready,
                hypothesis_id,
            )

    if len(grouped) != 12:
        raise ExperimentCBreadthError(
            f"normalized Experiment C hypothesis count must be 12; got {len(grouped)}"
        )

    evidence_items = []
    passed_hypotheses: set[str] = set()
    for hypothesis_id, item in sorted(grouped.items()):
        observations = tuple(item["observations"])
        evidence = evaluate_cross_market_breadth(
            hypothesis_id=hypothesis_id,
            observations=observations,
            policy=DEFAULT_EXPERIMENT_C_BREADTH_POLICY,
        )
        if evidence.passed:
            passed_hypotheses.add(hypothesis_id)
        evidence_items.append(
            {
                "hypothesis_id": hypothesis_id,
                "kind": item["kind"],
                "parameters_without_size": item["parameters_without_size"],
                "policy_fingerprint": evidence.policy_fingerprint,
                "evidence_fingerprint": evidence.fingerprint,
                "passed": evidence.passed,
                "reasons": list(evidence.reasons),
                "positive_return_markets": evidence.positive_return_markets,
                "positive_sharpe_markets": evidence.positive_sharpe_markets,
                "policy_eligible_markets": evidence.policy_eligible_markets,
                "robust_markets": evidence.robust_markets,
                "distinct_robust_symbols": evidence.distinct_robust_symbols,
                "median_sharpe": evidence.median_sharpe,
                "worst_net_return": evidence.worst_net_return,
                "max_drawdown_across_markets": evidence.max_drawdown_across_markets,
                "observations": [obs.payload for obs in evidence.observations],
            }
        )

    development_promotion_candidates: list[dict[str, object]] = []
    for (symbol, interval), (trial_id, hypothesis_id) in sorted(
        ready_trial_to_hypothesis.items()
    ):
        if hypothesis_id not in passed_hypotheses:
            continue
        development_promotion_candidates.append(
            {
                "symbol": symbol,
                "interval": interval,
                "trial_id": trial_id,
                "hypothesis_id": hypothesis_id,
                "breadth_passed": True,
                "per_campaign_gates_passed": True,
            }
        )

    return {
        "summary_version": 1,
        "experiment": "C",
        "scope": "DEVELOPMENT_ONLY",
        "breadth_policy_fingerprint": DEFAULT_EXPERIMENT_C_BREADTH_POLICY.fingerprint,
        "breadth_policy": DEFAULT_EXPERIMENT_C_BREADTH_POLICY.payload,
        "campaign_count": 6,
        "hypothesis_count": 12,
        "hypotheses_passing_breadth": len(passed_hypotheses),
        "per_campaign_development_ready_count": len(ready_trial_to_hypothesis),
        "development_promotion_candidate_count": len(development_promotion_candidates),
        "development_promotion_candidates": development_promotion_candidates,
        "hypotheses": evidence_items,
        "all_holdouts_untouched": True,
        "holdout_checked_out": False,
        "holdout_authority": False,
        "paper_authority": False,
        "live_authority": False,
        "next_boundary": (
            "A development promotion candidate still requires the separate existing "
            "protected-HOLDOUT permit path; this aggregate does not consume that permit."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the preregistered Experiment C cross-market breadth gate to six "
            "DEVELOPMENT reports. Does not open HOLDOUT."
        )
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    paths = sorted(args.input.rglob("*-report.json"))
    payload = summarize(paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
