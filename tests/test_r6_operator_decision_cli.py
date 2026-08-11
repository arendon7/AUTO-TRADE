from __future__ import annotations

import builtins
from datetime import datetime, timezone
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
from autotrade.persistence import SQLiteRuntime, SQLiteSafetyStateStore
from test_r6_operational_prepare import build, run_prepare


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


def prepared_workspace(tmp_path: Path):
    preparer, workspace, broker, submission, permit = build(tmp_path / "prepared")
    prepared = run_prepare(preparer, submission, permit)
    context = PaperOperatorDecisionContext.from_prepared_package(prepared.result.package)
    assert broker.calls == 0
    return context, workspace


def args_for(workspace, *, ttl: str | None = None) -> list[str]:
    args = [
        "--workspace",
        str(workspace.root),
        "--operator-id",
        "operator:arendon7",
    ]
    if ttl is not None:
        args.extend(["--ttl-seconds", ttl])
    return args


def test_noninteractive_process_cannot_mint_operator_authority(tmp_path, monkeypatch) -> None:
    context, workspace = prepared_workspace(tmp_path)
    ns = namespace()
    monkeypatch.setattr(sys, "stdin", NonTTYStringIO())
    monkeypatch.setattr(sys, "stdout", TTYStringIO())

    with pytest.raises(SystemExit, match="interactive TTY"):
        ns["main"](args_for(workspace))
    assert not workspace.operator_db_path.exists()
    assert context.preparation_hash


def test_wrong_confirmation_challenge_records_nothing(tmp_path, monkeypatch) -> None:
    _, workspace = prepared_workspace(tmp_path)
    ns = namespace()
    monkeypatch.setattr(sys, "stdin", TTYStringIO())
    monkeypatch.setattr(sys, "stdout", TTYStringIO())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "NO")

    with pytest.raises(SystemExit, match="did not match"):
        ns["main"](args_for(workspace))
    assert not workspace.operator_db_path.exists()


def test_exact_tty_challenge_records_only_human_decision_after_two_core_checks(
    tmp_path,
    monkeypatch,
) -> None:
    context, workspace = prepared_workspace(tmp_path)
    ns = namespace()
    output = TTYStringIO()
    calls: list[str] = []
    original_verify = ns["_verify_current_core"]

    def tracked_verify(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        calls.append(result)
        return result

    ns["_verify_current_core"] = tracked_verify
    monkeypatch.setattr(sys, "stdin", TTYStringIO())
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt="": operator_confirmation_challenge(context),
    )

    assert ns["main"](args_for(workspace, ttl="30")) == 0
    assert len(calls) == 2
    assert calls[0] == calls[1]
    state = SQLitePaperOperatorDecisionRegistry(SQLiteRuntime(workspace.operator_db_path)).get(
        context.preparation_hash
    )
    assert state.status is PaperOperatorDecisionStatus.ISSUED
    assert state.decision.operator_id == "operator:arendon7"
    assert state.decision.context == context
    assert state.consumed_at is None
    assert '"core_provenance_document_hash"' in output.getvalue()
    assert '"external_order_submitted": false' in output.getvalue()
    assert '"live_trading": "BLOCKED"' in output.getvalue()


def test_cli_ttl_is_strictly_bounded_before_registry_creation(tmp_path, monkeypatch) -> None:
    _, workspace = prepared_workspace(tmp_path)
    ns = namespace()
    monkeypatch.setattr(sys, "stdin", TTYStringIO())
    monkeypatch.setattr(sys, "stdout", TTYStringIO())

    with pytest.raises(SystemExit, match="between 1 and 120"):
        ns["main"](args_for(workspace, ttl="121"))
    assert not workspace.operator_db_path.exists()


def test_context_loader_rejects_tampered_preparation_hash(tmp_path) -> None:
    context, workspace = prepared_workspace(tmp_path)
    raw = context.to_dict()
    raw["prepared_package_hash"] = "f" * 64
    workspace.operator_context_path.write_text(json.dumps(raw), encoding="utf-8")
    ns = namespace()
    with pytest.raises(SystemExit, match="context is invalid"):
        ns["_load_prepared_context"](workspace)


def test_missing_core_provenance_blocks_human_authority(tmp_path, monkeypatch) -> None:
    _, workspace = prepared_workspace(tmp_path)
    (workspace.root / "core_provenance.json").unlink()
    ns = namespace()
    monkeypatch.setattr(sys, "stdin", TTYStringIO())
    monkeypatch.setattr(sys, "stdout", TTYStringIO())

    with pytest.raises(SystemExit, match="current durable core provenance is not eligible"):
        ns["main"](args_for(workspace))
    assert not workspace.operator_db_path.exists()


def test_core_drift_during_human_challenge_invalidates_authorization(tmp_path, monkeypatch) -> None:
    context, workspace = prepared_workspace(tmp_path)
    ns = namespace()
    monkeypatch.setattr(sys, "stdin", TTYStringIO())
    monkeypatch.setattr(sys, "stdout", TTYStringIO())

    def mutate_core_then_confirm(_prompt="") -> str:
        SQLiteSafetyStateStore(SQLiteRuntime(workspace.core_db_path)).activate(
            reason="operator-confirmation-race",
            now=datetime.now(timezone.utc),
        )
        return operator_confirmation_challenge(context)

    monkeypatch.setattr(builtins, "input", mutate_core_then_confirm)
    with pytest.raises(SystemExit, match="current durable core provenance is not eligible"):
        ns["main"](args_for(workspace))
    assert not workspace.operator_db_path.exists()


def test_workspace_argument_rejects_missing_and_symlinked_root(tmp_path) -> None:
    ns = namespace()
    with pytest.raises(SystemExit, match="does not exist"):
        ns["_open_workspace"](tmp_path / "missing")

    _, workspace = prepared_workspace(tmp_path / "real")
    link = tmp_path / "workspace-link"
    try:
        link.symlink_to(workspace.root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(SystemExit, match="cannot be a symlink"):
        ns["_open_workspace"](link)
