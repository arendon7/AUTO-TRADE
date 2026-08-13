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
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"


class SafeConsoleError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Safe Mac operator console for AUTO-TRADE R6. This console exposes only "
            "local setup/diagnostics/rehearsal, explicit GET-only PAPER discovery/preflights, "
            "credential-free candidate/preparation/review artifacts and read-only pre-canary status. "
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

    readiness = sub.add_parser("readiness", help="Inspect legacy workspace readiness read-only; no broker I/O.")
    readiness.add_argument("--workspace", required=True, type=Path)

    pre_status = sub.add_parser(
        "pre-canary-status",
        help=(
            "Read the current CONNECTIVITY_CANARY gate chain locally. READY means only "
            "ready for the named next gate; never POST authority."
        ),
    )
    pre_status.add_argument("--workspace", required=True, type=Path)

    discovery = sub.add_parser(
        "account-discovery",
        help=(
            "Identify the PAPER account behind the supplied PAPER credentials with one GET /v2/account. "
            "Creates no durable attestation and never writes an order."
        ),
    )
    discovery.add_argument("--workspace", required=True, type=Path)
    discovery.add_argument("--allow-paper-account-discovery-read", action="store_true")

    account = sub.add_parser(
        "account-preflight",
        help="Explicit single GET /v2/account PAPER preflight. Never writes an order.",
    )
    account.add_argument("--workspace", required=True, type=Path)
    account.add_argument("--expected-account-id", required=True)
    account.add_argument("--allow-paper-account-read", action="store_true")

    asset = sub.add_parser(
        "asset-preflight",
        help="Explicit single GET /v2/assets/{symbol} PAPER preflight. Never writes an order.",
    )
    asset.add_argument("--workspace", required=True, type=Path)
    asset.add_argument("--symbol", required=True)
    asset.add_argument("--allow-paper-asset-read", action="store_true")

    flat = sub.add_parser(
        "flat-account-preflight",
        help="Explicit GET-only first-canary flatness check. Never mutates broker state.",
    )
    flat.add_argument("--workspace", required=True, type=Path)
    flat.add_argument("--allow-paper-flat-account-read", action="store_true")

    market = sub.add_parser(
        "market-preflight",
        help="Explicit single GET IEX market snapshot preflight. Never writes an order.",
    )
    market.add_argument("--workspace", required=True, type=Path)
    market.add_argument("--symbol", required=True)
    market.add_argument("--allow-paper-market-read", action="store_true")

    candidate = sub.add_parser(
        "build-connectivity-candidate",
        help=(
            "Build a local OMS VALIDATED CONNECTIVITY_CANARY candidate from existing GET evidence. "
            "Credentials are stripped; no Strategy Health, operator authority or POST is created."
        ),
    )
    candidate.add_argument("--workspace", required=True, type=Path)

    preparation = sub.add_parser(
        "prepare-connectivity-candidate",
        help=(
            "Build the credential-free deterministic bracket/preparation package from an existing candidate. "
            "No broker I/O, human authority, OMS staging or POST."
        ),
    )
    preparation.add_argument("--workspace", required=True, type=Path)

    review = sub.add_parser(
        "review-receipt",
        help=(
            "Freeze the exact offline operator review receipt after the first human decision. "
            "No broker I/O, new human authority, OMS staging or POST."
        ),
    )
    review.add_argument("--workspace", required=True, type=Path)
    return parser


def _require_safe_shell() -> None:
    if os.environ.get(WRITE_ENV) == WRITE_ENABLED:
        raise SafeConsoleError(
            "Refusing Safe Console while R6_EXTERNAL_PAPER_WRITE=ENABLED. "
            "Run: export R6_EXTERNAL_PAPER_WRITE=DISABLED"
        )
    if not PYTHON.is_file():
        raise SafeConsoleError("Missing .venv. Run first: bash scripts/mac_bootstrap.sh")


