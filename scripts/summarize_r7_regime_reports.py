from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class RegimeSummaryError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegimeSummaryError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegimeSummaryError(f"report root must be object: {path}")
    required = {
        "campaign_id",
        "symbol",
        "interval",
        "selected_trial_id",
        "promotion_ready_trial_id",
        "holdout_ready_trial_id",
        "protected_holdout",
        "statistical_gate",
        "regime_stability_gate",
    }
    missing = required - set(payload)
    if missing:
        raise RegimeSummaryError(f"report missing {sorted(missing)}: {path}")
    holdout = payload["protected_holdout"]
    if not isinstance(holdout, dict) or holdout.get("checked_out") is not False:
        raise RegimeSummaryError(f"HOLDOUT proof invalid: {path}")
    return payload


def _selected_regime(report: dict[str, Any]) -> dict[str, Any] | None:
    selected = report.get("selected_trial_id")
    if not selected:
        return None
    gate = report["regime_stability_gate"]
    for item in gate.get("candidates", []):
        if isinstance(item, dict) and item.get("trial_id") == selected:
            return item
    if selected in set(report.get("robustness_eligible_trial_ids", [])):
        raise RegimeSummaryError(
            f"selected robust trial lacks regime evidence: {report['campaign_id']}"
        )
    return None


def summarize(paths: list[Path]) -> dict[str, Any]:
    if not paths:
        raise RegimeSummaryError("at least one report is required")
    reports = [_load(path) for path in sorted(paths)]
    campaign_ids = [str(report["campaign_id"]) for report in reports]
    if len(campaign_ids) != len(set(campaign_ids)):
        raise RegimeSummaryError("campaign ids must be unique")

    campaigns: list[dict[str, Any]] = []
    for report in reports:
        selected_regime = _selected_regime(report)
        campaigns.append(
            {
                "campaign_id": report["campaign_id"],
                "symbol": report["symbol"],
                "interval": report["interval"],
                "selected_trial_id": report["selected_trial_id"],
                "statistical_promotion_trial_id": report["promotion_ready_trial_id"],
                "holdout_ready_trial_id": report["holdout_ready_trial_id"],
                "holdout_checked_out": False,
                "holdout_hash": report["protected_holdout"]["dataset_hash"],
                "statistical_gate": report["statistical_gate"],
                "regime_selected": selected_regime,
                "regime_gate_selected_passed": bool(
                    report["regime_stability_gate"].get("selected_passed", False)
                ),
                "regime_evaluated_count": len(
                    report["regime_stability_gate"].get("candidates", [])
                ),
                "holdout_gate": report.get("holdout_gate", {}),
            }
        )

    robust_count = sum(1 for item in campaigns if item["selected_trial_id"])
    statistical_count = sum(
        1 for item in campaigns if item["statistical_promotion_trial_id"]
    )
    regime_selected_pass_count = sum(
        1
        for item in campaigns
        if item["selected_trial_id"] and item["regime_gate_selected_passed"]
    )
    holdout_ready_count = sum(1 for item in campaigns if item["holdout_ready_trial_id"])
    return {
        "summary_version": 1,
        "scope": "DEVELOPMENT_ONLY",
        "campaign_count": len(campaigns),
        "campaigns_with_robust_selection": robust_count,
        "campaigns_statistically_promotion_ready": statistical_count,
        "campaigns_selected_regime_pass": regime_selected_pass_count,
        "campaigns_holdout_ready": holdout_ready_count,
        "all_holdouts_untouched": all(
            item["holdout_checked_out"] is False for item in campaigns
        ),
        "campaigns": campaigns,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate R7 DEVELOPMENT regime evidence without opening HOLDOUT."
    )
    parser.add_argument("input", type=Path)
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
