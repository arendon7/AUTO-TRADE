from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/brokers/alpaca_paper_operator_decision.py"
TEST = ROOT / "tests/test_r6_operator_decision.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = MODULE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from autotrade.oms import ExternalSubmissionHandoff\n",
        "",
        "remove pre-operator OMS handoff import",
    )
    text = replace_once(
        text,
        "from .alpaca_paper_canary import PaperCanaryApproval\n",
        "from .alpaca_paper_canary_coordinator import PreparedPaperCanaryPackage\n",
        "prepared package import",
    )
    text = replace_once(
        text,
        "    oms_handoff_hash: str\n",
        "    prepared_package_hash: str\n",
        "context package field",
    )
    # Replace both instance-field forms before changing serialized keys. The
    # first refinement attempt changed the key but left context.oms_handoff_hash
    # in _context_payload_without_hash; keep this explicit and audit residuals.
    text = text.replace("self.oms_handoff_hash", "self.prepared_package_hash")
    text = text.replace("context.oms_handoff_hash", "context.prepared_package_hash")
    text = text.replace('"oms_handoff_hash"', '"prepared_package_hash"')
    text = text.replace("oms_handoff_hash=", "prepared_package_hash=")

    start = text.find("    @classmethod\n    def from_evidence(")
    end = text.find("\n    def to_dict(self)", start)
    if start < 0 or end < 0:
        raise SystemExit("operator context from_evidence block not found")
    new_factory = '''    @classmethod\n    def from_prepared_package(\n        cls,\n        package: PreparedPaperCanaryPackage,\n    ) -> "PaperOperatorDecisionContext":\n        if not isinstance(package, PreparedPaperCanaryPackage):\n            raise TypeError("prepared PAPER canary package is required")\n        if package.network_write_authorized is not False:\n            raise ValueError("operator decision requires a non-authorizing prepared package")\n        if package.next_action != "OPERATOR_DECISION_REQUIRED":\n            raise ValueError("prepared package does not require operator decision")\n        if package.order_status != "VALIDATED":\n            raise ValueError("operator decision requires prepared VALIDATED OMS state")\n        raw = {\n            "environment": "PAPER",\n            "account_attestation_fingerprint": package.account_attestation_fingerprint,\n            "order_id": package.order_id,\n            "client_order_id": package.client_order_id,\n            "binding_hash": package.submission_binding_hash,\n            "bracket_payload_hash": package.bracket_payload_hash,\n            "canary_approval_hash": package.canary_approval_hash,\n            "prepared_package_hash": package.package_hash,\n            "notional": str(package.notional),\n            "attempt_id": package.attempt_id,\n        }\n        return cls(\n            environment="PAPER",\n            account_attestation_fingerprint=package.account_attestation_fingerprint,\n            order_id=package.order_id,\n            client_order_id=package.client_order_id,\n            binding_hash=package.submission_binding_hash,\n            bracket_payload_hash=package.bracket_payload_hash,\n            canary_approval_hash=package.canary_approval_hash,\n            prepared_package_hash=package.package_hash,\n            notional=package.notional,\n            attempt_id=package.attempt_id,\n            preparation_hash=_hash_json(raw),\n        )\n'''
    text = text[:start] + new_factory + text[end:]
    if "oms_handoff_hash" in text or "ExternalSubmissionHandoff" in text:
        raise SystemExit("residual pre-operator OMS handoff binding remains after refinement")
    MODULE.write_text(text, encoding="utf-8")

    test = TEST.read_text(encoding="utf-8")
    test = replace_once(
        test,
        "from test_r6_paper_writer import NOW, stack\n",
        "from test_r6_paper_canary_coordinator import NOW, prepare, stack\n",
        "operator tests use coordinator package",
    )
    old_evidence_start = test.find("def evidence(")
    old_evidence_end = test.find("\n\ndef issue(", old_evidence_start)
    if old_evidence_start < 0 or old_evidence_end < 0:
        raise SystemExit("operator test evidence helper not found")
    new_evidence = '''def evidence(tmp_path):\n    coordinator, _, _, submission, permit = stack(tmp_path / "base")\n    prepared = prepare(coordinator, submission, permit)\n    context = PaperOperatorDecisionContext.from_prepared_package(prepared.package)\n    registry = SQLitePaperOperatorDecisionRegistry(\n        SQLiteRuntime(tmp_path / "operator.sqlite")\n    )\n    return prepared, context, registry\n'''
    test = test[:old_evidence_start] + new_evidence + test[old_evidence_end:]
    test = test.replace('values, context, _ = evidence(tmp_path)', 'prepared, context, _ = evidence(tmp_path)')
    test = test.replace('values, _, _ = evidence(tmp_path)', 'prepared, _, _ = evidence(tmp_path)')
    test = test.replace('values["binding"].order_id', 'prepared.binding.order_id')
    test = test.replace('values["binding"].client_order_id', 'prepared.binding.client_order_id')
    test = test.replace('values["binding"].fingerprint', 'prepared.binding.fingerprint')
    test = test.replace('values["expected"].payload_hash', 'prepared.bracket.payload_hash')
    test = test.replace('values["approval"].approval_hash', 'prepared.approval.approval_hash')
    test = test.replace('values["handoff"].handoff_hash', 'prepared.package.package_hash')
    test = test.replace('values["approval"].notional', 'prepared.approval.notional')
    test = test.replace('assert context.attempt_id == "writer-attempt-001"', 'assert context.attempt_id == prepared.package.attempt_id')
    test = test.replace('assert context.oms_handoff_hash == prepared.package.package_hash', 'assert context.prepared_package_hash == prepared.package.package_hash')

    start = test.find("def test_context_cross_evidence_mismatch_fails_closed")
    end = test.find("\n\ndef test_issue_is_durable", start)
    if start < 0 or end < 0:
        raise SystemExit("operator cross-evidence test block not found")
    replacement = '''def test_context_package_hash_tamper_fails_closed(tmp_path) -> None:\n    _, context, _ = evidence(tmp_path)\n    with pytest.raises(ValueError, match="preparation_hash mismatch"):\n        replace(\n            context,\n            prepared_package_hash="f" * 64,\n            preparation_hash="0" * 64,\n        )\n'''
    test = test[:start] + replacement + test[end:]
    test = test.replace('evidence(tmp_path / "tail", attempt_id="writer-attempt-002")', 'evidence(tmp_path / "tail")')
    test = test.replace('evidence(tmp_path / "control", attempt_id="writer-attempt-003")', 'evidence(tmp_path / "control")')
    if "oms_handoff_hash" in test:
        raise SystemExit("operator tests retain old OMS handoff binding")
    TEST.write_text(test, encoding="utf-8")
    print("operator decision now binds exact PreparedPaperCanaryPackage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
