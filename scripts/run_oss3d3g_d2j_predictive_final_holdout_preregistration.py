from __future__ import annotations

import argparse
from pathlib import Path

from labs.oss3_qlib.d2j_predictive_final_holdout_preregistration import (
    preregister_predictive_final_holdout,
    write_d3g_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preregister exact OSS-3D2J predictive FINAL_HOLDOUT protocol from certified D3C/D3F public identities")
    parser.add_argument("--d3c-bundle", required=True)
    parser.add_argument("--d3f-public-evidence", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    evidence = preregister_predictive_final_holdout(d3c_bundle_path=args.d3c_bundle, d3f_public_evidence_path=args.d3f_public_evidence, registry_path=args.registry)
    write_d3g_evidence(evidence, Path(args.output))
    print("OSS-3D3G D2J predictive FINAL_HOLDOUT preregistration: PASS")
    print("D3G evidence:", evidence.fingerprint)
    print("D2J receipt:", evidence.d2j_receipt_hash)
    print("D2I seal:", evidence.source_d2i_seal_fingerprint)
    print("Q1 commitment:", evidence.holdout_commitment_fingerprint)
    print("FINAL_HOLDOUT evaluations performed:", evidence.final_holdout_evaluations_performed)
    print("holdout permit issued:", evidence.holdout_permit_issued)
    print("holdout checkout authorized:", evidence.final_holdout_checkout_authorized)
    print("capital authority:", evidence.capital_authority)
    print("LIVE:", evidence.live_trading)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
