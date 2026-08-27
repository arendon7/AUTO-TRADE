from __future__ import annotations

import ast
import inspect
from pathlib import Path

import autotrade.paper_execution_human_review as review
import autotrade.paper_execution_risk_handoff as handoff


ROOT = Path(__file__).resolve().parents[1]
HANDOFF_PATH = ROOT / "src/autotrade/paper_execution_risk_handoff.py"
REVIEW_PATH = ROOT / "src/autotrade/paper_execution_human_review.py"

FORBIDDEN_IMPORT_FRAGMENTS = (
    "requests",
    "urllib",
    "http.client",
    "socket",
    "alpaca_paper_crypto_writer",
    "first_canary_execution_gate",
    "first_canary_real_paper_execution",
    "first_canary_external_post_consent",
    "connectivity_workspace_post",
)
FORBIDDEN_CALL_OR_SYMBOLS = (
    "record_operator_approval",
    "SQLiteCryptoOperatorDecisionRegistry",
    ".consume(",
    "submit_once",
    "stage_external_submission",
    "execute_first_canary_once",
    "HttpsAlpacaPaperCryptoWriteTransport",
    "external_post_consent",
)
FORBIDDEN_PARAMETERS = (
    "now",
    "credentials",
    "writer",
    "transport",
    "operator_id",
    "confirmation",
    "registry",
    "runtime",
    "live",
    "environment",
)


def _source(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"missing W87-D source: {path}")
    return path.read_text(encoding="utf-8")


def _imports(source: str) -> set[str]:
    tree = ast.parse(source)
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def _assert_no_forbidden_surface(path: Path, source: str) -> None:
    imports = _imports(source)
    for fragment in FORBIDDEN_IMPORT_FRAGMENTS:
        if any(fragment in imported for imported in imports):
            raise SystemExit(f"{path.name}: forbidden import surface {fragment}")
    for fragment in FORBIDDEN_CALL_OR_SYMBOLS:
        if fragment in source:
            raise SystemExit(f"{path.name}: forbidden authority symbol {fragment}")


def _assert_signature(fn) -> None:
    parameters = inspect.signature(fn).parameters
    for forbidden in FORBIDDEN_PARAMETERS:
        if forbidden in parameters:
            raise SystemExit(
                f"{fn.__name__}: caller-controlled authority parameter {forbidden}"
            )


def main() -> None:
    handoff_source = _source(HANDOFF_PATH)
    review_source = _source(REVIEW_PATH)
    _assert_no_forbidden_surface(HANDOFF_PATH, handoff_source)
    _assert_no_forbidden_surface(REVIEW_PATH, review_source)

    _assert_signature(handoff.latch_paper_execution_risk_handoff)
    _assert_signature(review.prepare_paper_execution_human_review)

    for required in (
        "source_risk_contract_hash",
        "seal_fresh_at_handoff",
        "risk_decision_window_retained",
        'capital_authority != "NONE"',
        'live_trading != "BLOCKED"',
    ):
        if required not in handoff_source:
            raise SystemExit(f"risk handoff missing permanent invariant: {required}")

    for required in (
        "CryptoOperatorDecisionContext.from_prepared_package",
        "crypto_operator_confirmation_challenge",
        "MIN_HUMAN_APPROVAL_REMAINING",
        'operator_decision_status != "NOT_ISSUED"',
        "operator_decision_issued is not False",
        "operator_decision_consumed is not False",
        "external_post_authorized is not False",
        'capital_authority != "NONE"',
        'live_trading != "BLOCKED"',
        'next_action != "HUMAN_OPERATOR_APPROVAL_REQUIRED"',
    ):
        if required not in review_source:
            raise SystemExit(f"human review missing permanent invariant: {required}")

    if "SQLite" in review_source:
        raise SystemExit("W87-D human review must remain persistence-free")
    if "OrderManagementSystem" in review_source or "CryptoPaperCanaryCoordinator" in review_source:
        raise SystemExit("W87-D human review must not own OMS/coordinator authority")

    print(
        "AUTO-TRADE W87 PAPER human-review handoff boundary: PASS "
        "(fresh W86/W87-B latch only; retained window is exact original RiskDecision; "
        "R6 package-bound challenge only; >=5s human window; no approval issuance, "
        "decision consume, SQLite, OMS staging, credentials, network, writer, POST, "
        "capital or LIVE authority)"
    )


if __name__ == "__main__":
    main()
