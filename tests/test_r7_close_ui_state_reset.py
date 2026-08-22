from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web/mac_r7_paper_operations.html"


def test_close_ui_clears_historical_result_before_new_attempt() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert "function resetCloseResult" in html
    assert "resetCloseResult(true);show('closeReviewCard',false);show('closeExecuteCard',false)" in html
    assert "$('closeExecuteBtn').onclick=async()=>{setBusy(true);resetCloseResult(true);" in html


def test_pre_post_block_is_never_rendered_as_old_reconciliation() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert "function closePrePostBlocked" in html
    assert "POST NO ENVIADO" in html
    assert "Bloqueado antes del broker POST" in html
    assert "closePrePostBlocked(e)" in html
    assert "const pending=e.payload?.close_recovery_pending===true||e.payload?.phase==='RECOVERY_ONLY'" in html


def test_recovery_ui_only_appears_for_burned_or_recovery_only_attempt() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert "if(pending){stat('closeExecuteStatus'" in html
    assert "show('closeRecoverBtn');show('closeRecoveryNotice')" in html
    assert "show('closeRecoverBtn',false);show('closeRecoveryNotice',false)" in html
