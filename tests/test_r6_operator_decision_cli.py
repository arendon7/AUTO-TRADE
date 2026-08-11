from __future__ import annotations

import builtins
import io
import json
from pathlib import Path
import runpy
import sys

import pytest

from autotrade.brokers.alpaca_paper_operator_decision import (
    PaperOperatorDecisionContext,
    PaperOperatorDecisionStatus,
    SQLitePaperOperatorDecisionRegistry,
    operator_confirmation_challenge,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_canary_coordinator import prepare, stack


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/r6_issue_operator_decision.py"


class TTYStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class NonTTYStringIO(io.StringIO):
    def isatty(self) -> bool:
        return False


def namespace() -> dict[str, object]:
    return runpy.run_path(str(SCRIPT))


def context_file(tmp_path: Path):
    coordinator, _, _, submission, permit = stack(tmp_path / "prepared")
    prepared = prepare(coordinator, submission, permit)
    context = PaperOperatorDecisionContext.from_prepared_package(prepared.package)
    target = tmp_path / "context.json"
    target.write_text(json.dumps(context.to_dict()), encoding="utf-8")
    return context, target


def test_noninteractive_process_cannot_mint_operator_authority(tmp_path, monkeypatch) -> None:
    context, target = context_file(tmp_path)
    db = tmp_path / "operator.sqlite"
    ns = namespace()
    monkeypatch.setattr(sys, "stdin", NonTTYStringIO())
    monkeypatch.setattr(sys, "stdout", TTYStringIO())

    with pytest.raises(SystemExit, match="interactive TTY"):
        ns["main"](
            [
                "--db",
                str(db),
                "--context",
                str(target),
                "--operator-id",
                "operator:arendon7",
            ]
        )
    assert not db.exists()
    assert context.preparation_hash


def test_wrong_confirmation_challenge_records_nothing(tmp_path, monkeypatch) -> None:
    _, target = context_file(tmp_path)
    db = tmp_path / "operator.sqlite"
    ns = namespace()
    monkeypatch.setattr(sys, "stdin", TTYStringIO())
    monkeypatch.setattr(sys, "stdout", TTYStringIO())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "NO")

    with pytest.raises(SystemExit, match="did not match"):
        ns["main"](
            [
                "--db",
                str(db),
                "--context",
                str(target),
                "--operator-id",
                "operator:arendon7",
            ]
        )
    assert not db.exists()


def test_exact_tty_challenge_records_only_human_decision(tmp_path, monkeypatch) -> None:
    context, target = context_file(tmp_path)
    db = tmp_path / "operator.sqlite"
    ns = namespace()
    output = TTYStringIO()
    monkeypatch.setattr(sys, "stdin", TTYStringIO())
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt="": operator_confirmation_challenge(context),
    )

    assert (
        ns["main"](
            [
                "--db",
                str(db),
                "--context",
                str(target),
                "--operator-id",
                "operator:arendon7",
                "--ttl-seconds",
                "30",
            ]
        )
        == 0
    )
    state = SQLitePaperOperatorDecisionRegistry(SQLiteRuntime(db)).get(
        context.preparation_hash
    )
    assert state.status is PaperOperatorDecisionStatus.ISSUED
    assert state.decision.operator_id == "operator:arendon7"
    assert state.decision.context == context
    assert state.consumed_at is None
    assert '"external_order_submitted": false' in output.getvalue()
    assert '"live_trading": "BLOCKED"' in output.getvalue()


def test_cli_ttl_is_strictly_bounded_before_registry_creation(tmp_path, monkeypatch) -> None:
    _, target = context_file(tmp_path)
    db = tmp_path / "operator.sqlite"
    ns = namespace()
    monkeypatch.setattr(sys, "stdin", TTYStringIO())
    monkeypatch.setattr(sys, "stdout", TTYStringIO())

    with pytest.raises(SystemExit, match="between 1 and 120"):
        ns["main"](
            [
                "--db",
                str(db),
                "--context",
                str(target),
                "--operator-id",
                "operator:arendon7",
                "--ttl-seconds",
                "121",
            ]
        )
    assert not db.exists()


def test_context_loader_rejects_tampered_preparation_hash(tmp_path) -> None:
    context, target = context_file(tmp_path)
    raw = context.to_dict()
    raw["prepared_package_hash"] = "f" * 64
    target.write_text(json.dumps(raw), encoding="utf-8")
    ns = namespace()
    with pytest.raises(SystemExit, match="context is invalid"):
        ns["_load_context"](target)
