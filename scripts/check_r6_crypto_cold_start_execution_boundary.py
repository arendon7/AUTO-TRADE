from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "guard": ROOT / "src/autotrade/brokers/alpaca_paper_crypto_cold_start_final_guard.py",
    "checkpoint": ROOT / "src/autotrade/brokers/alpaca_paper_crypto_cold_start_execution_attempt.py",
    "bridge": ROOT / "src/autotrade/brokers/alpaca_paper_crypto_cold_start_execution_bridge.py",
}


class ColdStartBoundaryError(RuntimeError):
    pass


def _read(label: str) -> str:
    path = FILES[label]
    if not path.is_file():
        raise ColdStartBoundaryError(f"required cold-start authority file missing: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def main() -> int:
    guard = _read("guard")
    checkpoint = _read("checkpoint")
    bridge = _read("bridge")
    combined = "\n".join((guard, checkpoint, bridge))

    required = (
        'COLD_START_SYMBOL = "BTC/USD"',
        'COLD_START_SCOPE = "FIRST_TECHNICAL_CANARY_ONLY"',
        'COLD_START_KILL_REASON = "R6_HEALTH_R4_EVIDENCE_REQUIRED"',
        'COLD_START_MIN_NOTIONAL = Decimal("1")',
        'COLD_START_MAX_NOTIONAL = Decimal("5")',
        'PRE_CONSUME = "PRE_CONSUME"',
        'PRE_IO = "PRE_IO"',
        "CryptoColdStartFinalWritePhase.PRE_CONSUME",
        "PRE_IO requires bootstrap OMS SUBMITTING",
        "PRE_IO requires decision consumed by exact attempt",
        "PRE_IO requires durable ENTRY_SUBMISSION_UNKNOWN with one attempt",
        "authority.state_fingerprint",
        "authority.health_state_rows != 0 or authority.health_bridge_rows != 0",
        "fresh_flat_account.clean_for_first_canary",
        "alpaca_crypto_cold_start_execution_attempts",
        "COLD_START_EXTERNAL_HANDOFF_AUTHORIZED",
        "operator_registry.consume(",
        "OrderStatus.SUBMITTING",
        "authoritative cold-start core changed",
    )
    for token in required:
        if token not in combined:
            raise ColdStartBoundaryError(f"cold-start authority contract missing: {token}")

    forbidden = (
        "AlpacaPaperCredentials",
        "GuardedAlpacaPaperCryptoWriteTransport",
        "FinalGuardedCryptoEntryTransport",
        "FinalGuardedCryptoProtectionTransport",
        "requests.post",
        "requests.request",
        "httpx.post",
        "urllib.request",
        "urlopen(",
        "HTTPSConnection",
        "broker.submit(",
        ".stage_external_submission(",
        "HealthRiskMode.NORMAL",
        "synchronize_health_bridge",
        "reset_kill_switch",
        ".reset(",
        "R6_EXTERNAL_PAPER_WRITE",
    )
    for token in forbidden:
        if token in combined:
            raise ColdStartBoundaryError(f"forbidden authority/network bypass in cold-start path: {token}")

    if "broker" in bridge.lower() and "no broker" not in bridge.lower():
        # The word may appear in comments describing the absence of a broker. The
        # actual import/constructor surface must remain free of ExecutionBroker.
        if "ExecutionBroker" in bridge or "AlpacaPaper" in bridge:
            raise ColdStartBoundaryError("cold-start bridge acquired broker-facing dependency")

    if 'CREATE TABLE IF NOT EXISTS alpaca_crypto_execution_attempts' in checkpoint:
        raise ColdStartBoundaryError("cold-start checkpoint may not reuse the normal execution-attempt table")
    if "CryptoFinalWriteAttestation" in checkpoint:
        raise ColdStartBoundaryError("cold-start checkpoint may not masquerade as normal Final Guard evidence")

    print(
        "R6 crypto cold-start execution boundary: PASS "
        "(isolated bootstrap authority, USD 1-5, no Health fabrication, no broker I/O)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
