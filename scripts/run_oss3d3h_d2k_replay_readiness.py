from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from labs.oss3_qlib.d2k_replay_readiness import (
    PREPARATION_FILE,
    SEAL_FILE,
    prepare_d2k_replay_package,
    read_d2k_replay_preparation,
    read_d2k_replay_readiness_seal,
    seal_d2k_replay_readiness,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OSS-3D3H D2K replay-readiness runner")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="reconstruct exact replay inputs without Qlib/Q1")
    prepare.add_argument("--evidence-root", required=True)
    prepare.add_argument("--d3c-bundle", required=True)
    prepare.add_argument("--d3g-result", required=True)
    prepare.add_argument("--d3g-registry", required=True)
    prepare.add_argument("--output-root", required=True)
    prepare.add_argument("--repository-root")

    seal = sub.add_parser("seal", help="attest exact current winner runtime without model execution")
    seal.add_argument("--package-root", required=True)
    seal.add_argument("--d3c-bundle", required=True)
    seal.add_argument("--d3g-result", required=True)
    seal.add_argument("--d3g-registry", required=True)

    verify = sub.add_parser("verify", help="strictly reload preparation + seal")
    verify.add_argument("--package-root", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "prepare":
        evidence = prepare_d2k_replay_package(
            evidence_root=args.evidence_root,
            d3c_bundle_path=args.d3c_bundle,
            d3g_result_path=args.d3g_result,
            d3g_registry_path=args.d3g_registry,
            output_root=args.output_root,
            now=datetime.now(timezone.utc),
            repository_root=args.repository_root,
        )
        print("D3H PREPARATION:", evidence.fingerprint)
        print("WINNER REQUEST:", evidence.winner_request_hash)
        print("TRAIN BUNDLE:", evidence.training_bundle_hash)
        return 0
    if args.command == "seal":
        seal = seal_d2k_replay_readiness(
            package_root=args.package_root,
            d3c_bundle_path=args.d3c_bundle,
            d3g_result_path=args.d3g_result,
            d3g_registry_path=args.d3g_registry,
        )
        print("D3H READINESS SEAL:", seal.fingerprint)
        print("D2K SEMANTIC HASH:", seal.evaluator_semantic_hash)
        return 0
    root = Path(args.package_root)
    preparation = read_d2k_replay_preparation(root / PREPARATION_FILE)
    seal = read_d2k_replay_readiness_seal(root / SEAL_FILE)
    if seal.preparation_fingerprint != preparation.fingerprint:
        raise SystemExit("D3H preparation/seal fingerprint mismatch")
    print("D3H STRICT RELOAD: PASS")
    print("D3H READINESS SEAL:", seal.fingerprint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
