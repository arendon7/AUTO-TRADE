from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(path: str, replacements: list[tuple[str, str]]) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"{path}: expected one anchor, found {count}: {old!r}")
        text = text.replace(old, new, 1)
    target.write_text(text, encoding="utf-8")


patch(
    "src/autotrade/brokers/alpaca_paper_canary_coordinator.py",
    [
        (
            "    intent_fingerprint,\n)",
            "    intent_fingerprint,\n    risk_decision_fingerprint,\n)",
        ),
        (
            "    risk_decision_id: str\n    risk_decision_safety_state_version: int",
            "    risk_decision_id: str\n    risk_decision_fingerprint: str\n    risk_decision_safety_state_version: int",
        ),
        (
            '            ("intent_fingerprint", self.intent_fingerprint),\n            ("market_fingerprint", self.market_fingerprint),',
            '            ("intent_fingerprint", self.intent_fingerprint),\n            ("risk_decision_fingerprint", self.risk_decision_fingerprint),\n            ("market_fingerprint", self.market_fingerprint),',
        ),
        (
            '        "risk_decision_id": order.risk_decision_id,\n        "risk_decision_safety_state_version": decision.safety_state_version,',
            '        "risk_decision_id": order.risk_decision_id,\n        "risk_decision_fingerprint": risk_decision_fingerprint(decision),\n        "risk_decision_safety_state_version": decision.safety_state_version,',
        ),
        (
            '        "risk_decision_id": decision.decision_id,\n        "risk_decision_safety_state_version": decision.safety_state_version,',
            '        "risk_decision_id": decision.decision_id,\n        "risk_decision_fingerprint": risk_decision_fingerprint(decision),\n        "risk_decision_safety_state_version": decision.safety_state_version,',
        ),
        (
            '        "risk_decision_id": package.risk_decision_id,\n        "risk_decision_safety_state_version": package.risk_decision_safety_state_version,',
            '        "risk_decision_id": package.risk_decision_id,\n        "risk_decision_fingerprint": package.risk_decision_fingerprint,\n        "risk_decision_safety_state_version": package.risk_decision_safety_state_version,',
        ),
        (
            '        "risk_decision_id": values["risk_decision_id"],\n        "risk_decision_safety_state_version": values["risk_decision_safety_state_version"],',
            '        "risk_decision_id": values["risk_decision_id"],\n        "risk_decision_fingerprint": values["risk_decision_fingerprint"],\n        "risk_decision_safety_state_version": values["risk_decision_safety_state_version"],',
        ),
    ],
)

patch(
    "src/autotrade/brokers/alpaca_paper_operational.py",
    [
        (
            '            risk_decision_id=_string(raw, "risk_decision_id"),\n            risk_decision_safety_state_version=_integer(',
            '            risk_decision_id=_string(raw, "risk_decision_id"),\n            risk_decision_fingerprint=_string(raw, "risk_decision_fingerprint"),\n            risk_decision_safety_state_version=_integer(',
        ),
    ],
)

patch(
    "src/autotrade/brokers/alpaca_paper_preparation_snapshot.py",
    [
        (
            "from autotrade.domain import MarketSnapshot, RiskDecision, RiskDecisionStatus, market_fingerprint",
            "from autotrade.domain import (\n    MarketSnapshot,\n    RiskDecision,\n    RiskDecisionStatus,\n    market_fingerprint,\n    risk_decision_fingerprint,\n)",
        ),
        (
            '            "decision_id": decision.decision_id,\n            "intent_id": decision.intent_id,',
            '            "decision_id": decision.decision_id,\n            "risk_decision_fingerprint": risk_decision_fingerprint(decision),\n            "intent_id": decision.intent_id,',
        ),
        (
            '        market = MarketSnapshot(',
            '        if decision_raw.get("risk_decision_fingerprint") != risk_decision_fingerprint(decision):\n            raise PaperOperationalIntegrityError("snapshot RiskDecision fingerprint mismatch")\n        market = MarketSnapshot(',
        ),
        (
            '    if decision.decision_id != package.risk_decision_id:\n        raise PaperOperationalIntegrityError("RiskDecision id differs from package")',
            '    if decision.decision_id != package.risk_decision_id:\n        raise PaperOperationalIntegrityError("RiskDecision id differs from package")\n    if risk_decision_fingerprint(decision) != package.risk_decision_fingerprint:\n        raise PaperOperationalIntegrityError("RiskDecision fingerprint differs from package")',
        ),
    ],
)

