from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class SummaryError(RuntimeError):
    pass


def _load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SummaryError(f"cannot read report {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SummaryError(f"report root must be an object: {path}")
    required = {
        "campaign_id",
        "symbol",
        "interval",
        "program_hash",
        "source_dataset_hash",
        "development_dataset_hash",
        "protected_holdout",
        "candidate_count",
        "selected_trial_id",
        "promotion_ready_trial_id",
        "statistical_gate",
        "tournament_selected_trial_id",
        "robustness_eligible_trial_ids",
        "robustness",
        "pbo",
        "deflated_sharpe",
        "candidates",
    }
    missing = required - set(payload)
    if missing:
        raise SummaryError(f"report {path} missing fields: {sorted(missing)}")
    holdout = payload["protected_holdout"]
    if not isinstance(holdout, dict) or holdout.get("checked_out") is not False:
        raise SummaryError(f"report does not prove untouched HOLDOUT: {path}")
    statistical_gate = payload["statistical_gate"]
    if not isinstance(statistical_gate, dict) or not isinstance(
        statistical_gate.get("passed"), bool
    ):
        raise SummaryError(f"report has invalid statistical gate: {path}")
    if bool(payload["promotion_ready_trial_id"]) != statistical_gate["passed"]:
        raise SummaryError(
            f"promotion readiness/statistical gate mismatch: {path}"
        )
    return payload


def _candidate_for_trial(
    report: dict[str, Any], trial_id: str
) -> dict[str, Any] | None:
    if not trial_id:
        return None
    for candidate in report.get("candidates", []):
        if isinstance(candidate, dict) and candidate.get("trial_id") == trial_id:
            return candidate
    raise SummaryError(
        f"trial is absent from candidate ledger: {report.get('campaign_id')}:{trial_id}"
    )


def _robustness_for_selected(
    report: dict[str, Any], selected_candidate: dict[str, Any] | None
) -> dict[str, Any] | None:
    if selected_candidate is None:
        return None
    strategy_id = selected_candidate.get("strategy_id")
    for evidence in report.get("robustness", []):
        if isinstance(evidence, dict) and evidence.get("strategy_id") == strategy_id:
            return evidence
    return None


def summarize(report_paths: list[Path]) -> dict[str, Any]:
    if not report_paths:
        raise SummaryError("at least one report is required")
    reports = [_load_report(path) for path in sorted(report_paths)]
    campaign_ids = [str(item["campaign_id"]) for item in reports]
    if len(campaign_ids) != len(set(campaign_ids)):
        raise SummaryError("campaign ids must be unique")

    entries: list[dict[str, Any]] = []
    for report in reports:
        selected_candidate = _candidate_for_trial(report, report["selected_trial_id"])
        promotion_candidate = _candidate_for_trial(
            report, report["promotion_ready_trial_id"]
        )
        robustness = _robustness_for_selected(report, selected_candidate)
        metrics = selected_candidate.get("metrics", {}) if selected_candidate else {}
        parameters = (
            selected_candidate.get("parameters", {}) if selected_candidate else {}
        )
        pbo = report.get("pbo", {})
        dsr = report.get("deflated_sharpe", {})
        entries.append(
            {
                "campaign_id": report["campaign_id"],
                "symbol": report["symbol"],
                "interval": report["interval"],
                "program_hash": report["program_hash"],
                "source_dataset_hash": report["source_dataset_hash"],
                "development_dataset_hash": report["development_dataset_hash"],
                "protected_holdout_hash": report["protected_holdout"]["dataset_hash"],
                "protected_holdout_bars": report["protected_holdout"]["bar_count"],
                "holdout_checked_out": False,
                "candidate_count": report["candidate_count"],
                "tournament_selected_trial_id": report["tournament_selected_trial_id"],
                "robustness_eligible_count": len(
                    report.get("robustness_eligible_trial_ids", [])
                ),
                "selected_trial_id": report["selected_trial_id"],
                "selected_strategy_id": (
                    selected_candidate.get("strategy_id") if selected_candidate else ""
                ),
                "selected_parameters": parameters,
                "selected_metrics": metrics,
                "selected_robustness": (
                    {
                        "fingerprint": robustness.get("fingerprint"),
                        "positive_fold_ratio": robustness.get("positive_fold_ratio"),
                        "median_fold_sharpe": robustness.get("median_fold_sharpe"),
                        "worst_fold_net_return": robustness.get("worst_fold_net_return"),
                        "worst_fold_drawdown": robustness.get("worst_fold_drawdown"),
                        "stress_pass_ratio": robustness.get("stress_pass_ratio"),
                        "worst_stress_net_return": robustness.get(
                            "worst_stress_net_return"
                        ),
                        "worst_stress_drawdown": robustness.get(
                            "worst_stress_drawdown"
                        ),
                    }
                    if robustness
                    else None
                ),
                "statistical_gate": report["statistical_gate"],
                "promotion_ready_trial_id": report["promotion_ready_trial_id"],
                "promotion_ready_strategy_id": (
                    promotion_candidate.get("strategy_id") if promotion_candidate else ""
                ),
                "pbo": pbo,
                "deflated_sharpe": dsr,
            }
        )

    selected_count = sum(1 for item in entries if item["selected_trial_id"])
    promotion_count = sum(
        1 for item in entries if item["promotion_ready_trial_id"]
    )
    return {
        "summary_version": 2,
        "campaign_count": len(entries),
        "campaigns_with_robust_selection": selected_count,
        "campaigns_without_robust_selection": len(entries) - selected_count,
        "campaigns_promotion_ready": promotion_count,
        "campaigns_not_promotion_ready": len(entries) - promotion_count,
        "all_holdouts_untouched": all(
            item["holdout_checked_out"] is False for item in entries
        ),
        "campaigns": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate R7 DEVELOPMENT reports without opening HOLDOUT."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Directory recursively containing *-report.json files.",
    )
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    paths = sorted(args.input.rglob("*-report.json"))
    summary = summarize(paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
