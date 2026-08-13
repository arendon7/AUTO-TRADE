from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from autotrade.brokers.alpaca_paper_bracket import AlpacaEquityBracketRequest
from test_r6_equity_bracket import request, rules


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_product_boundary.py"


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def test_permanent_product_boundary_checker_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "multi-asset product boundary: PASS" in result.stdout
    assert "us_equity bracket preserved" in result.stdout
    assert "crypto uses explicit ProductCapabilities" in result.stdout
    assert "no cross-product writer path" in result.stdout


@pytest.mark.parametrize("asset_class", ["crypto", "option", "options", "future", "futures", ""])
def test_authoritative_venue_rules_reject_every_non_equity_asset_class(asset_class) -> None:
    with pytest.raises(ValueError, match="us_equity"):
        rules(asset_class=asset_class)


def direct_request(**overrides) -> AlpacaEquityBracketRequest:
    safe = request()
    values = {field: getattr(safe, field) for field in safe.__dataclass_fields__}
    values.update(overrides)
    return AlpacaEquityBracketRequest(**values)


def test_direct_request_constructor_rejects_non_equity_asset_class() -> None:
    for asset_class in ("crypto", "options", "futures", "cash"):
        with pytest.raises(ValueError, match="us_equity"):
            direct_request(asset_class=asset_class)


@pytest.mark.parametrize(
    "key,value,reason",
    [
        ("order_class", "simple", "order_class"),
        ("order_class", "oco", "order_class"),
        ("type", "market", "type"),
        ("type", "stop_limit", "type"),
        ("side", "sell", "BUY-only"),
        ("time_in_force", "gtc", "time_in_force"),
        ("extended_hours", True, "extended_hours"),
    ],
)
def test_direct_request_rejects_unsupported_order_or_protection_modes(key, value, reason) -> None:
    safe = request()
    payload = deepcopy(dict(safe.canonical_payload))
    payload[key] = value
    payload_json = __import__("json").dumps(payload, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match=reason):
        direct_request(
            canonical_payload=payload,
            payload_json=payload_json,
            payload_hash=sha256(payload_json.encode()).hexdigest(),
        )


def test_request_rejects_extra_or_missing_payload_authority() -> None:
    safe = request()
    extra = deepcopy(dict(safe.canonical_payload))
    extra["trail_percent"] = "1"
    extra_json = __import__("json").dumps(extra, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="surface is not exact"):
        direct_request(
            canonical_payload=extra,
            payload_json=extra_json,
            payload_hash=sha256(extra_json.encode()).hexdigest(),
        )

    missing = deepcopy(dict(safe.canonical_payload))
    missing.pop("stop_loss")
    missing_json = __import__("json").dumps(missing, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="surface is not exact"):
        direct_request(
            canonical_payload=missing,
            payload_json=missing_json,
            payload_hash=sha256(missing_json.encode()).hexdigest(),
        )


def test_nested_protection_surface_is_exact() -> None:
    safe = request()
    for outer, extra_key, reason in (
        ("take_profit", "stop_price", "take_profit surface"),
        ("stop_loss", "limit_price", "stop_loss surface"),
    ):
        payload = deepcopy(dict(safe.canonical_payload))
        payload[outer][extra_key] = "1"
        raw = __import__("json").dumps(payload, sort_keys=True, separators=(",", ":"))
        with pytest.raises(ValueError, match=reason):
            direct_request(
                canonical_payload=payload,
                payload_json=raw,
                payload_hash=sha256(raw.encode()).hexdigest(),
            )


@pytest.mark.parametrize("field,value", [("qty", "1.0"), ("limit_price", "10.00")])
def test_request_requires_canonical_decimal_text(field, value) -> None:
    safe = request()
    payload = deepcopy(dict(safe.canonical_payload))
    payload[field] = value
    raw = __import__("json").dumps(payload, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="canonical decimal"):
        direct_request(
            canonical_payload=payload,
            payload_json=raw,
            payload_hash=sha256(raw.encode()).hexdigest(),
        )


def test_request_rejects_payload_json_or_hash_forgery() -> None:
    safe = request()
    with pytest.raises(ValueError, match="serialization"):
        direct_request(payload_json="{}")
    with pytest.raises(ValueError, match="payload_hash"):
        direct_request(payload_hash="f" * 64)


