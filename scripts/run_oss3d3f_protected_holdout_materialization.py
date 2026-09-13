from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from labs.oss3_market_data.dual_holdout_acquisition import (
    LEDGER_FILENAME,
    SQLiteD3EAcquisitionLedger,
)
from labs.oss3_qlib.protected_holdout_materialization import (
    EXPECTED_D3E_CAMPAIGN_SEAL,
    ProtectedHoldoutMaterializationIntegrityError,
    materialize_protected_dual_holdouts,
    write_public_d3f_evidence,
)


def _parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--now must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _d3e_reverification_time(evidence_root: str | Path) -> datetime:
    """Replay the immutable D3E campaign timestamp for byte-identical restart proof."""
    ledger_path = Path(evidence_root).expanduser().resolve() / LEDGER_FILENAME
    if not ledger_path.is_file():
        raise ProtectedHoldoutMaterializationIntegrityError(
            "D3F requires the pre-existing certified D3E campaign ledger"
        )
    campaign = SQLiteD3EAcquisitionLedger(ledger_path).get_campaign()
    if campaign is None:
        raise ProtectedHoldoutMaterializationIntegrityError(
            "D3F requires a pre-existing complete D3E campaign seal"
        )
    if campaign.fingerprint != EXPECTED_D3E_CAMPAIGN_SEAL:
        raise ProtectedHoldoutMaterializationIntegrityError(
            "D3F D3E campaign seal differs from the certified source"
        )
    parsed = datetime.fromisoformat(campaign.sealed_at)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProtectedHoldoutMaterializationIntegrityError(
            "D3F certified D3E sealed_at is not timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize value-opaque OSS-3D3F predictive/economic holdout commitments")
    parser.add_argument("--d3d-registry", required=True)
    parser.add_argument("--d3e-evidence-root", required=True)
    parser.add_argument("--d2y-evidence-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repository-root", default=None)
    parser.add_argument("--now", default=None)
    args = parser.parse_args()

    requested_now = _parse_now(args.now)
    certified_d3e_now = _d3e_reverification_time(args.d3e_evidence_root)
    if args.now is not None and requested_now != certified_d3e_now:
        raise ProtectedHoldoutMaterializationIntegrityError(
            "D3F --now must equal the immutable certified D3E campaign sealed_at"
        )

    runtime = materialize_protected_dual_holdouts(
        d3d_registry_path=args.d3d_registry,
        d3e_evidence_root=args.d3e_evidence_root,
        d2y_evidence_root=args.d2y_evidence_root,
        now=certified_d3e_now,
        repository_root=args.repository_root,
    )
    write_public_d3f_evidence(runtime.evidence, Path(args.output))
    evidence = runtime.evidence
    print("OSS-3D3F protected holdout materialization: PASS")
    print("evidence_fingerprint:", evidence.fingerprint)
    print("predictive_commitment_fingerprint:", evidence.predictive_commitment_fingerprint)
    print("predictive_rows:", evidence.predictive_row_count)
    print("predictive_cross_sections:", evidence.predictive_cross_section_count)
    print("economic_commitment_fingerprint:", evidence.economic_commitment_fingerprint)
    print("economic_bars:", evidence.economic_bar_count)
    print("label_values_exposed:", evidence.label_values_exposed)
    print("prediction_values_materialized:", evidence.prediction_values_materialized)
    print("predictive_metrics_computed:", evidence.predictive_metrics_computed)
    print("holdout_permit_consumed:", evidence.holdout_permit_consumed)
    print("capital_authority:", evidence.capital_authority)
    print("live_trading:", evidence.live_trading)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
