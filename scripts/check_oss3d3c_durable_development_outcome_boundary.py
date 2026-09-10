from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ROOT / "labs/oss3_qlib/durable_development_outcome.py",
    ROOT / "labs/oss3_qlib/durable_development_outcome_rehydration.py",
    ROOT / "scripts/run_oss3d3c_durable_development_outcome.py",
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
    ):
        if required not in bundle_source:
            raise SystemExit(f"OSS-3D3C missing required fail-closed invariant: {required}")

    rehydrator_source = FILES[1].read_text(encoding="utf-8")
    for required in (
        'mode=ro',
        'PRAGMA query_only = ON',
        'shutil.copyfile(source_ledger, copy)',
        'evaluate_oss3d2e_tournament(ledger, preregistration.d2e_plan)',
        'seal_development_winner(',
        'output.fingerprint != expected_hashes[candidate_id]',
        'preregistration.fingerprint != d3a_payload.get("d2h_preregistration_fingerprint")',
        'batch.fingerprint != d3a_payload.get("d2h_batch_evidence_fingerprint")',
        'winner.fingerprint != d3a_payload.get("d2i_winner_seal_fingerprint")',
    ):
        if required not in rehydrator_source:
            raise SystemExit(f"OSS-3D3C rehydration proof missing: {required}")

    # D3C may verify the persisted D2E tournament on a temporary ledger copy,
    # but it must never reproduce candidate fitting, label materialization, D2D
    # evaluation, holdout checkout or any execution path.
    print(
        "OSS-3D3C DURABLE DEVELOPMENT OUTCOME BOUNDARY PASS — "
        "portable D3A/D2H/D2I evidence only; source ledgers read-only; "
        "no model rerun, no label rematerialization, no D2D recomputation, "
        "FINAL_HOLDOUT unobserved/unconsumed; no broker/OMS/Safety/OrderIntent; "
        "PAPER false; capital NONE; LIVE blocked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
