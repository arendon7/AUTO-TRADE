from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts/mac_crypto_dashboard.py"
HTML = ROOT / "web/mac_crypto_dashboard.html"
TEST = ROOT / "tests/test_mac_crypto_dashboard_http_resilience.py"


def main() -> int:
    errors: list[str] = []
    if not SERVER.is_file():
        errors.append("missing scripts/mac_crypto_dashboard.py")
    else:
        text = SERVER.read_text(encoding="utf-8")
        for anchor in (
            'self.send_header("Content-Length", str(content_length))',
            'self.send_header("Connection", "close")',
            "self.wfile.flush()",
            "except Exception as exc:",
            "HTTPStatus.INTERNAL_SERVER_ERROR",
            "_unexpected_failure(exc, payload)",
            '"diagnostic_id": diagnostic_id',
            '"broker_write_performed": False',
            '"external_post_authorized": False',
            '"operator_approval_authority": "NONE"',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
            'env[WRITE_ENV] = "DISABLED"',
            'PREVIEW_RESULT_TTL_SECONDS = 120',
            'MAX_PREVIEW_RESULTS = 16',
            '"/api/canary-preview-result"',
            "begin_preview(preview_request_id)",
            "finish_preview(preview_request_id, result)",
            "preview_status(raw)",
            '"preview request id already exists; no replay permitted"',
        ):
            if anchor not in text:
                errors.append(f"crypto localhost HTTP resilience anchor missing: {anchor}")
        if 'env[WRITE_ENV] = "ENABLED"' in text:
            errors.append("crypto localhost HTTP layer may never enable PAPER write")

    if not HTML.is_file():
        errors.append("missing crypto dashboard HTML")
    else:
        html = HTML.read_text(encoding="utf-8")
        for anchor in (
            "crypto.getRandomValues(bytes)",
            "body.preview_request_id=requestId",
            'fetch("/api/canary-preview",',
            'fetch("/api/canary-preview-result?request_id="+encodeURIComponent(requestId)',
            "RECUPERANDO MISMO INTENTO · NO REPLAY",
            "d=await recoverPreview(requestId)",
        ):
            if anchor not in html:
                errors.append(f"crypto Safari same-attempt recovery anchor missing: {anchor}")
        if html.count('fetch("/api/canary-preview",') != 1:
            errors.append("preview UI must issue exactly one POST fetch per operator click")

    if not TEST.is_file():
        errors.append("missing localhost HTTP resilience test")
    else:
        test = TEST.read_text(encoding="utf-8")
        for anchor in (
            "HTTPConnection",
            '"/api/canary-preview"',
            "response.status == 500",
            'response.getheader("Content-Length")',
            'decoded["broker_write_performed"] is False',
            'decoded["external_post_authorized"] is False',
            'decoded["capital_authority"] == "NONE"',
            'decoded["live_trading"] == "BLOCKED"',
            "test_preview_result_is_recoverable_by_same_request_id_without_second_post",
            '"/api/canary-preview-result?request_id=',
            'assert calls["count"] == 1',
        ):
            if anchor not in test:
                errors.append(f"crypto localhost HTTP resilience test anchor missing: {anchor}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE Mac crypto localhost HTTP resilience: PASS "
        "(complete response framing; same-attempt Safari recovery by GET; no replay; "
        "fail-closed JSON fallback; no broker-write/capital/LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
