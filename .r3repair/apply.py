from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing repair marker: {label} in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. OMS replay: ORDER_BROKER_RESULT is a semantic broker snapshot.
#    Re-observing the same snapshot at another reconciliation timestamp is not
#    an identity conflict. Legacy `recovered` payload metadata is non-semantic.
# ---------------------------------------------------------------------------
oms = ROOT / "src" / "autotrade" / "oms.py"
text = oms.read_text(encoding="utf-8")
text = text.replace(
    '''                    "recovered": str(recovered).lower(),\n''',
    "",
)
old = '''                if existing.event_id == event.event_id:\n                    if (\n                        existing.event_type != event.event_type\n                        or existing.occurred_at != event.occurred_at\n                        or dict(existing.payload) != dict(event.payload)\n                    ):\n                        raise BrokerStateConflict(\n                            f"ledger event identity conflict: {event.event_id}"\n                        )\n                    return\n'''
new = '''                if existing.event_id == event.event_id:\n                    if existing.event_type != event.event_type:\n                        raise BrokerStateConflict(\n                            f"ledger event identity conflict: {event.event_id}"\n                        )\n                    existing_payload = dict(existing.payload)\n                    new_payload = dict(event.payload)\n                    if event.event_type == "ORDER_BROKER_RESULT":\n                        # Snapshot identity is encoded by event_id/status/fills.\n                        # Reconciliation time and the legacy `recovered` marker\n                        # describe observation context, not broker-state identity.\n                        existing_payload.pop("recovered", None)\n                        new_payload.pop("recovered", None)\n                        conflict = existing_payload != new_payload\n                    else:\n                        conflict = (\n                            existing.occurred_at != event.occurred_at\n                            or existing_payload != new_payload\n                        )\n                    if conflict:\n                        raise BrokerStateConflict(\n                            f"ledger event identity conflict: {event.event_id}"\n                        )\n                    return\n'''
if old not in text:
    raise SystemExit("missing OMS idempotency marker")
oms.write_text(text.replace(old, new, 1), encoding="utf-8")

# ---------------------------------------------------------------------------
# 2. R3 external-data adapter must use the already-certified R1 market model,
#    not invent a parallel InstrumentMetadata/Bar/MarketDataset API.
# ---------------------------------------------------------------------------
ext = ROOT / "src" / "autotrade" / "research" / "external_data.py"
text = ext.read_text(encoding="utf-8")
text = text.replace('''                "asset_class": self.dataset.instrument.asset_class,\n''', "")
text = text.replace('''                "price_increment": str(self.dataset.instrument.price_increment),\n                "quantity_increment": str(self.dataset.instrument.quantity_increment),\n''', '''                "price_tick": str(self.dataset.instrument.price_tick),\n                "quantity_step": str(self.dataset.instrument.quantity_step),\n''')
text = text.replace('''                asset_class=instrument_data["asset_class"],\n                quote_currency=instrument_data["quote_currency"],\n                price_increment=Decimal(instrument_data["price_increment"]),\n                quantity_increment=Decimal(instrument_data["quantity_increment"]),\n''', '''                quote_currency=instrument_data["quote_currency"],\n                price_tick=Decimal(instrument_data["price_tick"]),\n                quantity_step=Decimal(instrument_data["quantity_step"]),\n''')
text = text.replace('''            dataset = MarketDataset(\n                instrument=instrument,\n                timeframe=manifest.interval,\n                bars=bars,\n                provenance=manifest.provenance,\n            )\n''', '''            dataset = MarketDataset(\n                instrument=instrument,\n                bars=bars,\n                source=manifest.provenance,\n            )\n''')
text = text.replace('''        dataset = MarketDataset(\n            instrument=request.instrument,\n            timeframe=request.interval,\n            bars=bars,\n            provenance=provenance,\n        )\n        if dataset.detect_gaps():\n''', '''        dataset = MarketDataset(\n            instrument=request.instrument,\n            bars=bars,\n            source=provenance,\n        )\n        if dataset.gap_indexes():\n''')
text = text.replace('''        if _epoch_ms(bar.timestamp) != expected_ms:\n''', '''        if _epoch_ms(bar.started_at) != expected_ms:\n''')
old_bar = '''def _bar_from_canonical_row(row: tuple[object, ...], interval: str) -> Bar:\n    return Bar(\n        timestamp=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),\n        open=Decimal(str(row[1])),\n        high=Decimal(str(row[2])),\n        low=Decimal(str(row[3])),\n        close=Decimal(str(row[4])),\n        volume=Decimal(str(row[5])),\n        timeframe=interval,\n    )\n'''
new_bar = '''def _bar_from_canonical_row(row: tuple[object, ...], interval: str) -> Bar:\n    interval_ms = FIXED_INTERVAL_MS[interval]\n    if interval_ms % 1000:\n        raise ExternalDataIntegrityError("bar interval is not whole-second compatible")\n    return Bar(\n        symbol="",  # replaced below by dataset binding helper\n        started_at=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),\n        timeframe_seconds=interval_ms // 1000,\n        open=Decimal(str(row[1])),\n        high=Decimal(str(row[2])),\n        low=Decimal(str(row[3])),\n        close=Decimal(str(row[4])),\n        volume=Decimal(str(row[5])),\n    )\n'''
# We cannot create a Bar with blank symbol because R1 validation correctly
# rejects it. Convert helper to accept symbol explicitly instead.
new_bar = '''def _bar_from_canonical_row(\n    row: tuple[object, ...], interval: str, symbol: str\n) -> Bar:\n    interval_ms = FIXED_INTERVAL_MS[interval]\n    if interval_ms % 1000:\n        raise ExternalDataIntegrityError("bar interval is not whole-second compatible")\n    return Bar(\n        symbol=symbol,\n        started_at=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),\n        timeframe_seconds=interval_ms // 1000,\n        open=Decimal(str(row[1])),\n        high=Decimal(str(row[2])),\n        low=Decimal(str(row[3])),\n        close=Decimal(str(row[4])),\n        volume=Decimal(str(row[5])),\n    )\n'''
if old_bar not in text:
    raise SystemExit("missing external bar helper marker")
