from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_research_authority.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_research_authority_test", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scan(tmp_path, source: str):
    module = _load_checker()
    path = tmp_path / "portfolio_manager_probe.py"
    path.write_text(source, encoding="utf-8")
    return module._scan_file(path, forbid_execution_calls=True)


def test_authority_gate_accepts_advisory_dependencies(tmp_path):
    errors = _scan(
        tmp_path,
        "from autotrade.instrument_master import AuthoritativeInstrumentRules\n"
        "from autotrade.research.portfolio_dependence import DependenceEvidence\n"
        "def size_candidate():\n"
        "    return None\n",
    )
    assert errors == []


def test_authority_gate_rejects_oms_import(tmp_path):
    errors = _scan(
        tmp_path,
        "from autotrade.oms import OrderManagementSystem\n",
    )
    assert any("imports execution module autotrade.oms" in error for error in errors)


def test_authority_gate_rejects_order_intent_symbol(tmp_path):
    errors = _scan(
        tmp_path,
        "from autotrade.domain import OrderIntent\n",
    )
    assert any("OrderIntent" in error for error in errors)


def test_authority_gate_rejects_execution_like_submit_call(tmp_path):
    errors = _scan(
        tmp_path,
        "def advisory(x):\n"
        "    return x.submit()\n",
    )
    assert any("execution-like method submit" in error for error in errors)
