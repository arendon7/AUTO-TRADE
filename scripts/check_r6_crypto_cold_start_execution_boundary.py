from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "cold_start_oms": ROOT / "src/autotrade/cold_start_oms.py",
    "guard": ROOT / "src/autotrade/brokers/alpaca_paper_crypto_cold_start_final_guard.py",
    "checkpoint": ROOT / "src/autotrade/brokers/alpaca_paper_crypto_cold_start_execution_attempt.py",
    "bridge": ROOT / "src/autotrade/brokers/alpaca_paper_crypto_cold_start_execution_bridge.py",
    "pre_io_authority": ROOT / "src/autotrade/brokers/alpaca_paper_crypto_cold_start_pre_io_authority.py",
}


class ColdStartBoundaryError(RuntimeError):
    pass


def _read(label: str) -> str:
    path = FILES[label]
    if not path.is_file():
        raise ColdStartBoundaryError(f"required cold-start authority file missing: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def _subclasses(source: str, base_name: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
            if name == base_name:
                found.add(node.name)
    return found


def main() -> int:
    cold_start_oms = _read("cold_start_oms")
    guard = _read("guard")
    checkpoint = _read("checkpoint")
    bridge = _read("bridge")
    pre_io_authority = _read("pre_io_authority")
    broker_combined = "\n".join((guard, checkpoint, bridge, pre_io_authority))
    combined = cold_start_oms + "\n" + broker_combined

    required = (
        'COLD_START_SYMBOL = "BTC/USD"',
        'COLD_START_SCOPE = "FIRST_TECHNICAL_CANARY_ONLY"',
        'COLD_START_KILL_REASON = "R6_HEALTH_R4_EVIDENCE_REQUIRED"',
        'COLD_START_OMS_SCOPE = "FIRST_TECHNICAL_CANARY_ONLY"',
        'COLD_START_OMS_KILL_REASON = "R6_HEALTH_R4_EVIDENCE_REQUIRED"',
        'COLD_START_MIN_NOTIONAL = Decimal("1")',
        'COLD_START_MAX_NOTIONAL = Decimal("5")',
        'PRE_CONSUME = "PRE_CONSUME"',
        'PRE_IO = "PRE_IO"',
        "CryptoColdStartFinalWritePhase.PRE_CONSUME",
        "PRE_IO requires bootstrap OMS SUBMITTING",
        "PRE_IO requires decision consumed by exact attempt",
        "PRE_IO requires durable ENTRY_SUBMISSION_UNKNOWN with one attempt",
        "authority.health_state_rows != 0 or authority.health_bridge_rows != 0",
        "fresh_flat_account.clean_for_first_canary",
        "alpaca_crypto_cold_start_execution_attempts",
        "operator_registry.consume(",
        "stage_cold_start_external_submission(",
        'event_type="COLD_START_EXTERNAL_ORDER_HANDOFF_AUTHORIZED"',
        "status=OrderStatus.SUBMITTING",
        "self._orders.update(staged)",
        "resolve_cold_start_external_submission_handoff(",
        "self._checkpoints.get(attempt_id)",
        "previous_attestation=checkpoint.pre_consume",
        "PRE_IO attestation predecessor differs from durable checkpoint",
        "r6-crypto-paper-cold-start:",
    )
    for token in required:
        if token not in combined:
            raise ColdStartBoundaryError(f"cold-start authority contract missing: {token}")

    forbidden_everywhere = (
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
    for token in forbidden_everywhere:
        if token in combined:
            raise ColdStartBoundaryError(f"forbidden authority/network bypass in cold-start path: {token}")

    forbidden_in_brokers = (
        "self._orders.update(",
        "ColdStartExternalSubmissionHandoff(",
        "ColdStartOmsStageAuthorization(",
        'event_type="COLD_START_EXTERNAL_ORDER_HANDOFF_AUTHORIZED"',
        "status=OrderStatus.SUBMITTING",
    )
    for token in forbidden_in_brokers:
        if token in broker_combined:
            raise ColdStartBoundaryError(
                f"broker cold-start modules may not own OMS mutation/handoff construction: {token}"
            )

    subclasses = _subclasses(bridge, "ColdStartOmsStageAuthority")
    if subclasses != {"CryptoColdStartExecutionBridge"}:
        raise ColdStartBoundaryError(
            f"cold-start OMS authority subclass drift: {sorted(subclasses)}"
        )
    for label, source in (
        ("guard", guard),
        ("checkpoint", checkpoint),
        ("pre_io_authority", pre_io_authority),
    ):
        if _subclasses(source, "ColdStartOmsStageAuthority"):
            raise ColdStartBoundaryError(f"unexpected cold-start OMS authority subclass in {label}")

    stage_start = cold_start_oms.find("    def stage_cold_start_external_submission(")
    resolve_start = cold_start_oms.find("    def resolve_cold_start_external_submission_handoff(", stage_start)
    if stage_start < 0 or resolve_start < 0:
        raise ColdStartBoundaryError("cold-start OMS stage/resolve method boundaries missing")
    stage = cold_start_oms[stage_start:resolve_start]
    event_pos = stage.find('event_type="COLD_START_EXTERNAL_ORDER_HANDOFF_AUTHORIZED"')
    update_pos = stage.find("self._orders.update(staged)")
    if event_pos < 0 or update_pos < 0 or event_pos > update_pos:
        raise ColdStartBoundaryError(
            "cold-start OMS must append durable handoff before SUBMITTING update"
        )
    if "self._broker.submit" in stage:
        raise ColdStartBoundaryError("cold-start OMS staging may never submit to broker")

    if 'CREATE TABLE IF NOT EXISTS alpaca_crypto_execution_attempts' in checkpoint:
        raise ColdStartBoundaryError("cold-start checkpoint may not reuse normal execution-attempt table")
    if "CryptoFinalWriteAttestation" in checkpoint:
        raise ColdStartBoundaryError("cold-start checkpoint may not masquerade as normal Final Guard evidence")

    print(
        "R6 crypto cold-start execution boundary: PASS "
        "(OMS-owned SUBMITTING, durable PRE_CONSUME+handoff PRE_IO binding, "
        "USD 1-5, no Health fabrication, no broker I/O)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