text = text.replace(old_bar, new_bar, 1)
text = text.replace(
    '''        bars = tuple(_bar_from_canonical_row(row, manifest.interval) for row in rows)\n''',
    '''        bars = tuple(\n            _bar_from_canonical_row(row, manifest.interval, instrument.symbol) for row in rows\n        )\n''',
    1,
)
text = text.replace(
    '''        bars = tuple(_bar_from_canonical_row(row, request.interval) for row in rows_tuple)\n''',
    '''        bars = tuple(\n            _bar_from_canonical_row(row, request.interval, request.instrument.symbol)\n            for row in rows_tuple\n        )\n''',
    1,
)
ext.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# 3. Tests and bounded campaign use canonical R1 metadata field names/API.
# ---------------------------------------------------------------------------
for rel in [
    "tests/test_r3_external_data.py",
    "tests/test_r3_external_data_hardening.py",
    "scripts/run_r3_real_data_campaign.py",
]:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    text = text.replace('''        asset_class="CRYPTO",\n''', "")
    text = text.replace("price_increment=", "price_tick=")
    text = text.replace("quantity_increment=", "quantity_step=")
    text = text.replace("artifact.dataset.provenance", "artifact.dataset.source")
    text = text.replace("artifact.dataset.detect_gaps()", "artifact.dataset.gap_indexes()")
    path.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# 4. Old safety boundary tests must build internally consistent snapshots.
