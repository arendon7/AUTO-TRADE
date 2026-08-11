from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sys

from autotrade.contract_registry import ContractRegistry, ContractRegistryError


REQUIRED_CONTRACTS = {
    "OrderIntent@1",
    "RiskDecision@1",
    "Fill@1",
    "BrokerExecution@1",
    "OrderRecord@1",
    "RiskReservation@1",
    "SafetyControlState@1",
    "RiskTelemetryState@1",
    "LedgerEvent@1",
}


def main() -> int:
    registry = ContractRegistry.load_default()
    ids = {spec.contract_id for spec in registry.all_contracts()}
    missing = REQUIRED_CONTRACTS - ids
    if missing:
        raise ContractRegistryError(f"missing required R2 contracts: {sorted(missing)}")

    now = datetime.now(timezone.utc).isoformat()
    fill = {
        "fill_id": "ci-fill",
        "order_id": "ci-order",
        "symbol": "TEST-USD",
        "side": "BUY",
        "quantity": "1",
        "price": "100",
        "occurred_at": now,
    }
    registry.validate(
        "BrokerExecution@1",
        {"status": "FILLED", "fills": [fill]},
    )

    fingerprint = registry.registry_fingerprint()
    if len(fingerprint) != 64:
        raise ContractRegistryError("registry fingerprint is not SHA-256 length")

    source = Path(__file__).resolve().parents[1] / "src" / "autotrade" / "contracts" / "registry.json"
    json.loads(source.read_text(encoding="utf-8"))
    print(
        f"AUTO-TRADE contract registry: PASS ({len(ids)} contracts, sha256={fingerprint})"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: contract registry: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
