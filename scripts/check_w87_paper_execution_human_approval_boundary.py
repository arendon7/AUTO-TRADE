from __future__ import annotations

import ast
import inspect
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mac_crypto_first_canary_approval as canonical_issuer
from scripts import w87_issue_paper_execution_human_approval as wrapper


REVIEW = ROOT / "src/autotrade/paper_execution_human_review.py"
ISSUER = ROOT / "scripts/mac_crypto_first_canary_approval.py"
WRAPPER = ROOT / "scripts/w87_issue_paper_execution_human_approval.py"

FORBIDDEN_IMPORTS = (
    "requests",
    "urllib",
    "http.client",
    "socket",
    "websockets",
    "alpaca_paper_crypto_writer",
    "first_canary_execution_gate",
    "first_canary_external_post_consent",
)
FORBIDDEN_WRAPPER_SYMBOLS = (
    "record_operator_approval",
    "SQLiteCryptoOperatorDecisionRegistry",
    ".consume(",
    "submit_once",
    "stage_external_submission",
    "FinalGuard",
    "OrderManagementSystem",
    "AlpacaPaperCredentials",
)


def _source(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"missing W87-E surface: {path}")
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


def main() -> None:
    review_source = _source(REVIEW)
    issuer_source = _source(ISSUER)
    wrapper_source = _source(WRAPPER)

    for path, source in ((ISSUER, issuer_source), (WRAPPER, wrapper_source)):
        imports = _imports(source)
        for fragment in FORBIDDEN_IMPORTS:
            if any(fragment in value for value in imports):
                raise SystemExit(f"{path.name}: forbidden network/execution import {fragment}")

    for fragment in FORBIDDEN_WRAPPER_SYMBOLS:
        if fragment in wrapper_source:
            raise SystemExit(f"W87-E wrapper owns forbidden low-level authority: {fragment}")

    if 'attempt_id = f"first-canary-{preparation.receipt.receipt_hash[:32]}"' not in review_source:
        raise SystemExit("W87-D does not derive canonical R6 first-canary attempt identity")
    if "_ATTEMPT_RE.fullmatch(self.attempt_id)" not in review_source:
        raise SystemExit("W87-D receipt does not permanently validate first-canary attempt identity")

    for anchor in (
        "registry.get(context.preparation_hash)",
        "except KeyError:",
        "registry.record_operator_approval(",
        "state.decision.context != context",
        "state.decision.operator_id != operator",
        "state.consumed_at is not None",
        "not state.decision.is_valid_at(instant)",
    ):
        if anchor not in issuer_source:
            raise SystemExit(f"canonical issuer crash-recovery anchor missing: {anchor}")
    if issuer_source.count("record_operator_approval(") != 1:
        raise SystemExit("canonical issuer must retain exactly one audited mint call site")

    for anchor in (
        "from scripts import mac_crypto_first_canary_approval as canonical_issuer",
        "FirstCanaryAttemptWorkspace.open",
        "canonical_issuer.issue_approval(",
        "review.operator_context.to_dict()",
        '"decision_status": "ISSUED"',
        '"decision_consumed": False',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    ):
        if anchor not in wrapper_source:
            raise SystemExit(f"W87-E wrapper invariant missing: {anchor}")

    parameters = inspect.signature(wrapper.issue_w87_human_approval).parameters
    for forbidden in ("now", "credentials", "writer", "transport", "runtime", "live", "environment"):
        if forbidden in parameters:
            raise SystemExit(f"W87-E wrapper exposes forbidden caller authority: {forbidden}")
    if tuple(parameters) != ("workspace_path", "review", "operator_id", "confirmation"):
        raise SystemExit("W87-E wrapper signature changed from exact human-approval surface")

    if canonical_issuer.WRITE_ENV != "R6_EXTERNAL_PAPER_WRITE":
        raise SystemExit("canonical issuer write-enablement isolation changed")

    print(
        "AUTO-TRADE W87 PAPER human approval boundary: PASS "
        "(canonical first-canary identity; sole audited R6 issuer; restart-safe receipt recovery "
        "from exact durable ISSUED state; approval remains unconsumed; no Final Guard consume, "
        "OMS staging, credentials, network, writer, POST, capital or LIVE authority)"
    )


if __name__ == "__main__":
    main()