def test_request_rejects_protection_geometry_forgery() -> None:
    safe = request()
    payload = deepcopy(dict(safe.canonical_payload))
    payload["take_profit"]["limit_price"] = "9"
    raw = __import__("json").dumps(payload, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="geometry"):
        direct_request(
            canonical_payload=payload,
            payload_json=raw,
            payload_hash=sha256(raw.encode()).hexdigest(),
        )


def test_request_identity_and_instrument_hash_are_self_validating() -> None:
    with pytest.raises(ValueError, match="order_id"):
        direct_request(order_id="bad id")
    with pytest.raises(ValueError, match="client_order_id"):
        direct_request(client_order_id="bad id")
    with pytest.raises(ValueError, match="fingerprint"):
        direct_request(instrument_master_fingerprint="bad")


def test_checker_rejects_request_constructor_outside_certified_builder(tmp_path) -> None:
    namespace = runpy.run_path(str(CHECKER))
    fake_root = tmp_path / "root"
    broker_dir = fake_root / "src/autotrade/brokers"
    broker_dir.mkdir(parents=True)
    (broker_dir / "alpaca_paper_bracket.py").write_text(
        "def build():\n    return AlpacaEquityBracketRequest()\n",
        encoding="utf-8",
    )
    (broker_dir / "alpaca_paper_writer.py").write_text(
        "def bad():\n    return AlpacaEquityBracketRequest()\n",
        encoding="utf-8",
    )
    fn = namespace["_validate_constructor_authority"]
    fn.__globals__["ROOT"] = fake_root
    fn.__globals__["BROKER_DIR"] = broker_dir
    errors = fn()
    assert any("forbidden outside certified builder" in error for error in errors)


def test_checker_requires_exactly_one_builder_constructor(tmp_path) -> None:
    namespace = runpy.run_path(str(CHECKER))
    fake_root = tmp_path / "root"
    broker_dir = fake_root / "src/autotrade/brokers"
    broker_dir.mkdir(parents=True)
    (broker_dir / "alpaca_paper_bracket.py").write_text("def build():\n    return None\n")
    fn = namespace["_validate_constructor_authority"]
    fn.__globals__["ROOT"] = fake_root
    fn.__globals__["BROKER_DIR"] = broker_dir
    errors = fn()
    assert any("expected exactly one" in error for error in errors)


def test_checker_rejects_crypto_import_of_equity_bracket_or_writer(tmp_path) -> None:
    namespace = runpy.run_path(str(CHECKER))
    fake_root = tmp_path / "root"
    broker_dir = fake_root / "src/autotrade/brokers"
    broker_dir.mkdir(parents=True)
    # Create every expected crypto file so this test isolates cross-product imports.
    for name in namespace["CRYPTO_FILES"]:
        (broker_dir / name).write_text("VALUE = 1\n", encoding="utf-8")
    (broker_dir / "alpaca_paper_crypto_order.py").write_text(
        "from autotrade.brokers.alpaca_paper_bracket import AlpacaEquityBracketRequest\n",
        encoding="utf-8",
    )
    fn = namespace["_validate_crypto_files"]
    fn.__globals__["ROOT"] = fake_root
    fn.__globals__["BROKER_DIR"] = broker_dir
    errors = fn()
    assert any("forbidden equity/write authority" in error for error in errors)
    assert any("may never construct AlpacaEquityBracketRequest" in error for error in errors) is False


def test_checker_rejects_crypto_construction_of_equity_bracket(tmp_path) -> None:
    namespace = runpy.run_path(str(CHECKER))
    fake_root = tmp_path / "root"
    broker_dir = fake_root / "src/autotrade/brokers"
    broker_dir.mkdir(parents=True)
    for name in namespace["CRYPTO_FILES"]:
        (broker_dir / name).write_text("VALUE = 1\n", encoding="utf-8")
    (broker_dir / "alpaca_paper_crypto_lifecycle.py").write_text(
        "def bad():\n    return AlpacaEquityBracketRequest()\n",
        encoding="utf-8",
    )
    fn = namespace["_validate_crypto_files"]
    fn.__globals__["ROOT"] = fake_root
    fn.__globals__["BROKER_DIR"] = broker_dir
    errors = fn()
    assert any("may never construct AlpacaEquityBracketRequest" in error for error in errors)
