from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.brokers.alpaca_paper_operational_execute import (
    PaperOperationalExecutionBlocked,
    PaperOperationalExecutionRuntime,
    _ExistingHealthStateReader,
    _require_aware,
    _require_regular_db,
    _required_bool,
    _required_datetime,
    _required_decimal,
    _required_str,
)
from autotrade.brokers.alpaca_paper_writer import AlpacaPaperSingleShotWriter


def test_runtime_constructor_rejects_non_workspace() -> None:
    with pytest.raises(TypeError, match="operational workspace"):
        PaperOperationalExecutionRuntime(  # type: ignore[arg-type]
            workspace=object(),
            writer=AlpacaPaperSingleShotWriter(),
        )


def test_runtime_constructor_rejects_non_writer(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    with pytest.raises(TypeError, match="single-shot PAPER writer"):
        PaperOperationalExecutionRuntime(  # type: ignore[arg-type]
            workspace=workspace,
            writer=object(),
        )


def test_existing_health_reader_requires_existing_core_db(tmp_path) -> None:
    with pytest.raises(PaperOperationalExecutionBlocked, match="core SQLite database"):
        _ExistingHealthStateReader(tmp_path / "missing.sqlite3")


def test_regular_db_guard_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(PaperOperationalExecutionBlocked, match="submission SQLite database"):
        _require_regular_db(tmp_path / "missing.sqlite3", "submission")


def test_required_string_is_strict() -> None:
    assert _required_str({"value": "PAPER"}, "value") == "PAPER"
    with pytest.raises(ValueError, match="non-empty string"):
        _required_str({"value": ""}, "value")
    with pytest.raises(ValueError, match="non-empty string"):
        _required_str({"value": 1}, "value")


def test_required_bool_is_strict() -> None:
    assert _required_bool({"value": False}, "value") is False
    with pytest.raises(ValueError, match="must be bool"):
        _required_bool({"value": 0}, "value")


def test_required_decimal_rejects_non_string_and_nonfinite() -> None:
    assert _required_decimal({"value": "1.25"}, "value") == Decimal("1.25")
    with pytest.raises(ValueError, match="decimal string"):
        _required_decimal({"value": 1.25}, "value")
    with pytest.raises(ValueError, match="finite"):
        _required_decimal({"value": "NaN"}, "value")


def test_required_datetime_rejects_naive_or_noncanonical() -> None:
    canonical = "2026-08-11T18:30:00+00:00"
    assert _required_datetime({"value": canonical}, "value").isoformat() == canonical
    with pytest.raises(ValueError, match="timezone-aware"):
        _required_datetime({"value": "2026-08-11T18:30:00"}, "value")


def test_execution_time_guard_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _require_aware(datetime(2026, 8, 11, 18, 30, 0))
