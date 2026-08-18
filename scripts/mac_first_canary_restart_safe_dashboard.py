from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import mac_first_canary_dashboard as safe  # noqa: E402


RESTART_SAFE_PREPARE = ROOT / "scripts/mac_crypto_first_canary_prepare_restart_safe.py"
_BASE_REQUIRE_RUNTIME = safe._require_runtime


class RestartSafeDashboardError(RuntimeError):
    pass


def _require_runtime() -> None:
    _BASE_REQUIRE_RUNTIME()
    if not RESTART_SAFE_PREPARE.is_file():
        raise RestartSafeDashboardError(
            "AUTO-TRADE restart-safe first-canary preparation runtime is missing"
        )


def _prepare_restart_safe(payload: dict[str, object]) -> dict[str, object]:
    workspace = safe._workspace_value(payload.get("workspace"))
    attempt_id = safe._attempt_id(payload.get("attempt_id"))
    credentials = safe._credentials(payload)
    return safe._run_child(
        [
            "scripts/mac_crypto_first_canary_prepare_restart_safe.py",
            "--workspace",
            str(workspace),
            "--attempt-id",
            attempt_id,
            "--allow-paper-crypto-read",
        ],
        credentials=credentials,
        timeout=75,
    )


def main(argv: list[str] | None = None) -> int:
    # Patch only the preparation implementation. The inherited localhost server,
    # approval and recovery surfaces stay identical to the certified no-POST
    # dashboard from PR #41.
    safe._require_runtime = _require_runtime
    safe._prepare = _prepare_restart_safe
    return safe.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
