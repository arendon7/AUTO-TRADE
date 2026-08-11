from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "knowledge/00_CANON/debt_register.json"
MATRIX = ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md"
CERT = ROOT / "knowledge/60_EVIDENCE/R4_CERTIFICATION.json"
WORKFLOWS = ROOT / ".github/workflows"
SCRIPTS = ROOT / "scripts"

EXPECTED_CERT_BASIS = "350efd43ac133c95a1997b4a821a2e0bab4afaf2"
EXPECTED_R4_DEBTS = {f"TD-R4-{index:03d}" for index in range(1, 15)}


def test_r4_is_machine_readably_certified_with_no_blocking_debt():
    debt = json.loads(DEBT.read_text())
    assert "R4" in debt["certified_tracks"]
    r4_items = [item for item in debt["items"] if item.get("track") == "R4"]
    assert {item["id"] for item in r4_items} == EXPECTED_R4_DEBTS
    assert not [
        item
        for item in r4_items
        if item["status"] == "OPEN" and item["severity"] in {"P0", "P1", "P2"}
    ]
    assert all(item["status"] == "CLOSED" for item in r4_items)
    assert all(item.get("resolution") for item in r4_items)
    assert all(item.get("evidence") for item in r4_items)


def test_every_required_r4_capability_matrix_row_is_pass():
    rows = []
    for line in MATRIX.read_text().splitlines():
        if not line.startswith("| R4 |"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) >= 5
        rows.append((cells[1], cells[3]))
    assert len(rows) == 8
    assert all(status == "PASS" for _, status in rows), rows


def test_r4_certificate_is_bound_to_final_scan_and_denies_capital_authority():
    cert = json.loads(CERT.read_text())
    assert cert["track"] == "R4"
    assert cert["status"] == "CERTIFIED_BRANCH_PENDING_PR_INTEGRATION"
    assert cert["certification_basis_head"] == EXPECTED_CERT_BASIS
    assert cert["ci"]["tests_passed"] == 479
    assert cert["ci"]["coverage_percent"] == 86.45
    assert cert["ci"]["coverage_percent"] >= 85.0
    assert cert["ci"]["contract_registry"] == "PASS"
    assert cert["ci"]["research_advisory_authority_boundary"] == "PASS"
    assert cert["ci"]["debt_register_contract"] == "PASS"
    assert cert["ci"]["knowledge_contract"] == "PASS"
    assert cert["open_r4_blocking_debt_ids"] == []
    assert set(cert["closed_r4_debt_ids"]) == EXPECTED_R4_DEBTS
    assert cert["capital_authority"] == "NONE"
    assert cert["external_paper_authority"] == "NONE_ADDED_BY_R4"
    assert cert["live_trading"] == "BLOCKED"


def test_no_r4_one_shot_workflows_or_temporary_helpers_remain():
    r4_workflows = sorted(
        path.name for path in WORKFLOWS.glob("r4-*.yml") if path.is_file()
    )
    r4_helpers = sorted(
        path.name for path in SCRIPTS.glob("r4_*.py") if path.is_file()
    )
    assert r4_workflows == []
    assert r4_helpers == []
