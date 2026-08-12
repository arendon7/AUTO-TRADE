from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalWorkspace,
    read_prepared_package,
)
from autotrade.brokers.alpaca_paper_operational_execute import (
    PaperOperationalExecutionRuntime,
)
from autotrade.brokers.alpaca_paper_writer import (
    AlpacaPaperSingleShotWriter,
    AlpacaPaperWriterConfig,
)


KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
WRITE_ENABLE_ENV = "R6_EXTERNAL_PAPER_WRITE"
WRITE_ENABLE_VALUE = "ENABLED"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly one previously prepared and human-approved Alpaca PAPER canary. "
            "This command cannot prepare or approve a canary and can never target LIVE."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--execute-paper-canary",
        action="store_true",
        help="Explicitly enable the one-shot PAPER execution path for this invocation.",
    )
    return parser


def _open_workspace(root: Path) -> PaperOperationalWorkspace:
    if not isinstance(root, Path):
        raise SystemExit("workspace path must be pathlib.Path")
    if not root.exists():
        raise SystemExit("workspace does not exist")
    if root.is_symlink():
        raise SystemExit("workspace cannot be a symlink")
    if not root.is_dir():
        raise SystemExit("workspace must be a directory")
    return PaperOperationalWorkspace(root=root.resolve())


def _credentials_from_environment() -> AlpacaPaperCredentials:
    key_id = os.environ.get(KEY_ENV, "")
    secret_key = os.environ.get(SECRET_ENV, "")
    if not key_id or not secret_key:
        raise SystemExit(
            f"PAPER credentials must be provided only through {KEY_ENV} and {SECRET_ENV}"
        )
    try:
        return AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)
    except ValueError as exc:
        raise SystemExit(f"PAPER credentials are invalid: {exc}") from exc


def _execution_challenge(*, attempt_id: str, package_hash: str) -> str:
    return f"EXECUTE PAPER CANARY {attempt_id} {package_hash}"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute_paper_canary:
        raise SystemExit("PAPER execution is disabled; --execute-paper-canary is required")
    if os.environ.get(WRITE_ENABLE_ENV) != WRITE_ENABLE_VALUE:
        raise SystemExit(
            f"PAPER execution is disabled; set {WRITE_ENABLE_ENV}={WRITE_ENABLE_VALUE} for this invocation"
        )
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("external PAPER execution requires an interactive TTY")

    workspace = _open_workspace(args.workspace)
    package = read_prepared_package(workspace.prepared_package_path)
    print(
        "\n".join(
            (
                "R6 EXTERNAL PAPER CANARY — FINAL EXECUTION",
                "Environment: PAPER",
                f"Order ID: {package.order_id}",
                f"Client order ID: {package.client_order_id}",
                f"Notional: USD {package.notional}",
                f"Attempt ID: {package.attempt_id}",
                f"Prepared package: {package.package_hash}",
                "This command may send exactly one real order to the Alpaca PAPER account.",
                "LIVE trading remains blocked.",
            )
        )
    )
    challenge = _execution_challenge(
        attempt_id=package.attempt_id,
        package_hash=package.package_hash,
    )
    print(f"Type exactly: {challenge}")
    entered = input("> ")
    if entered != challenge:
        raise SystemExit("execution confirmation challenge did not match; no PAPER order sent")

    # Secrets and the network-enabled writer are materialized only after all
    # three explicit gates: CLI flag, dedicated environment enable, and exact
    # interactive challenge. This script never performs preparation or approval.
    credentials = _credentials_from_environment()
    writer = AlpacaPaperSingleShotWriter(
        config=AlpacaPaperWriterConfig(enabled=True),
    )
    runtime = PaperOperationalExecutionRuntime(
        workspace=workspace,
        writer=writer,
    )
    result = runtime.execute_once(
        credentials=credentials,
        now=datetime.now(timezone.utc),
    )
    print(
        json.dumps(
            {
                "environment": "PAPER",
                "order_id": result.submit.order_id,
                "client_order_id": result.submit.client_order_id,
                "attempt_id": result.submit.attempt_id,
                "http_status": result.submit.http_status,
                "request_id": result.submit.request_id,
                "broker_order_id": result.submit.broker_order_id,
                "provisionally_accepted": result.submit.provisionally_accepted,
                "durable_submission_status": result.submit.durable_status.value,
                "reconciliation_required": result.submit.reconciliation_required,
                "capital_authority": "NONE",
                "profitability_claim": False,
                "live_trading": "BLOCKED",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