patch(
    "src/autotrade/brokers/alpaca_paper_execution_bridge.py",
    [
        (
            "    market_fingerprint,\n)",
            "    market_fingerprint,\n    risk_decision_fingerprint,\n)",
        ),
        (
            '        if risk_decision.decision_id != package.risk_decision_id:\n            raise PaperExecutionBridgeBlocked("RiskDecision id does not match prepared package")',
            '        if risk_decision.decision_id != package.risk_decision_id:\n            raise PaperExecutionBridgeBlocked("RiskDecision id does not match prepared package")\n        if risk_decision_fingerprint(risk_decision) != package.risk_decision_fingerprint:\n            raise PaperExecutionBridgeBlocked("RiskDecision fingerprint does not match prepared package")',
        ),
    ],
)

patch(
    "scripts/check_r6_canary_coordinator_boundary.py",
    [
        (
            '    "risk_decision_safety_state_version",\n    "market_fingerprint",',
            '    "risk_decision_fingerprint",\n    "risk_decision_safety_state_version",\n    "market_fingerprint",',
        ),
    ],
)

patch(
    "scripts/check_r6_execution_bridge_boundary.py",
    [
        (
            '    "package.risk_decision_safety_state_version",\n    "package.market_fingerprint",',
            '    "risk_decision_fingerprint(risk_decision) != package.risk_decision_fingerprint",\n    "package.risk_decision_safety_state_version",\n    "package.market_fingerprint",',
        ),
    ],
)

patch(
    "tests/test_r6_paper_execution_bridge.py",
    [
        (
            "def test_risk_decision_safety_version_is_bound_to_human_reviewed_package(tmp_path) -> None:\n",
            "def test_full_risk_decision_identity_is_bound_before_human_consume(tmp_path) -> None:\n    prepared, bridge, registry, operator_decision, broker, _ = prepared_stack(tmp_path)\n    for forged in (\n        replace(decision(), approved_notional=Decimal(\"9\")),\n        replace(decision(), reason_detail=\"different authority explanation\"),\n        replace(decision(), limits_version=\"different-limits\"),\n    ):\n        with pytest.raises(PaperExecutionBridgeBlocked, match=\"RiskDecision fingerprint\"):\n            stage(prepared, bridge, registry, operator_decision, risk_decision=forged)\n        assert registry.get(operator_decision.context.preparation_hash).status is PaperOperatorDecisionStatus.ISSUED\n        assert broker.calls == 0\n\n\ndef test_risk_decision_safety_version_is_bound_to_human_reviewed_package(tmp_path) -> None:\n",
        ),
    ],
)

