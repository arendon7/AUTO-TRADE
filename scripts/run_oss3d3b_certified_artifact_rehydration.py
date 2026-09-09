from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from labs.oss3_qlib.certified_artifact_rehydration import rehydrate_canonical_d2y_artifact


RESULT_VERSION = "OSS3D3B_CERTIFIED_ARTIFACT_REAL_REHYDRATION_RESULT_V1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_result(artifact_zip: str | Path) -> dict[str, object]:
    material = rehydrate_canonical_d2y_artifact(artifact_zip)
    evidence = material.evidence
    result = {
        "result_version": RESULT_VERSION,
        "d3b_material_fingerprint": material.fingerprint,
        "d3b_evidence_fingerprint": evidence.fingerprint,
        "d3a_scientific_fingerprint": material.raw_split.scientific_fingerprint,
        "d3a_material_fingerprint": material.raw_split.fingerprint,
        "d3a_evidence_fingerprint": material.raw_split.evidence.fingerprint,
        "artifact_zip_sha256": evidence.artifact_zip_sha256,
        "source_inventory_sha256": evidence.source_inventory_sha256,
        "evidence_tar_sha256": evidence.evidence_tar_sha256,
        "source_inventory_root": evidence.source_inventory_root,
        "d2z_stable_material_root": evidence.d2z_stable_material_root,
        "original_d2y_partition_material_fingerprint": evidence.original_d2y_partition_material_fingerprint,
        "rehydrated_partition_material_fingerprint": evidence.rehydrated_partition_material_fingerprint,
        "raw_training_source_hash": evidence.raw_training_source_hash,
        "raw_development_source_hash": evidence.raw_development_source_hash,
        "training_universe_hash": evidence.training_universe_hash,
        "development_universe_hash": evidence.development_universe_hash,
        "descriptor_count": evidence.descriptor_count,
        "source_file_count": evidence.source_file_count,
        "exact_original_partition_fingerprint_reproduced": evidence.exact_original_partition_fingerprint_reproduced,
        "network_used_by_rehydrator": evidence.network_used_by_rehydrator,
        "provider_network_used_by_rehydrator": evidence.provider_network_used_by_rehydrator,
        "qlib_runtime_used": evidence.qlib_runtime_used,
        "final_holdout_values_loaded": evidence.final_holdout_values_loaded,
        "promotion_authorized": evidence.promotion_authorized,
        "execution_authorized": evidence.execution_authorized,
        "paper_execution_authorized": evidence.paper_execution_authorized,
        "capital_authority": evidence.capital_authority,
        "live_trading": evidence.live_trading,
    }
    result["result_fingerprint"] = sha256(_canonical(result)).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Rehydrate exact D2Y certified artifact through D2Z/D3A")
    parser.add_argument("--artifact-zip", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result = build_result(args.artifact_zip)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_canonical(result) + b"\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
