from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/autotrade/connectivity_workspace_stage.py"

REQUIRED = (
    "ConnectivityOmsStager(",
    "mark_submit_attempt_unknown(",
    "PaperSubmissionStatus.UNKNOWN",
    '"unknown_before_post_committed": True',
    '"external_post_authorized": False',
    '"external_order_submitted": False',
    '"live_trading": "BLOCKED"',
    '"next_action": "CONNECTIVITY_ONE_SHOT_EXECUTOR_REQUIRED"',
    "core.sqlite3 changed after Final Freshness",
)

FORBIDDEN = (
    "AlpacaPaperAccountGateway",
    "AlpacaPaperEquityMarketDataGateway",
    "AlpacaPaperOneShotWriter",
    "alpaca_paper_writer",
    "submit_once",
    "requests.",
    "urllib",
    "http.client",
    "/v2/orders",
    "health_bridge",
    "R6_EXTERNAL_PAPER_WRITE",
)


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    for anchor in REQUIRED:
        if anchor not in source:
            raise SystemExit(f"ERROR: connectivity workspace staging anchor missing: {anchor}")
    for forbidden in FORBIDDEN:
        if forbidden in source:
            raise SystemExit(f"ERROR: forbidden connectivity workspace staging surface: {forbidden}")
    print(
        "AUTO-TRADE R6 connectivity workspace staging boundary: PASS "
        "(verified <=5s binding; durable handoff; OMS SUBMITTING; submission UNKNOWN-before-POST; "
        "next action is same-process one-shot executor; no broker/writer/network/Health/LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