patch(
    "tests/test_r6_preparation_snapshot.py",
    [
        (
            "def test_snapshot_hash_authority_and_package_tamper_fail_closed(tmp_path) -> None:\n",
            "def test_snapshot_binds_full_risk_decision_identity(tmp_path) -> None:\n    result = prepared(tmp_path)\n    workspace = PaperOperationalWorkspace.initialize(tmp_path / \"workspace\")\n    for forged in (\n        replace(decision(), approved_notional=decision().approved_notional + 1),\n        replace(decision(), reason_detail=\"forged reason\"),\n        replace(decision(), limits_version=\"forged-limits\"),\n    ):\n        with pytest.raises(PaperOperationalIntegrityError, match=\"fingerprint\"):\n            write_preparation_snapshot(\n                workspace,\n                package=result.package,\n                decision=forged,\n                market=market(),\n                approval=result.approval,\n            )\n\n\ndef test_snapshot_rejects_malformed_and_wrong_authority_artifacts(tmp_path) -> None:\n    workspace, result, path = write_snapshot(tmp_path)\n    mutations = (\n        ({\"schema_version\": 2}, \"header\"),\n        ({\"environment\": \"LIVE\"}, \"header\"),\n        ({\"credentials_persisted\": True}, \"persist credentials\"),\n        ({\"next_action\": \"EXECUTE\"}, \"action changed\"),\n    )\n    original = json.loads(path.read_text(encoding=\"utf-8\"))\n    for changed, message in mutations:\n        raw = dict(original)\n        raw.update(changed)\n        without_hash = dict(raw)\n        without_hash.pop(\"snapshot_hash\", None)\n        raw[\"snapshot_hash\"] = artifact_hash(without_hash)\n        path.write_text(json.dumps(raw), encoding=\"utf-8\")\n        with pytest.raises(PaperOperationalIntegrityError, match=message):\n            read_preparation_snapshot(workspace, package=result.package)\n    path.write_text(\"[]\", encoding=\"utf-8\")\n    with pytest.raises(PaperOperationalIntegrityError, match=\"root must be object\"):\n        read_preparation_snapshot(workspace, package=result.package)\n    path.write_text(\"{bad-json\", encoding=\"utf-8\")\n    with pytest.raises(PaperOperationalIntegrityError, match=\"cannot read\"):\n        read_preparation_snapshot(workspace, package=result.package)\n\n\ndef test_snapshot_rejects_nested_shape_and_field_tamper(tmp_path) -> None:\n    workspace, result, path = write_snapshot(tmp_path)\n    original = json.loads(path.read_text(encoding=\"utf-8\"))\n    cases = [\n        (\"risk_decision\", None, \"risk_decision must be object\"),\n        (\"market\", [], \"market must be object\"),\n        (\"approval\", \"bad\", \"approval must be object\"),\n    ]\n    for field, value, message in cases:\n        raw = dict(original)\n        raw[field] = value\n        without_hash = dict(raw)\n        without_hash.pop(\"snapshot_hash\", None)\n        raw[\"snapshot_hash\"] = artifact_hash(without_hash)\n        path.write_text(json.dumps(raw), encoding=\"utf-8\")\n        with pytest.raises(PaperOperationalIntegrityError, match=message):\n            read_preparation_snapshot(workspace, package=result.package)\n\n    raw = json.loads(json.dumps(original))\n    raw[\"risk_decision\"][\"reason_detail\"] = \"tampered after preparation\"\n    without_hash = dict(raw)\n    without_hash.pop(\"snapshot_hash\", None)\n    raw[\"snapshot_hash\"] = artifact_hash(without_hash)\n    path.write_text(json.dumps(raw), encoding=\"utf-8\")\n    with pytest.raises(PaperOperationalIntegrityError, match=\"RiskDecision fingerprint\"):\n        read_preparation_snapshot(workspace, package=result.package)\n\n    raw = json.loads(json.dumps(original))\n    raw[\"market\"][\"market_fingerprint\"] = \"f\" * 64\n    without_hash = dict(raw)\n    without_hash.pop(\"snapshot_hash\", None)\n    raw[\"snapshot_hash\"] = artifact_hash(without_hash)\n    path.write_text(json.dumps(raw), encoding=\"utf-8\")\n    with pytest.raises(PaperOperationalIntegrityError, match=\"MarketSnapshot fingerprint\"):\n        read_preparation_snapshot(workspace, package=result.package)\n\n\ndef test_snapshot_hash_authority_and_package_tamper_fail_closed(tmp_path) -> None:\n",
        ),
    ],
)

print("R6 RiskDecision fingerprint hardening patch applied")
