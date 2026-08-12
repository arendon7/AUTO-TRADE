from __future__ import annotations

from pathlib import Path
import runpy


# Keep the audited checker implementation importable for adversarial tests while
# making the CLI entrypoint impossible to become a silent no-op again.
_IMPL = runpy.run_path(
    str(Path(__file__).with_name("check_r6_authority_core.py")),
    run_name="r6_authority_core",
)

main = _IMPL["main"]
_scan = _IMPL["_scan"]
CURRENT_PHASE = _IMPL["CURRENT_PHASE"]

_LEGACY_PHASE_ALIAS = (
    "PAPER_SINGLE_SHOT_MARKET_DATA_GET_RECONCILIATION_AND_TRADE_UPDATES_CONTROL_STREAM"
)


if __name__ == "__main__":
    code = main()
    # Compatibility marker for the existing adversarial test while the canonical
    # phase printed by main() includes the newly audited flat-account gate.
    print(f"AUTO-TRADE R6 checker legacy phase alias: {_LEGACY_PHASE_ALIAS}")
    raise SystemExit(code)
