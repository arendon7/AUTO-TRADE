from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from labs.oss3_market_data.real_campaign_rehydration import (
    rehydrate_certified_real_campaign,
    write_rehydration_evidence,
)


def _parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--now must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rehydrate exact certified OSS-3D3A D2Y campaign evidence")
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--extraction-parent", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repository-root", default=None)
    parser.add_argument("--now", default=None)
    args = parser.parse_args()

    result = rehydrate_certified_real_campaign(
        artifact_directory=args.artifact_dir,
        extraction_parent=args.extraction_parent,
        now=_parse_now(args.now),
        repository_root=args.repository_root,
    )
    write_rehydration_evidence(result.evidence, args.output)
    print("OSS-3D3A rehydration: PASS")
    print("evidence_root:", result.evidence_root)
    print("rehydration_evidence_fingerprint:", result.evidence.fingerprint)
    print("d2w_evidence_fingerprint:", result.handoff.evidence.fingerprint)
    print("training_universe_hash:", result.handoff.evidence.training_universe_hash)
    print("development_universe_hash:", result.handoff.evidence.development_universe_hash)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
