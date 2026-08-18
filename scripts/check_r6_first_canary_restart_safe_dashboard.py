from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts/mac_first_canary_restart_safe_dashboard.py"
LAUNCHER = ROOT / "ABRIR_PRIMER_CANARY_PREPARAR.command"
BASE = ROOT / "scripts/mac_first_canary_dashboard.py"
RESTART_PREPARE = ROOT / "scripts/mac_crypto_first_canary_prepare_restart_safe.py"


def fail(message: str) -> None:
    print(f"first-canary restart-safe Mac preparation: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    for path in (WRAPPER, LAUNCHER, BASE, RESTART_PREPARE):
        if not path.is_file():
            fail(f"missing required file: {path.relative_to(ROOT)}")

    wrapper = WRAPPER.read_text(encoding="utf-8")
    launcher = LAUNCHER.read_text(encoding="utf-8")
    base = BASE.read_text(encoding="utf-8")
    restart = RESTART_PREPARE.read_text(encoding="utf-8")

    for token in (
        'RESTART_SAFE_PREPARE = ROOT / "scripts/mac_crypto_first_canary_prepare_restart_safe.py"',
        "_BASE_REQUIRE_RUNTIME = safe._require_runtime",
        "def _prepare_restart_safe(",
        '"scripts/mac_crypto_first_canary_prepare_restart_safe.py"',
        '"--allow-paper-crypto-read"',
        "safe._prepare = _prepare_restart_safe",
        "return safe.main(argv)",
    ):
        if token not in wrapper:
            fail(f"wrapper missing restart-safe anchor: {token}")
    for forbidden in (
        "HttpsAlpacaPaperCryptoWriteTransport",
        "AlpacaPaperCryptoWriter",
        "execute_first_canary_once",
        "submit_once(",
        "paper-api.alpaca.markets",
        ".post(",
    ):
        if forbidden in wrapper:
            fail(f"restart-safe wrapper leaked write authority: {forbidden}")

    for token in (
        'external_post_authorized": False',
        'broker_write_performed": False',
        'credentials_persisted": False',
        'secret_persisted": False',
        'live_trading": "BLOCKED"',
        'EVIDENCE_FILENAME = "prepared_evidence.json"',
        "attempt.write_once(",
    ):
        if token not in restart:
            fail(f"restart-safe prepare missing durable no-POST anchor: {token}")

    for token in (
        'DASHBOARD="$HERE/scripts/mac_first_canary_restart_safe_dashboard.py"',
        "export R6_EXTERNAL_PAPER_WRITE=DISABLED",
        "unset APCA_API_KEY_ID || true",
        "unset APCA_API_SECRET_KEY || true",
        "Esta pantalla NO puede enviar órdenes.",
        "ABRIR_PRIMER_CANARY_REAL_PAPER.command",
        "LIVE: BLOCKED",
    ):
        if token not in launcher:
            fail(f"preparation launcher missing isolation/UX anchor: {token}")
    for forbidden in (
        "R6_EXTERNAL_PAPER_WRITE=ENABLED",
        "APCA_API_SECRET_KEY=",
        "mac_crypto_first_canary_execute_real_paper.py",
    ):
        if forbidden in launcher:
            fail(f"preparation launcher leaked execution authority: {forbidden}")

    if 'parsed_path not in {"/api/prepare", "/api/approve", "/api/recover"}' not in base:
        fail("inherited safe dashboard route boundary drifted")
    if '"real_execution_enabled": False' not in base:
        fail("inherited safe dashboard no longer denies real execution")

    print(
        "first-canary restart-safe Mac preparation: PASS — safe PR41 localhost UX reused; "
        "prepare now persists typed restart-safe evidence; approve/recover remain no-POST; "
        "separate REAL PAPER launcher required for any broker POST; LIVE blocked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