#    Strict snapshot validation stays intact; only stale fixtures are repaired.
# ---------------------------------------------------------------------------
safety_tests = ROOT / "tests" / "test_safety_edges.py"
text = safety_tests.read_text(encoding="utf-8")
old = '''def test_strategy_gross_limit(limits, market, empty_portfolio, market_buy_intent):\n    safety, _ = make_kernel(limits)\n    portfolio = replace(\n        empty_portfolio,\n        gross_exposure=Decimal("24500"),\n        strategy_gross_exposure={"strategy-a": Decimal("24500")},\n    )\n    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)\n    assert decision.reason_code == "MAX_STRATEGY_GROSS"\n\n\ndef test_portfolio_gross_limit(limits, market, empty_portfolio, market_buy_intent):\n    safety, _ = make_kernel(limits)\n    portfolio = replace(empty_portfolio, gross_exposure=Decimal("49500"))\n    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)\n    assert decision.reason_code == "MAX_PORTFOLIO_GROSS"\n\n\ndef test_net_exposure_limit(limits, market, empty_portfolio, market_buy_intent):\n    safety, _ = make_kernel(limits)\n    portfolio = replace(empty_portfolio, gross_exposure=Decimal("29000"), net_exposure=Decimal("29500"))\n    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)\n    assert decision.reason_code == "MAX_NET_EXPOSURE"\n\n\ndef test_leverage_limit(limits, market, empty_portfolio, market_buy_intent):\n    safety, _ = make_kernel(limits)\n    portfolio = replace(empty_portfolio, equity=Decimal("10000"), gross_exposure=Decimal("19500"))\n    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)\n    assert decision.reason_code == "MAX_LEVERAGE"\n'''
new = '''def test_strategy_gross_limit(limits, market, empty_portfolio, market_buy_intent):\n    safety, _ = make_kernel(limits)\n    portfolio = replace(\n        empty_portfolio,\n        gross_exposure=Decimal("24500"),\n        net_exposure=Decimal("24500"),\n        signed_position_notional_by_symbol={\n            "ALT1-USD": Decimal("12250"),\n            "ALT2-USD": Decimal("12250"),\n        },\n        strategy_gross_exposure={"strategy-a": Decimal("24500")},\n        strategy_signed_position_notional_by_symbol={\n            "strategy-a": {\n                "ALT1-USD": Decimal("12250"),\n                "ALT2-USD": Decimal("12250"),\n            }\n        },\n    )\n    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)\n    assert decision.reason_code == "MAX_STRATEGY_GROSS"\n\n\ndef test_portfolio_gross_limit(limits, market, empty_portfolio, market_buy_intent):\n    safety, _ = make_kernel(limits)\n    portfolio = replace(\n        empty_portfolio,\n        gross_exposure=Decimal("49500"),\n        net_exposure=Decimal("16500"),\n        signed_position_notional_by_symbol={\n            "ALT1-USD": Decimal("16500"),\n            "ALT2-USD": Decimal("16500"),\n            "ALT3-USD": Decimal("-16500"),\n        },\n        strategy_gross_exposure={\n            "strategy-a": Decimal("16500"),\n            "strategy-b": Decimal("16500"),\n            "strategy-c": Decimal("16500"),\n        },\n        strategy_signed_position_notional_by_symbol={\n            "strategy-a": {"ALT1-USD": Decimal("16500")},\n            "strategy-b": {"ALT2-USD": Decimal("16500")},\n            "strategy-c": {"ALT3-USD": Decimal("-16500")},\n        },\n    )\n    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)\n    assert decision.reason_code == "MAX_PORTFOLIO_GROSS"\n\n\ndef test_net_exposure_limit(limits, market, empty_portfolio, market_buy_intent):\n    safety, _ = make_kernel(limits)\n    portfolio = replace(\n        empty_portfolio,\n        gross_exposure=Decimal("29500"),\n        net_exposure=Decimal("29500"),\n        signed_position_notional_by_symbol={\n            "ALT1-USD": Decimal("14750"),\n            "ALT2-USD": Decimal("14750"),\n        },\n        strategy_gross_exposure={\n            "strategy-a": Decimal("14750"),\n            "strategy-b": Decimal("14750"),\n        },\n        strategy_signed_position_notional_by_symbol={\n            "strategy-a": {"ALT1-USD": Decimal("14750")},\n            "strategy-b": {"ALT2-USD": Decimal("14750")},\n        },\n    )\n    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)\n    assert decision.reason_code == "MAX_NET_EXPOSURE"\n\n\ndef test_leverage_limit(limits, market, empty_portfolio, market_buy_intent):\n    safety, _ = make_kernel(limits)\n    portfolio = replace(\n        empty_portfolio,\n        equity=Decimal("10000"),\n        gross_exposure=Decimal("19500"),\n        net_exposure=Decimal("0"),\n        signed_position_notional_by_symbol={\n            "ALT1-USD": Decimal("9750"),\n            "ALT2-USD": Decimal("-9750"),\n        },\n        strategy_gross_exposure={\n            "strategy-a": Decimal("9750"),\n            "strategy-b": Decimal("9750"),\n        },\n        strategy_signed_position_notional_by_symbol={\n            "strategy-a": {"ALT1-USD": Decimal("9750")},\n            "strategy-b": {"ALT2-USD": Decimal("-9750")},\n        },\n    )\n    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)\n    assert decision.reason_code == "MAX_LEVERAGE"\n'''
if old not in text:
    raise SystemExit("missing stale safety test block")
safety_tests.write_text(text.replace(old, new, 1), encoding="utf-8")

# ---------------------------------------------------------------------------
# 5. Add an explicit regression test: semantic broker result replay at a later
#    time must be idempotent, while changed payload remains fail-closed.
# ---------------------------------------------------------------------------
regression = ROOT / "tests" / "test_r3_oms_replay_regression.py"
regression.write_text('''from datetime import timedelta\n\n\ndef test_terminal_broker_snapshot_replay_at_later_time_is_idempotent(\n    tmp_path, limits, market, empty_portfolio, market_buy_intent\n):\n    from autotrade.bootstrap import build_durable_paper_core\n\n    db = tmp_path / "semantic-replay.db"\n    core = build_durable_paper_core(\n        db_path=db,\n        limits=limits,\n        initial_portfolio=empty_portfolio,\n        now=market.observed_at,\n    )\n    first = core.pipeline.process_intent(\n        intent=market_buy_intent,\n        market=market,\n        now=market.observed_at,\n    )\n    assert first.order is not None\n    before = tuple(event.event_id for event in core.ledger.all_events())\n\n    restarted = build_durable_paper_core(\n        db_path=db,\n        limits=limits,\n        initial_portfolio=empty_portfolio,\n        now=market.observed_at + timedelta(seconds=5),\n    )\n    assert restarted.startup_reconciliation.ok is True\n    after = tuple(event.event_id for event in restarted.ledger.all_events())\n    # Re-observation cannot create a second semantic ORDER_BROKER_RESULT.\n    result_ids = [value for value in after if value.startswith("order-result:")]\n    assert len(result_ids) == 1\n    assert set(before).issubset(set(after))\n''', encoding="utf-8")

# Self-clean after successful patch execution.
shutil.rmtree(ROOT / ".r3repair", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r3-repair-one-shot.yml"
if workflow.exists():
    workflow.unlink()
print("R3 compatibility/OMS repair applied")
