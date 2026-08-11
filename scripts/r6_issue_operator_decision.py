from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from autotrade.brokers.alpaca_paper_core_provenance import (
    PaperCoreProvenanceError,
    PaperOperationalCoreProvenanceReader,
)
from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
    read_prepared_package,
)
from autotrade.brokers.alpaca_paper_operational_prepare import (
    verify_core_provenance_document,
)
from autotrade.brokers.alpaca_paper_operator_decision import (
    PaperOperatorDecisionContext,
    SQLitePaperOperatorDecisionRegistry,
    operator_confirmation_challenge,
)
from autotrade.persistence import SQLiteRuntime


DEFAULT_TTL_SECONDS = 30
MAX_TTL_SECONDS = 120


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Interactively record one explicit human authorization for the exact "
            "offline-prepared Alpaca PAPER canary. This command never submits an order."
        )
    )
    parser.add_argument(
        "--workspace",
        required=True,
        type=Path,
        help="Exact sanitized R6 operational workspace prepared for this canary.",
    )
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    return parser


def _open_workspace(path: Path) -> PaperOperationalWorkspace:
    if not isinstance(path, Path):
        raise SystemExit("--workspace must be a filesystem path")
    if not path.exists():
        raise SystemExit("operational workspace does not exist")
    if path.is_symlink():
        raise SystemExit("operational workspace cannot be a symlink")
    if not path.is_dir():
        raise SystemExit("operational workspace must be a directory")
    return PaperOperationalWorkspace(root=path.resolve())


def _load_context(path: Path) -> PaperOperatorDecisionContext:
    if not path.is_file() or path.is_symlink():
        raise SystemExit("canonical operator-decision context must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read canonical operator-decision context: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit("operator-decision context JSON root must be an object")
    try:
        return PaperOperatorDecisionContext.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"operator-decision context is invalid: {exc}") from exc


def _load_prepared_context(workspace: PaperOperationalWorkspace):
    try:
        package = read_prepared_package(workspace.prepared_package_path)
    except (OSError, TypeError, ValueError, PaperOperationalIntegrityError) as exc:
        raise SystemExit(f"prepared PAPER package is invalid: {exc}") from exc
    context = _load_context(workspace.operator_context_path)
    expected = PaperOperatorDecisionContext.from_prepared_package(package)
    if context != expected:
        raise SystemExit("operator context does not match exact prepared PAPER package")
    return package, context


def _verify_current_core(
    workspace: PaperOperationalWorkspace,
    *,
    package,
    now: datetime,
) -> str:
    try:
        observed = PaperOperationalCoreProvenanceReader(workspace).verify(now=now)
        return verify_core_provenance_document(
            workspace,
            package=package,
            observed=observed,
        )
    except (PaperCoreProvenanceError, PaperOperationalIntegrityError, TypeError, ValueError) as exc:
        raise SystemExit(f"current durable core provenance is not eligible: {exc}") from exc


def _validate_ttl(value: int) -> int:
    if isinstance(value, bool) or value <= 0 or value > MAX_TTL_SECONDS:
        raise SystemExit(f"--ttl-seconds must be between 1 and {MAX_TTL_SECONDS}")
    return value


def _render_summary(context: PaperOperatorDecisionContext, provenance_hash: str) -> str:
    return "\n".join(
        (
            "R6 EXTERNAL PAPER CANARY — HUMAN FINAL DECISION",
            "Environment: PAPER",
            f"Account attestation: {context.account_attestation_fingerprint[:16]}…",
            f"Order ID: {context.order_id}",
            f"Client order ID: {context.client_order_id}",
            f"Notional: USD {context.notional}",
            f"Prepared package: {context.prepared_package_hash}",
            f"Core provenance document: {provenance_hash}",
            f"Attempt ID: {context.attempt_id}",
            "This records authorization only. It does NOT submit an order and does NOT enable LIVE trading.",
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    ttl = _validate_ttl(args.ttl_seconds)
    workspace = _open_workspace(args.workspace)
    package, context = _load_prepared_context(workspace)

    # A piped command, CI job, agent invocation or background process must not
    # silently mint human execution authority. The final decision requires a
    # real interactive terminal and an exact challenge typed after seeing the
    # immutable prepared-package summary.
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("human PAPER authorization requires an interactive TTY")

    first_checked_at = datetime.now(timezone.utc)
    provenance_hash = _verify_current_core(
        workspace,
        package=package,
        now=first_checked_at,
    )
    print(_render_summary(context, provenance_hash))
    challenge = operator_confirmation_challenge(context)
    print(f"Type exactly: {challenge}")
    entered = input("> ")
    if entered != challenge:
        raise SystemExit("operator confirmation challenge did not match; no authorization recorded")

    # The operator may take time to read and type the challenge. Re-read the
    # exact same durable core immediately before minting authority; any DB byte,
    # Safety, OMS, Portfolio, Health or Health Bridge drift forces re-preparation.
    issued_at = datetime.now(timezone.utc)
    second_provenance_hash = _verify_current_core(
        workspace,
        package=package,
        now=issued_at,
    )
    if second_provenance_hash != provenance_hash:
        raise SystemExit("core provenance changed during human decision; no authorization recorded")

    expires_at = issued_at + timedelta(seconds=ttl)
    registry = SQLitePaperOperatorDecisionRegistry(SQLiteRuntime(workspace.operator_db_path))
    state = registry.record_operator_approval(
        context=context,
        operator_id=args.operator_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    print(
        json.dumps(
            {
                "status": state.status.value,
                "preparation_hash": context.preparation_hash,
                "core_provenance_document_hash": provenance_hash,
                "decision_hash": state.decision.decision_hash,
                "operator_id": state.decision.operator_id,
                "issued_at": state.decision.issued_at.isoformat(),
                "expires_at": state.decision.expires_at.isoformat(),
                "external_order_submitted": False,
                "live_trading": "BLOCKED",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
