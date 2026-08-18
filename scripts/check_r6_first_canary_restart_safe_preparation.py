from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CODEC = ROOT / "src/autotrade/first_canary_prepared_evidence.py"
WRAPPER = ROOT / "scripts/mac_crypto_first_canary_prepare_restart_safe.py"
BASE_PREPARE = ROOT / "scripts/mac_crypto_first_canary_prepare.py"
DIRECT_NETWORK_ROOTS = {
    "http",
    "urllib",
    "socket",
    "ssl",
    "requests",
    "httpx",
    "aiohttp",
    "websocket",
    "websockets",
}
FORBIDDEN_WRITE_TOKENS = (
    "AlpacaPaperCryptoWriter",
    "HttpsAlpacaPaperCryptoWriteTransport",
    "GuardedAlpacaPaperCryptoWriteTransport",
    "submit_once(",
    ".post(",
    'method="POST"',
    "method='POST'",
    "stage_external_submission",
    "stage_cold_start_external_submission",
    "api.alpaca.markets",
)


def fail(message: str) -> None:
    print(f"first-canary restart-safe preparation boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def main() -> int:
    for path in (CODEC, WRAPPER, BASE_PREPARE):
        if not path.is_file():
            fail(f"missing required preparation surface: {path.relative_to(ROOT)}")

    for path in (CODEC, WRAPPER):
        roots = {module.split(".", 1)[0] for module in _imports(path) if module}
        forbidden = roots & DIRECT_NETWORK_ROOTS
        if forbidden:
            fail(f"{path.name} imports forbidden direct network stack: {sorted(forbidden)}")
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_WRITE_TOKENS:
            if token in text:
                fail(f"{path.name} contains forbidden write/LIVE authority: {token}")

    codec = CODEC.read_text(encoding="utf-8")
    for anchor in (
        'DOCUMENT_TYPE = "R6_CRYPTO_PAPER_FIRST_CANARY_PREPARED_EVIDENCE"',
        "class FirstCanaryPreparedEvidence:",
        "def canonical_payload(self) -> dict[str, object]:",
        "def document(self) -> dict[str, object]:",
        "def from_document(cls, document: Mapping[str, object])",
        '"credentials_persisted": False',
        '"secret_persisted": False',
        '"live_trading": "BLOCKED"',
        'raw.get("credentials_persisted") is not False',
        'raw.get("secret_persisted") is not False',
        'raw.get("live_trading") != "BLOCKED"',
        '"prepared_evidence_hash"',
        '"risk_decision_fingerprint"',
        '"market_attestation_fingerprint"',
    ):
        if anchor not in codec:
            fail(f"prepared-evidence codec missing integrity anchor: {anchor}")
    for forbidden in (
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "secret_key",
        '"paper_secret"',
        "os.environ",
    ):
        if forbidden in codec:
            fail(f"prepared-evidence codec may not access or persist credential secret material: {forbidden}")

    wrapper = WRAPPER.read_text(encoding="utf-8")
    for anchor in (
        'BASE_PREPARE = ROOT / "scripts/mac_crypto_first_canary_prepare.py"',
        'EVIDENCE_FILENAME = "prepared_evidence.json"',
        'os.environ.get(WRITE_ENV) == "ENABLED"',
        'namespace["prepare_first_canary"]',
        "FirstCanaryPreparedEvidence(",
        "evidence.document()",
        '"credentials_persisted": False',
        '"secret_persisted": False',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"live_trading": "BLOCKED"',
        "attempt.write_once(",
        "attempt.read(",
    ):
        if anchor not in wrapper:
            fail(f"restart-safe preparation wrapper missing anchor: {anchor}")
    if wrapper.count('namespace["prepare_first_canary"]') != 1:
        fail("restart-safe wrapper must delegate to exactly one canonical preparation callable")
    if "prepare_from_evidence" in wrapper:
        fail("restart-safe production wrapper may not bypass canonical broker-read preparation")
    for forbidden in (
        "record_operator_approval(",
        ".consume(",
        "ENTRY_SUBMISSION_UNKNOWN",
        "PROTECTION_SUBMISSION_UNKNOWN",
        "R6_EXTERNAL_PAPER_WRITE=ENABLED",
    ):
        if forbidden in wrapper:
            fail(f"restart-safe preparation acquired forbidden downstream authority: {forbidden}")

    print(
        "first-canary restart-safe preparation boundary: PASS — canonical fresh PAPER preparation reused; "
        "sanitized typed evidence hash persisted; no Key/Secret persistence; no approval/UNKNOWN/writer/POST authority; LIVE BLOCKED"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
