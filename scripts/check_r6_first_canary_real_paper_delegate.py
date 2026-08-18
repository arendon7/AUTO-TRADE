from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CONSENT = ROOT / "src/autotrade/first_canary_external_post_consent.py"
WRAPPER = ROOT / "src/autotrade/first_canary_real_paper_execution.py"
CLI = ROOT / "scripts/mac_crypto_first_canary_execute_real_paper.py"
SAFE_DASHBOARD = ROOT / "scripts/mac_first_canary_dashboard.py"
SAFE_PAGE = ROOT / "web/mac_first_canary.html"
GENERIC_SURFACES = (
    ROOT / "scripts/mac_dashboard.py",
    ROOT / "scripts/mac_crypto_dashboard.py",
    ROOT / "ABRIR_AUTO_TRADE.command",
    ROOT / "ABRIR_CRYPTO_PAPER.command",
)
NETWORK_ROOTS = {"http", "urllib", "socket", "ssl", "requests", "httpx", "aiohttp", "websocket", "websockets"}


def fail(message: str) -> None:
    print(f"first-canary real PAPER delegate: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def require(text: str, tokens: tuple[str, ...], label: str) -> None:
    for token in tokens:
        if token not in text:
            fail(f"{label} missing authority anchor: {token}")


def main() -> int:
    for path in (CONSENT, WRAPPER, CLI, SAFE_DASHBOARD, SAFE_PAGE):
        if not path.is_file():
            fail(f"missing required file: {path.relative_to(ROOT)}")

    consent = CONSENT.read_text(encoding="utf-8")
    wrapper = WRAPPER.read_text(encoding="utf-8")
    cli = CLI.read_text(encoding="utf-8")
    safe_dashboard = SAFE_DASHBOARD.read_text(encoding="utf-8")
    safe_page = SAFE_PAGE.read_text(encoding="utf-8")

    for path in (CONSENT, WRAPPER):
        roots = {module.split(".", 1)[0] for module in imports(path) if module}
        forbidden = roots & NETWORK_ROOTS
        if forbidden:
            fail(f"{path.name} imports raw network stack: {sorted(forbidden)}")

    require(
        consent,
        (
            'CONSENT_FILENAME = "external_post_consent.json"',
            "CONSENT_TTL = timedelta(seconds=10)",
            'DOCUMENT_TYPE = "R6_CRYPTO_PAPER_FIRST_CANARY_EXTERNAL_POST_CONSENT"',
            'self.symbol != "BTC/USD"',
            'self.notional < Decimal("1") or self.notional > Decimal("5")',
            "self.source_host != ALPACA_PAPER_TRADING_HOST",
            "self.source_path != CRYPTO_ORDERS_PATH",
            '"EXECUTE ONCE PAPER BTC/USD USD "',
            "attempt.assert_unexecuted()",
            "if consent_path.exists():",
            '"external PAPER POST consent was already consumed; POST replay is forbidden"',
            'broker_payload.get("side") != "buy"',
            'broker_payload.get("type") != "limit"',
            'broker_payload.get("time_in_force") != "ioc"',
            'package.get("network_write_authorized") is not False',
            '"one_shot": True',
            '"retry_authorized": False',
            '"credentials_persisted": False',
            '"secret_persisted": False',
            '"exact_paper_post_authorized": True',
            '"live_trading": "BLOCKED"',
            "attempt.write_once(path=consent_path",
        ),
        "consent latch",
    )
    for forbidden in (
        "HttpsAlpacaPaperCryptoWriteTransport",
        "AlpacaPaperCryptoWriter",
        "submit_once(",
        ".write(",
        ".post(",
        "api.alpaca.markets",
        "APCA_API_SECRET_KEY",
    ):
        if forbidden in consent:
            fail(f"consent latch leaked broker/network authority: {forbidden}")

    require(
        wrapper,
        (
            "FirstCanaryPreparedEvidence.from_document(nested)",
            "PreparedCryptoPaperCanaryPackage(",
            "AlpacaPaperCryptoOrderRequest(",
            'if broker_order.fingerprint != package.crypto_order_fingerprint:',
            'if broker_order.payload_hash != package.crypto_order_payload_hash:',
            "AlpacaPaperAccountGateway(config=config)",
            "AlpacaPaperCryptoAssetGateway(config=config)",
            "AlpacaPaperFlatAccountGateway(config=config)",
            "AlpacaPaperCryptoMarketDataGateway(",
            'symbol="BTC/USD"',
            "consume_external_post_consent(",
            "require_fresh_external_post_consent(receipt=consent",
            "effective_delegate = delegate or HttpsAlpacaPaperCryptoWriteTransport()",
            "execute_first_canary_once(",
            "delegate=effective_delegate",
            "reconciler=effective_reconciler",
        ),
        "real PAPER wrapper",
    )
    if wrapper.count("HttpsAlpacaPaperCryptoWriteTransport()") != 1:
        fail("real PAPER wrapper must have exactly one audited HTTPS delegate construction site")
    if wrapper.count("execute_first_canary_once(") != 1:
        fail("real PAPER wrapper must cross the certified orchestrator exactly once")
    sequence = (
        wrapper.find("final = final_evidence or collect_fresh_final_evidence("),
        wrapper.find("consent = consume_external_post_consent("),
        wrapper.find("require_fresh_external_post_consent(receipt=consent"),
        wrapper.find("effective_delegate = delegate or HttpsAlpacaPaperCryptoWriteTransport()"),
        wrapper.find("outcome = execute_first_canary_once("),
    )
    if any(index < 0 for index in sequence) or tuple(sorted(sequence)) != sequence:
        fail("required order is final GET evidence -> durable consent -> freshness -> audited delegate -> certified orchestrator")
    for forbidden in (
        "http.client",
        "urllib.request",
        "requests.",
        "httpx.",
        "api.alpaca.markets",
        "R6_LIVE",
    ):
        if forbidden in wrapper:
            fail(f"real PAPER wrapper leaked raw/LIVE authority: {forbidden}")

    require(
        cli,
        (
            'WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"',
            'parser.add_argument("--allow-exact-paper-post", action="store_true")',
            "if not args.allow_exact_paper_post:",
            'if os.environ.get(WRITE_ENV) == "ENABLED":',
            '"generic R6_EXTERNAL_PAPER_WRITE must remain disabled',
            "confirmation=_confirmation_from_stdin()",
            "execute_real_paper_first_canary_once(",
            '"retry_post": False',
            '"reconciliation_get_only": True',
            '"credentials_persisted": False',
            '"secret_persisted": False',
            '"live_trading": "BLOCKED"',
        ),
        "real PAPER CLI",
    )
    for forbidden in ("http.client", "urllib.request", "requests.", "api.alpaca.markets", ".post("):
        if forbidden in cli:
            fail(f"real PAPER CLI bypasses audited delegate: {forbidden}")

    for text, label in ((safe_dashboard, SAFE_DASHBOARD.name), (safe_page, SAFE_PAGE.name)):
        for forbidden in (
            "first_canary_real_paper_execution",
            "mac_crypto_first_canary_execute_real_paper.py",
            "external_post_consent",
        ):
            if forbidden in text:
                fail(f"PR41 no-POST surface acquired real execution authority: {label}: {forbidden}")
    if '"real_execution_enabled": False' not in safe_dashboard:
        fail("PR41 no-POST dashboard no longer advertises real execution disabled")

    for path in GENERIC_SURFACES:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            "first_canary_real_paper_execution",
            "mac_crypto_first_canary_execute_real_paper.py",
            "FirstCanaryExternalPostConsent",
        ):
            if forbidden in text:
                fail(f"generic Mac surface acquired first-canary real POST authority: {path.name}: {forbidden}")

    print(
        "first-canary real PAPER delegate: PASS — exact BTC/USD BUY LIMIT IOC USD1-5; "
        "fresh GET-only evidence precedes durable second consent; one audited HTTPS delegate construction site; "
        "certified execution gate owns UNKNOWN/replay protection; PR41 safe dashboard and generic Control Center remain no-POST; LIVE blocked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