def _child_env(*, credential_free: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    env[WRITE_ENV] = "DISABLED"
    if credential_free:
        env.pop(KEY_ENV, None)
        env.pop(SECRET_ENV, None)
    return env


def _run(argv: list[str], *, credential_free: bool = False) -> int:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        env=_child_env(credential_free=credential_free),
        check=False,
    )
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
            [str(PYTHON), "scripts/mac_create_workspace.py", "--workspace", str(args.workspace)],
            credential_free=True,
        )
    if args.command == "doctor":
        command = [str(PYTHON), "scripts/mac_doctor.py"]
        if args.workspace is not None:
            command.extend(["--workspace", str(args.workspace)])
        return _run(command, credential_free=True)
    if args.command == "rehearsal":
        return _run(["bash", "scripts/mac_rehearsal.sh"], credential_free=True)
    if args.command == "safety-rehearsal":
        command = [
            str(PYTHON), "scripts/mac_safety_rehearsal.py",
            "--symbol", args.symbol, "--side", args.side,
            "--quantity", args.quantity, "--limit-price", args.limit_price,
            "--market-age-ms", str(args.market_age_ms),
        ]
        if args.reconciliation_failed:
            command.append("--reconciliation-failed")
        if args.broker_state_unknown:
            command.append("--broker-state-unknown")
        if args.kill_switch:
            command.append("--kill-switch")
        return _run(command, credential_free=True)
    if args.command == "readiness":
        return _run(
            [str(PYTHON), "scripts/r6_inspect_paper_readiness.py", "--workspace", str(args.workspace)],
            credential_free=True,
        )
    if args.command == "pre-canary-status":
        return _run(
            [str(PYTHON), "scripts/r6_precanary_status.py", "--workspace", str(args.workspace)],
            credential_free=True,
        )
    if args.command == "account-discovery":
        if not args.allow_paper_account_discovery_read:
            print(
                "SAFE CONSOLE BLOCKED: account discovery GET requires "
                "--allow-paper-account-discovery-read",
                file=sys.stderr,
            )
            return 2
        return _run([
            str(PYTHON), "scripts/r6_external_paper_account_discovery.py",
            "--workspace", str(args.workspace),
            "--allow-paper-account-discovery-read",
        ])
    if args.command == "account-preflight":
        if not args.allow_paper_account_read:
            print("SAFE CONSOLE BLOCKED: account GET requires --allow-paper-account-read", file=sys.stderr)
            return 2
        return _run([
            str(PYTHON), "scripts/r6_external_paper_preflight.py",
            "--workspace", str(args.workspace), "--expected-account-id", args.expected_account_id,
            "--allow-paper-account-read",
        ])
    if args.command == "asset-preflight":
        if not args.allow_paper_asset_read:
            print("SAFE CONSOLE BLOCKED: asset GET requires --allow-paper-asset-read", file=sys.stderr)
            return 2
        return _run([
            str(PYTHON), "scripts/r6_external_paper_asset_preflight.py",
            "--workspace", str(args.workspace), "--symbol", args.symbol,
            "--allow-paper-asset-read",
        ])
    if args.command == "flat-account-preflight":
        if not args.allow_paper_flat_account_read:
            print("SAFE CONSOLE BLOCKED: flat-account GETs require --allow-paper-flat-account-read", file=sys.stderr)
            return 2
        return _run([
            str(PYTHON), "scripts/r6_external_paper_flat_account_preflight.py",
            "--workspace", str(args.workspace), "--allow-paper-flat-account-read",
        ])
    if args.command == "market-preflight":
        if not args.allow_paper_market_read:
            print("SAFE CONSOLE BLOCKED: market GET requires --allow-paper-market-read", file=sys.stderr)
            return 2
        return _run([
            str(PYTHON), "scripts/r6_external_paper_market_preflight.py",
            "--workspace", str(args.workspace), "--symbol", args.symbol,
            "--allow-paper-market-read",
        ])
    if args.command == "build-connectivity-candidate":
        return _run(
            [str(PYTHON), "scripts/r6_build_connectivity_candidate.py", "--workspace", str(args.workspace)],
            credential_free=True,
        )
    if args.command == "prepare-connectivity-candidate":
        return _run(
            [str(PYTHON), "scripts/r6_prepare_connectivity_candidate.py", "--workspace", str(args.workspace)],
            credential_free=True,
        )
    if args.command == "review-receipt":
        return _run(
            [str(PYTHON), "scripts/r6_connectivity_review_receipt.py", "--workspace", str(args.workspace)],
            credential_free=True,
        )
    raise SafeConsoleError(f"unsupported safe command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
