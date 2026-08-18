from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web/mac_first_canary.html"


def test_first_canary_operator_is_real_default_not_placeholder_only() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert 'id="operatorId" autocomplete="off" value="operator-001"' in html
    assert 'if(!$("operatorId").value.trim())$("operatorId").value="operator-001";' in html


def test_approval_button_requires_operator_and_exact_challenge() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert '!!$("operatorId").value.trim()' in html
    assert '$("confirmation").value===challenge' in html
    assert 'for(const id of ["operatorId","confirmation"])$(id).addEventListener("input",render);' in html
    assert 'operator_id:$("operatorId").value.trim()' in html


def test_prepare_surface_points_to_separate_real_paper_launcher_and_stays_no_post() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert "ABRIR_PRIMER_CANARY_REAL_PAPER.command" in html
    assert 'id="executeBtn" disabled' in html
    assert '$("executeBtn").disabled=true' in html
    assert 'fetch("/api/execute"' not in html
    assert "LIVE BLOCKED" in html
