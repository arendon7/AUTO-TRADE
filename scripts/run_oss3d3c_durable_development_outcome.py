from __future__ import annotations

import argparse

from labs.oss3_qlib.durable_development_outcome_rehydration import (
    reconstruct_and_write_durable_development_outcome,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconstruct and persist OSS-3D3C durable DEVELOPMENT outcome evidence"
    )
    parser.add_argument("--d3a-result", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    bundle = reconstruct_and_write_durable_development_outcome(
        d3a_result_path=args.d3a_result,
        work_root=args.work_root,
        output_path=args.output,
    )
    print("OSS-3D3C artifact_hash:", bundle.fingerprint)
    print("source_d3a_evidence_fingerprint:", bundle.source_d3a_evidence_fingerprint)
    print("d2h_preregistration_fingerprint:", bundle.d2h_preregistration_fingerprint)
    print("d2h_batch_evidence_fingerprint:", bundle.d2h_batch_evidence_fingerprint)
    print("d2i_winner_seal_fingerprint:", bundle.d2i_winner_seal_fingerprint)
    print("selected_trial_id:", bundle.selected_trial_id)
    print("final_holdout_observed:", bundle.final_holdout_observed)
    print("capital_authority:", bundle.capital_authority)
    print("live_trading:", bundle.live_trading)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
