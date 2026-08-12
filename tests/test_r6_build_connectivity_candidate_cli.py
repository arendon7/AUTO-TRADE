from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import json
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/r6_build_connectivity_candidate.py"


@dataclass
class FakeResult:
    artifact_path: Path
    order_id: str = "order-connectivity-001"
    quantity: Decimal = Decimal("1")
    limit_price: Decimal = Decimal("5.01")
    effective_notional_cap: Decimal = Decimal("10")
    intent_fingerprint: str = "1" * 64
    risk_decision_fingerprint: str = "2" * 64
    instrument_rules_fingerprint: str = "3" * 64
    authority_id: str = "4" * 64
    authority_hash: str = "5" * 64
    candidate_hash: str = "6" * 64
    core_db_sha256: str = "7" * 64


class FakeBuilder:
    calls = []

    def __init__(self, workspace):
        self.workspace = workspace

    def build(self, *, now):
        self.calls.append((self.workspace, now))
        return FakeResult(self.workspace.root / "connectivity_candidate.json")


def namespace():
    ns = runpy.run_path(str(SCRIPT))
    return ns, ns["main"]


def test_candidate_cli_refuses_enabled_write_gate(tmp_path, monkeypatch) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    _, main = namespace()
    with pytest.raises(SystemExit, match="disable the write gate"):
        main(["--workspace", str(root)])


def test_candidate_cli_refuses_credentials(tmp_path, monkeypatch) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "DISABLED")
    monkeypatch.setenv("APCA_API_KEY_ID", "secret-key")
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    _, main = namespace()
    with pytest.raises(SystemExit, match="credential-free"):
        main(["--workspace", str(root)])


def test_candidate_cli_happy_path_is_sanitized_and_non_authorizing(tmp_path, monkeypatch, capsys) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "DISABLED")
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    ns, main = namespace()
    FakeBuilder.calls = []
    main.__globals__["PaperConnectivityCandidateBuilder"] = FakeBuilder

    assert main(["--workspace", str(root)]) == 0
    assert len(FakeBuilder.calls) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "CONNECTIVITY_CANDIDATE_BUILT"
    assert output["order_status"] == "VALIDATED"
    assert output["quantity"] == "1"
    assert output["network_used"] is False
    assert output["credentials_used"] is False
    assert output["strategy_health_required"] is False
    assert output["strategy_health_created"] is False
    assert output["strategy_trading_authorized"] is False
    assert output["operator_authority_created"] is False
    assert output["external_post_authorized"] is False
    assert output["external_order_submitted"] is False
    assert output["capital_authority"] == "NONE"
    assert output["profitability_claim"] is False
    assert output["live_trading"] == "BLOCKED"
    assert output["next_action"] == "CONNECTIVITY_PREPARATION_BRIDGE_REQUIRED"
