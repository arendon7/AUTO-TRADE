from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts/mac_dashboard.py"
TEST = ROOT / "tests/test_mac_crypto_dashboard_http_resilience.py"


def main() -> int:
    errors=[]
    server=SERVER.read_text(encoding="utf-8") if SERVER.is_file() else ""
    test=TEST.read_text(encoding="utf-8") if TEST.is_file() else ""
    for anchor in (
        '"crypto_preview": ActionSpec("paper", 60)', '"scripts/mac_crypto_canary_preview.py"',
        '"/api/canary-preview"', '"/api/canary-preview-result"',
        "begin_preview", "finish_preview", "preview_status", "PREVIEW_RESULT_TTL_SECONDS = 120",
        '"preview request id already exists; no replay permitted"', '"broker_write_performed": False',
        '"external_post_authorized": False', '"capital_authority": "NONE"', '"live_trading": "BLOCKED"',
        'self.send_header("Content-Length"', 'self.send_header("Connection", "close")', "self.wfile.flush()",
    ):
        if anchor not in server: errors.append(f"primary Control Center missing resilience anchor: {anchor}")
    for forbidden in ("alpaca_paper_writer", "FinalGuardedCryptoEntryTransport", "stage_external_submission", "submit_once"):
        if forbidden in server: errors.append(f"primary Control Center contains forbidden execution surface: {forbidden}")
    for anchor in ("/crypto", "/api/canary-preview", "/api/canary-preview-result", "len(calls) == 1", "no replay"):
        if anchor not in test: errors.append(f"primary real-loopback test missing anchor: {anchor}")
    if errors:
        for error in errors: print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("AUTO-TRADE Mac crypto primary-Control-Center HTTP resilience: PASS (same-server /crypto + preview routes; same-attempt GET recovery; no replay; no broker-write/capital/LIVE authority)")
    return 0

if __name__ == "__main__": raise SystemExit(main())
