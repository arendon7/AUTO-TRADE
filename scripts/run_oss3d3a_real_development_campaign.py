from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from labs.oss3_qlib.real_development_campaign import (
    run_real_development_campaign,
    write_real_development_campaign_evidence,
)


def _parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--now must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _expected_d2w(path: str | None) -> str | None:
    if path is None:
        return None
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    value = document.get("d2w_evidence_fingerprint")
    if not isinstance(value, str):
        raise ValueError("rehydration evidence lacks d2w_evidence_fingerprint")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OSS-3D3A real DEVELOPMENT model campaign")
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--rehydration-evidence", default=None)
    parser.add_argument("--repository-root", default=None)
    parser.add_argument("--now", default=None)
    args = parser.parse_args()

    evidence = run_real_development_campaign(
        evidence_root=args.evidence_root,
        work_root=args.work_root,
        now=_parse_now(args.now),
        repository_root=args.repository_root,
        expected_d2w_evidence_fingerprint=_expected_d2w(args.rehydration_evidence),
    )
    write_real_development_campaign_evidence(evidence, args.output)
    print("OSS-3D3A status:", evidence.status)
    print("evidence_fingerprint:", evidence.fingerprint)
    print("evaluable_candidates:", ",".join(evidence.evaluable_candidate_ids) or "NONE")
    print("selected_trial_id:", evidence.selected_trial_id or "NONE")
    if evidence.winner_primary_metric is not None:
        print("winner_primary_metric:", evidence.winner_primary_metric)
        print("winner_raw_p_value:", evidence.winner_raw_p_value)
        print("winner_holm_adjusted_p_value:", evidence.winner_holm_adjusted_p_value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
