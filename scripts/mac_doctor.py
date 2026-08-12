from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys

from autotrade.brokers.alpaca_paper_market_readiness import inspect_market_aware_readiness


EXPECTED_BRANCH = "reconstruction/r6-external-paper-protection"
WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
WRITE_ENABLED = "ENABLED"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local-only AUTO-TRADE Mac doctor. No broker network or execution authority."
    )
    parser.add_argument("--workspace", type=Path)
    return parser


def _git_branch(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    branch = result.stdout.strip()
    return branch or None


def _build_source_head(root: Path) -> str | None:
    path = root / "MAC_BUILD_INFO.txt"
    if not path.is_file() or path.is_symlink():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("source_head="):
            value = line.split("=", 1)[1].strip()
            if len(value) == 40 and all(ch in "0123456789abcdef" for ch in value.lower()):
                return value
    return None


def _env_file_mode(root: Path) -> str:
    path = root / ".env"
    if not path.exists():
        return "ABSENT"
    if path.is_symlink() or not path.is_file():
        return "UNSAFE"
    mode = stat.S_IMODE(path.stat().st_mode)
    return "SAFE_0600" if mode & 0o077 == 0 else f"TOO_OPEN_{mode:04o}"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    branch = _git_branch(root)
    source_head = _build_source_head(root)
    packaged = branch is None and source_head is not None
    provenance_mode = "CERTIFIED_PACKAGE" if packaged else ("GIT_BRANCH" if branch else "DETACHED_SOURCE")
    branch_matches_r6 = branch == EXPECTED_BRANCH or packaged
    write_enabled = os.environ.get(WRITE_ENV) == WRITE_ENABLED

    report: dict[str, object] = {
        "platform": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "python_supported": sys.version_info >= (3, 12),
        "repository": str(root),
        "git_branch": branch,
        "expected_r6_branch": EXPECTED_BRANCH,
        "branch_matches_r6": branch_matches_r6,
        "provenance_mode": provenance_mode,
        "package_source_head": source_head,
        "package_source_verified": packaged,
        "env_file": _env_file_mode(root),
        "paper_key_environment": "SET" if os.environ.get(KEY_ENV) else "NOT_SET",
        "paper_secret_environment": "SET" if os.environ.get(SECRET_ENV) else "NOT_SET",
        "external_paper_write_gate": "ENABLED" if write_enabled else "DISABLED",
        "broker_network_used": False,
        "broker_write_performed": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }

    ok = True
    if platform.system() != "Darwin":
        report["platform_warning"] = "Mac rehearsal expects Darwin"
        ok = False
    if sys.version_info < (3, 12):
        report["python_warning"] = "Python 3.12+ required"
        ok = False
    if write_enabled:
        report["write_gate_warning"] = (
            "Disable R6_EXTERNAL_PAPER_WRITE before installation/readiness rehearsal"
        )
        ok = False
    if report["env_file"] == "UNSAFE" or str(report["env_file"]).startswith("TOO_OPEN_"):
        report["env_file_warning"] = "Use a regular .env with chmod 600"
        ok = False

    if args.workspace is not None:
        try:
            report["workspace_readiness"] = inspect_market_aware_readiness(
                root=args.workspace,
                now=datetime.now(timezone.utc),
            )
        except Exception as exc:  # diagnostic boundary: message only, no authority fallback
            report["workspace_readiness_error"] = f"{type(exc).__name__}: {exc}"
            ok = False

    report["doctor_status"] = "PASS" if ok else "ATTENTION_REQUIRED"
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
