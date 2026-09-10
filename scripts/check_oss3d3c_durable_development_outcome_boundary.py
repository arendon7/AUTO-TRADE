from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path

from labs.oss3_qlib.durable_development_outcome import (
    SCIENTIFIC_IDENTITY_POLICY,
    d3a_scientific_outcome_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ROOT / "labs/oss3_qlib/durable_development_outcome.py",
    ROOT / "labs/oss3_qlib/durable_development_outcome_rehydration.py",
    ROOT / "scripts/run_oss3d3c_durable_development_outcome.py",
)
BASELINE = ROOT / "knowledge/20_RESEARCH/OSS3D3A_CERTIFIED_BASELINE_d6a66390.json"
EXPECTED_BASELINE_FINGERPRINT = (
    "ce703916e83161cc71e73e56e4a9ffab5c0989bf630cfbae260a90a5846ca9cc"
)
EXPECTED_SCIENTIFIC_OUTCOME = (
    "5b4261b186e30cbdc30ec2c3025305fbb96650247a066cdf470ce53e80f86ea7"
)

FORBIDDEN_IMPORT_FRAGMENTS = (
    "broker",
    "oms",
    "safety",
    "execution_engine",
    "order_intent",
    "holdout_permit",
    "final_holdout_evaluation",
    "final_holdout_protocol",
    "economic_holdout",
)

FORBIDDEN_CALL_NAMES = {
    "run_isolated_qlib_family_candidate",
    "evaluate_development_predictions",
    "materialize_development_labels_after_preregistration",
    "preregister_and_record",
    "consume_holdout_permit",
    "submit_order",
    "place_order",
    "execute_order",
    "send_order",
    "urlopen",
    "create_connection",
}

FORBIDDEN_TEXT_MARKERS = (
    "requests.",
    "httpx.",
    "socket.socket(",
    "subprocess.",
    "urllib.request",
    "qlib.contrib",
    "qlib.init(",
    "holdoutpermit(",
    "orderintent(",
)


def _imports(tree: ast.AST) -> tuple[str, ...]:
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            result.append(module)
    return tuple(result)


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _load_baseline() -> dict[str, object]:
    if not BASELINE.is_file() or BASELINE.is_symlink():
        raise SystemExit("OSS-3D3C certified D3A baseline evidence is missing or unsafe")
    raw = BASELINE.read_text(encoding="utf-8")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit("OSS-3D3C certified D3A baseline is invalid JSON") from exc
    if not isinstance(document, dict):
        raise SystemExit("OSS-3D3C certified D3A baseline must be an object")
    if raw != _canonical_json(document) + "\n":
        raise SystemExit("OSS-3D3C certified D3A baseline is not canonical JSON")

    stored_fingerprint = document.get("fingerprint")
    if stored_fingerprint != EXPECTED_BASELINE_FINGERPRINT:
        raise SystemExit("OSS-3D3C certified D3A baseline fingerprint drifted")
    payload = dict(document)
    payload.pop("fingerprint")
    recomputed = sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    if recomputed != stored_fingerprint:
        raise SystemExit("OSS-3D3C certified D3A baseline payload hash mismatch")

    science = d3a_scientific_outcome_fingerprint(payload)
    if science != EXPECTED_SCIENTIFIC_OUTCOME:
        raise SystemExit("OSS-3D3C certified D3A scientific identity drifted")
    return document


def main() -> int:
    for path in FILES:
        if not path.is_file():
            raise SystemExit(f"OSS-3D3C boundary missing source: {path.relative_to(ROOT)}")
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        for module in _imports(tree):
            lowered = module.lower()
            for marker in FORBIDDEN_IMPORT_FRAGMENTS:
                if marker in lowered:
                    raise SystemExit(
                        f"OSS-3D3C forbidden import {module!r} in {path.relative_to(ROOT)}"
                    )

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in FORBIDDEN_CALL_NAMES:
                raise SystemExit(
                    f"OSS-3D3C forbidden call {_call_name(node)!r} in {path.relative_to(ROOT)}"
                )

        lowered_source = source.lower()
        for marker in FORBIDDEN_TEXT_MARKERS:
            if marker in lowered_source:
                raise SystemExit(
                    f"OSS-3D3C forbidden runtime marker {marker!r} in {path.relative_to(ROOT)}"
                )

    bundle_source = FILES[0].read_text(encoding="utf-8")
    for required in (
        'SCIENTIFIC_IDENTITY_POLICY = "D3A_STABLE_SCIENTIFIC_OUTPUT_EXCLUDING_RUNTIME_PROVENANCE_V1"',
        'certified_d3a_baseline_fingerprint: str',
        'scientific_outcome_fingerprint: str',
        'final_holdout_observed: bool = False',
        'final_holdout_authorized: bool = False',
        'holdout_permit_consumed: bool = False',
        'profitability_claim_authorized: bool = False',
        'promotion_authorized: bool = False',
        'execution_authorized: bool = False',
        'paper_execution_authorized: bool = False',
        'capital_authority: str = "NONE"',
        'live_trading: str = "BLOCKED"',
        'next_frontier != NEXT_FRONTIER',
        'verify_development_winner_seal(',
        'baseline_science != source_science',
    ):
        if required not in bundle_source:
            raise SystemExit(f"OSS-3D3C missing required fail-closed invariant: {required}")

    rehydrator_source = FILES[1].read_text(encoding="utf-8")
    for required in (
        'certified_d3a_baseline_path: str | Path',
        'baseline_science != replay_science',
        'mode=ro',
        'PRAGMA query_only = ON',
        'shutil.copyfile(source_ledger, copy)',
        'evaluate_oss3d2e_tournament(',
        'preregistration.d2e_plan',
        'seal_development_winner(',
        'output.fingerprint != expected_hashes[candidate_id]',
        'preregistration.fingerprint != d3a_payload.get(',
        'batch.fingerprint != d3a_payload.get("d2h_batch_evidence_fingerprint")',
        'winner.fingerprint != d3a_payload.get("d2i_winner_seal_fingerprint")',
    ):
        if required not in rehydrator_source:
            raise SystemExit(f"OSS-3D3C rehydration proof missing: {required}")

    runner_source = FILES[2].read_text(encoding="utf-8")
    if '--certified-d3a-baseline' not in runner_source:
        raise SystemExit("OSS-3D3C runner does not require certified baseline")

    _load_baseline()
    if SCIENTIFIC_IDENTITY_POLICY != (
        "D3A_STABLE_SCIENTIFIC_OUTPUT_EXCLUDING_RUNTIME_PROVENANCE_V1"
    ):
        raise SystemExit("OSS-3D3C imported scientific identity policy drifted")

    print(
        "OSS-3D3C DURABLE DEVELOPMENT OUTCOME BOUNDARY PASS — "
        "certified baseline + stable scientific identity + current provenance; "
        "source ledgers read-only; no model rerun, no label rematerialization, "
        "no D2D recomputation; FINAL_HOLDOUT unobserved/unconsumed; "
        "no broker/OMS/Safety/OrderIntent; PAPER false; capital NONE; LIVE blocked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
