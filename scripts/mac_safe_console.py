from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
WRITE_ENABLED = "ENABLED"


class SafeConsoleError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Safe Mac operator console for AUTO-TRADE R6. This console exposes only "
            "local setup/diagnostics/rehearsal and explicit GET-only PAPER preflights. "
            "It has no order execution command."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    workspace = sub.add_parser(
        "init-workspace",
        help="Create a private credential-free local PAPER workspace; no broker I/O.",
    )
    workspace.add_argument("--workspace", required=True, type=Path)

    doctor = sub.add_parser("doctor", help="Run local Mac Doctor; no broker I/O.")
    doctor.add_argument("--workspace", type=Path)

    sub.add_parser("rehearsal", help="Run the complete offline rehearsal; no broker I/O.")

    safety = sub.add_parser(
        "safety-rehearsal",
        help=(
            "Evaluate a local candidate through the real Capital Safety Kernel. "
            "No broker, writer, OMS staging or external authority."
        ),
    )
    safety.add_argument("--symbol", default="AAPL")
    safety.add_argument("--side", choices=("BUY", "SELL"), default="BUY")
    safety.add_argument("--quantity", default="0.25")
    safety.add_argument("--limit-price", default="100")
    safety.add_argument("--market-age-ms", type=int, default=250)
    safety.add_argument("--reconciliation-failed", action="store_true")
    safety.add_argument("--broker-state-unknown", action="store_true")
    safety.add_argument("--kill-switch", action="store_true")

    readiness = sub.add_parser("readiness", help="Inspect one workspace read-only; no broker I/O.")
    readiness.add_argument("--workspace", required=True, type=Path)

    account = sub.add_parser(
        "account-preflight",
        help="Explicit single GET /v2/account PAPER preflight. Never writes an order.",
    )
    account.add_argument("--workspace", required=True, type=Path)
    account.add_argument("--expected-account-id", required=True)
    account.add_argument(
        "--allow-paper-account-read",
        action="store_true",
        help="Required explicit opt-in to the account GET.",
    )

    flat = sub.add_parser(
        "flat-account-preflight",
        help=(
            "Explicit GET-only first-canary flatness check for positions and open orders. "
            "Never cancels, liquidates or submits anything."
        ),
    )
    flat.add_argument("--workspace", required=True, type=Path)
    flat.add_argument(
        "--allow-paper-flat-account-read",
        action="store_true",
        help="Required explicit opt-in to the two flat-account GETs.",
    )

    market = sub.add_parser(
        "market-preflight",
        help="Explicit single GET IEX market snapshot preflight. Never writes an order.",
    )
    market.add_argument("--workspace", required=True, type=Path)
    market.add_argument("--symbol", required=True)
    market.add_argument(
        "--allow-paper-market-read",
        action="store_true",
        help="Required explicit opt-in to the IEX GET.",
    )
    return parser


def _require_safe_shell() -> None:
    if os.environ.get(WRITE_ENV) == WRITE_ENABLED:
        raise SafeConsoleError(
            "Refusing Safe Console while R6_EXTERNAL_PAPER_WRITE=ENABLED. "
            "Run: export R6_EXTERNAL_PAPER_WRITE=DISABLED"
        )
    if not PYTHON.is_file():
        raise SafeConsoleError(
            "Missing .venv. Run first: bash scripts/mac_bootstrap.sh"
        )


def _run(argv: list[str]) -> int:
    env = os.environ.copy()
    env[WRITE_ENV] = "DISABLED"
    completed = subprocess.run(argv, cwd=ROOT, env=env, check=False)
    return int(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _require_safe_shell()
    except SafeConsoleError as exc:
        print(f"SAFE CONSOLE BLOCKED: {exc}", file=sys.stderr)
        return 2

    if args.command == "init-workspace":
        return _run(
            [
                str(PYTHON),
                "scripts/mac_create_workspace.py",
                "--workspace",
                str(args.workspace),
            ]
        )

    if args.command == "doctor":
        command = [str(PYTHON), "scripts/mac_doctor.py"]
        if args.workspace is not None:
            command.extend(["--workspace", str(args.workspace)])
        return _run(command)

    if args.command == "rehearsal":
        return _run(["bash", "scripts/mac_rehearsal.sh"])

    if args.command == "safety-rehearsal":
        command = [
            str(PYTHON),
            "scripts/mac_safety_rehearsal.py",
            "--symbol",
            args.symbol,
            "--side",
            args.side,
            "--quantity",
            args.quantity,
            "--limit-price",
            args.limit_price,
            "--market-age-ms",
            str(args.market_age_ms),
        ]
        if args.reconciliation_failed:
            command.append("--reconciliation-failed")
        if args.broker_state_unknown:
            command.append("--broker-state-unknown")
        if args.kill_switch:
            command.append("--kill-switch")
        return _run(command)

    if args.command == "readiness":
        return _run(
            [
                str(PYTHON),
                "scripts/r6_inspect_paper_readiness.py",
                "--workspace",
                str(args.workspace),
            ]
        )

    if args.command == "account-preflight":
        if not args.allow_paper_account_read:
            print(
                "SAFE CONSOLE BLOCKED: account GET requires --allow-paper-account-read",
                file=sys.stderr,
            )
            return 2
        return _run(
            [
                str(PYTHON),
                "scripts/r6_external_paper_preflight.py",
                "--workspace",
                str(args.workspace),
                "--expected-account-id",
                args.expected_account_id,
                "--allow-paper-account-read",
            ]
        )

    if args.command == "flat-account-preflight":
        if not args.allow_paper_flat_account_read:
            print(
                "SAFE CONSOLE BLOCKED: flat-account GETs require --allow-paper-flat-account-read",
                file=sys.stderr,
            )
            return 2
        return _run(
            [
                str(PYTHON),
                "scripts/r6_external_paper_flat_account_preflight.py",
                "--workspace",
                str(args.workspace),
                "--allow-paper-flat-account-read",
            ]
        )

    if args.command == "market-preflight":
        if not args.allow_paper_market_read:
            print(
                "SAFE CONSOLE BLOCKED: market GET requires --allow-paper-market-read",
                file=sys.stderr,
            )
            return 2
        return _run(
            [
                str(PYTHON),
                "scripts/r6_external_paper_market_preflight.py",
                "--workspace",
                str(args.workspace),
                "--symbol",
                args.symbol,
                "--allow-paper-market-read",
            ]
        )

    raise SafeConsoleError(f"unsupported safe command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
