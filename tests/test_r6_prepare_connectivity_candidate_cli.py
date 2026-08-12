from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import runpy

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/r6_prepare_connectivity_candidate.py"

@dataclass
class FakeResult:
    artifact_path: Path
    order_id: str = "order-connectivity-001"
    attempt_id: str = "attempt-connectivity-001"
    connectivity_authority_id: str = "1" * 64
    connectivity_binding_id: str = "2" * 64
    standard_package_hash: str = "3" * 64
    bracket_payload_hash: str = "4" * 64
    preparation_hash: str = "5" * 64
    core_db_sha256_after_preparation: str = "6" * 64

class FakeBridge:
    calls = []
    def __init__(self, workspace): self.workspace = workspace
    def prepare(self, *, now):
        self.calls.append((self.workspace, now))
        return FakeResult(self.workspace.root / "connectivity_preparation.json")

def namespace():
    ns = runpy.run_path(str(SCRIPT))
    return ns, ns["main"]

def test_prepare_connectivity_cli_refuses_enabled_write_gate(tmp_path, monkeypatch) -> None:
    root = tmp_path / "workspace"; root.mkdir()
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    _, main = namespace()
    with pytest.raises(SystemExit, match="disable the write gate"):
        main(["--workspace", str(root)])

def test_prepare_connectivity_cli_refuses_credentials(tmp_path, monkeypatch) -> None:
    root = tmp_path / "workspace"; root.mkdir()
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "DISABLED")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret")
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    _, main = namespace()
    with pytest.raises(SystemExit, match="credential-free"):
        main(["--workspace", str(root)])

def test_prepare_connectivity_cli_happy_path_is_non_authorizing(tmp_path, monkeypatch, capsys) -> None:
    root = tmp_path / "workspace"; root.mkdir()
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "DISABLED")
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    _, main = namespace()
    FakeBridge.calls = []
    main.__globals__["PaperConnectivityPreparationBridge"] = FakeBridge
    assert main(["--workspace", str(root)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "CONNECTIVITY_CANARY_PREPARED"
    for key in ("network_used", "credentials_used", "strategy_health_required", "strategy_health_created", "strategy_trading_authorized", "operator_authority_created", "external_post_authorized", "external_order_submitted"):
        assert output[key] is False
    assert output["capital_authority"] == "NONE"
    assert output["profitability_claim"] is False
    assert output["live_trading"] == "BLOCKED"
    assert output["next_action"] == "CONNECTIVITY_OPERATOR_BRIDGE_REQUIRED"
