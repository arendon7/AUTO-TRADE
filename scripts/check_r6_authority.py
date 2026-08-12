from __future__ import annotations

from pathlib import Path
import runpy


# Keep the audited implementation importable for adversarial tests while making
# the CLI entrypoint impossible to become a silent no-op again.
_IMPL = runpy.run_path(
    str(Path(__file__).with_name("check_r6_authority_core.py")),
    run_name="r6_authority_core",
)

main = _IMPL["main"]
_scan = _IMPL["_scan"]
CURRENT_PHASE = _IMPL["CURRENT_PHASE"]


if __name__ == "__main__":
    raise SystemExit(main())
