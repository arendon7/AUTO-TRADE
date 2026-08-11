from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3

import pytest
from websockets.exceptions import WebSocketException

from autotrade.research import stream_transport
from autotrade.research.forward import (
    ForwardEvidenceIntegrityError,
    FrozenForwardPolicy,
    SQLiteForwardEvidenceRegistry,
)
from autotrade.research.market import InstrumentMetadata
from autotrade.research.shadow import (
    FrozenShadowConfig,
    ShadowIntegrityError,
    SQLitePortfolioShadowRegistry,
    StrategyShadowObservation,
)
from autotrade.research.streaming import (
    ClosedKlineStream,
    ClosedKlineStreamPolicy,
    ClosedKlineSubscription,
    StreamIntegrityError,
    StreamOpenRequest,
    StreamPolicyError,
    StreamState,
    StreamUnavailable,
)
from autotrade.research.stream_transport import (
    WebsocketsReadOnlySession,
    WebsocketsReadOnlyTransport,
)

UTC = timezone.utc
T0 = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def instrument(**overrides) -> InstrumentMetadata:
    values = {
        "symbol": "BTCUSDT",
        "venue": "BINANCE_SPOT",
        "quote_currency": "USDT",
        "price_tick": Decimal("0.01"),
        "quantity_step": Decimal("0.00001"),
    }
    values.update(overrides)
    return InstrumentMetadata(**values)


def subscription(**overrides) -> ClosedKlineSubscription:
    values = {"instrument": instrument(), "interval": "1m", "enabled": True}
    values.update(overrides)
    return ClosedKlineSubscription(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"instrument": instrument(venue="ALPACA")},
        {"instrument": instrument(symbol="btcusdt")},
        {"interval": "2m"},
        {"timeout_seconds": 0},
        {"timeout_seconds": 31},
        {"max_event_lag_seconds": 0},
        {"max_event_lag_seconds": 301},
        {"future_tolerance_seconds": -1},
        {"future_tolerance_seconds": 6},
    ],
)
def test_subscription_rejects_invalid_authority_or_bounds(overrides) -> None:
    with pytest.raises(ValueError):
        subscription(**overrides)


def test_subscription_derives_exact_market_data_url_and_timeframe() -> None:
    value = subscription(interval="1s")
    assert value.stream_name == "btcusdt@kline_1s"
    assert value.url == "wss://data-stream.binance.vision/ws/btcusdt@kline_1s"
    assert value.timeframe_seconds == 1


@pytest.mark.parametrize(
    "open_request,stream_name",
    [
        (StreamOpenRequest("wss://data-stream.binance.vision/ws/btcusdt@kline_1m", 1, False), "btcusdt@kline_1m"),
        (StreamOpenRequest("wss://data-stream.binance.vision/ws/btcusdt@kline_1m", 0), "btcusdt@kline_1m"),
        (StreamOpenRequest("wss://data-stream.binance.vision/ws/btcusdt@kline_1m", 31), "btcusdt@kline_1m"),
        (StreamOpenRequest("wss://data-stream.binance.vision/ws/btcusdt@kline_1m", 1), "BAD"),
        (StreamOpenRequest("ws://data-stream.binance.vision/ws/btcusdt@kline_1m", 1), "btcusdt@kline_1m"),
        (StreamOpenRequest("wss://user:pass@data-stream.binance.vision/ws/btcusdt@kline_1m", 1), "btcusdt@kline_1m"),
        (StreamOpenRequest("wss://data-stream.binance.vision:8443/ws/btcusdt@kline_1m", 1), "btcusdt@kline_1m"),
    ],
)
def test_stream_policy_rejects_noncanonical_requests(open_request, stream_name) -> None:
    with pytest.raises(StreamPolicyError):
        ClosedKlineStreamPolicy().validate(open_request, expected_stream_name=stream_name)


class PassiveSession:
    def __init__(self) -> None:
        self.closed = False

    def receive(self, *, timeout_seconds: float):
        raise AssertionError("receive not expected")

    def close(self) -> None:
        self.closed = True


class PassiveTransport:
    def __init__(self) -> None:
        self.session = PassiveSession()
        self.calls = 0

    def open(self, open_request: StreamOpenRequest):
        self.calls += 1
        return self.session


def test_open_is_idempotent_and_closed_stream_stays_closed() -> None:
    transport = PassiveTransport()
    stream = ClosedKlineStream(subscription=subscription(), transport=transport)
    stream.open()
    stream.open()
    assert transport.calls == 1
    stream.close()
    assert transport.session.closed is True
    with pytest.raises(StreamUnavailable):
        stream.open()


def test_poll_and_direct_ingest_require_active_session() -> None:
    stream = ClosedKlineStream(subscription=subscription(), transport=PassiveTransport())
    with pytest.raises(StreamUnavailable):
        stream.poll_once(received_at=T0)
    with pytest.raises(StreamUnavailable):
        stream.ingest("{}", received_at=T0)


class QueueSession:
    def __init__(self, payload: str | bytes) -> None:
        self.payload = payload
        self.closed = False

    def receive(self, *, timeout_seconds: float):
        return self.payload

    def close(self) -> None:
        self.closed = True


class QueueTransport:
    def __init__(self, payload: str | bytes) -> None:
        self.session = QueueSession(payload)

    def open(self, open_request: StreamOpenRequest):
        return self.session


def active_stream(payload: str | bytes) -> ClosedKlineStream:
    stream = ClosedKlineStream(subscription=subscription(), transport=QueueTransport(payload))
    stream.open()
    return stream


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        "[]",
        '{"value":NaN}',
        '{"e":"kline","s":"BTCUSDT"}',
        '{"e":"trade","s":"BTCUSDT","k":{}}',
    ],
)
def test_stream_payload_envelope_errors_degrade(payload) -> None:
    stream = active_stream(payload)
    with pytest.raises(StreamIntegrityError):
        stream.poll_once(received_at=T0)
    assert stream.state == StreamState.DEGRADED


def closed_payload(**changes) -> str:
    start_ms = int(T0.timestamp() * 1000)
    data = {
        "e": "kline",
        "E": start_ms + 60_000,
        "s": "BTCUSDT",
        "k": {
            "t": start_ms,
            "T": start_ms + 59_999,
            "s": "BTCUSDT",
            "i": "1m",
            "o": "100",
            "h": "101",
            "l": "99",
            "c": "100.5",
            "v": "1",
            "x": True,
        },
    }
    for key, value in changes.items():
        if key.startswith("k_"):
            data["k"][key[2:]] = value
        else:
            data[key] = value
    return json.dumps(data, separators=(",", ":"))


@pytest.mark.parametrize(
    "payload",
    [
        closed_payload(k_s="ETHUSDT"),
        closed_payload(k_x="yes"),
        closed_payload(k_t=int(T0.timestamp() * 1000) + 1),
        closed_payload(E=int(T0.timestamp() * 1000) + 59_998),
        closed_payload(k_o=100),
        closed_payload(k_o="bad"),
        closed_payload(k_h="98"),
    ],
)
def test_closed_kline_semantic_errors_fail_closed(payload) -> None:
    stream = active_stream(payload)
    with pytest.raises(StreamIntegrityError):
        stream.poll_once(received_at=T0 + timedelta(minutes=1))
    assert stream.state == StreamState.DEGRADED


@pytest.mark.parametrize(
    "overrides",
    [
        {"config_id": ""},
        {"activated_at": datetime(2026, 8, 11, 7, 0)},
        {"initial_nav": Decimal("0")},
        {"initial_nav": Decimal("NaN")},
        {"source_config_hash": "bad"},
        {"strategy_weights": {}},
        {"strategy_weights": {"bad id!": Decimal("1")}},
        {"strategy_weights": {"a": Decimal("0"), "b": Decimal("1")}},
    ],
)
def test_shadow_config_validation_edges(overrides) -> None:
    values = {
        "config_id": "cfg",
        "activated_at": T0,
        "initial_nav": Decimal("100"),
        "strategy_weights": {"a": Decimal("0.5"), "b": Decimal("0.5")},
        "source_config_hash": h("cfg"),
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        FrozenShadowConfig(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"strategy_id": "bad id!"},
        {"period_started_at": datetime(2026, 8, 11, 7, 0)},
        {"period_ended_at": T0},
        {"return_fraction": Decimal("NaN")},
        {"return_fraction": Decimal("-1")},
        {"source_fingerprint": "bad"},
    ],
)
def test_shadow_observation_validation_edges(overrides) -> None:
    values = {
        "strategy_id": "a",
        "period_started_at": T0,
        "period_ended_at": T0 + timedelta(minutes=1),
        "return_fraction": Decimal("0.01"),
        "source_fingerprint": h("source"),
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        StrategyShadowObservation(**values)


def make_shadow(path) -> SQLitePortfolioShadowRegistry:
    registry = SQLitePortfolioShadowRegistry(path)
    registry.register_config(
        FrozenShadowConfig(
            config_id="cfg",
            activated_at=T0,
            initial_nav=Decimal("100"),
            strategy_weights={"a": Decimal("0.5"), "b": Decimal("0.5")},
            source_config_hash=h("cfg"),
        )
    )
    return registry


def shadow_obs(strategy: str, start: datetime) -> StrategyShadowObservation:
    return StrategyShadowObservation(
        strategy_id=strategy,
        period_started_at=start,
        period_ended_at=start + timedelta(minutes=1),
        return_fraction=Decimal("0.01"),
        source_fingerprint=h(f"{strategy}:{start}"),
    )


def test_uninitialized_shadow_registry_and_duplicate_observation_fail_closed(tmp_path) -> None:
    uninitialized = SQLitePortfolioShadowRegistry(":memory:")
    with pytest.raises(ShadowIntegrityError):
        uninitialized.get_config()
    with pytest.raises(ShadowIntegrityError):
        uninitialized.list_records()
    with pytest.raises(ShadowIntegrityError):
        uninitialized.control_state()

    registry = make_shadow(tmp_path / "s.sqlite")
    with pytest.raises(ShadowIntegrityError):
        registry.append_period((shadow_obs("a", T0), shadow_obs("a", T0)))


def test_shadow_anchor_and_orphan_tamper_fail_closed(tmp_path) -> None:
    db = tmp_path / "s.sqlite"
    registry = make_shadow(db)
    registry.append_period((shadow_obs("a", T0), shadow_obs("b", T0)))
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM shadow_control")
        conn.commit()
    with pytest.raises(ShadowIntegrityError):
        registry.get_config()

    db2 = tmp_path / "s2.sqlite"
    registry2 = make_shadow(db2)
    registry2.append_period((shadow_obs("a", T0), shadow_obs("b", T0)))
    with sqlite3.connect(db2) as conn:
        conn.execute("DELETE FROM shadow_config")
        conn.commit()
    with pytest.raises(ShadowIntegrityError):
        registry2.list_records()


def test_shadow_control_hash_mutation_is_detected(tmp_path) -> None:
    db = tmp_path / "s.sqlite"
    registry = make_shadow(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE shadow_control SET control_hash = ?", (h("forged"),))
        conn.commit()
    with pytest.raises(ShadowIntegrityError):
        registry.control_state()


@pytest.mark.parametrize(
    "overrides",
    [
        {"campaign_id": ""},
        {"activated_at": datetime(2026, 8, 11, 7, 0)},
        {"shadow_config_fingerprint": "bad"},
        {"frozen_parameters_hash": "bad"},
        {"source_code_hash": "bad"},
    ],
)
def test_forward_policy_validation_edges(overrides) -> None:
    values = {
        "campaign_id": "forward",
        "activated_at": T0,
        "shadow_config_fingerprint": h("shadow"),
        "frozen_parameters_hash": h("params"),
        "source_code_hash": h("code"),
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        FrozenForwardPolicy(**values)


def make_forward(path, shadow_fingerprint: str) -> SQLiteForwardEvidenceRegistry:
    registry = SQLiteForwardEvidenceRegistry(path)
    registry.register_policy(
        FrozenForwardPolicy(
            campaign_id="forward",
            activated_at=T0,
            shadow_config_fingerprint=shadow_fingerprint,
            frozen_parameters_hash=h("params"),
            source_code_hash=h("code"),
        )
    )
    return registry


def test_uninitialized_forward_registry_and_anchor_tamper_fail_closed(tmp_path) -> None:
    uninitialized = SQLiteForwardEvidenceRegistry(":memory:")
    with pytest.raises(ForwardEvidenceIntegrityError):
        uninitialized.get_policy()
    with pytest.raises(ForwardEvidenceIntegrityError):
        uninitialized.list_records()
    with pytest.raises(ForwardEvidenceIntegrityError):
        uninitialized.control_state()

    shadow = make_shadow(tmp_path / "s.sqlite")
    db = tmp_path / "f.sqlite"
    forward = make_forward(db, shadow.get_config().fingerprint)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM forward_control")
        conn.commit()
    with pytest.raises(ForwardEvidenceIntegrityError):
        forward.get_policy()


def test_forward_policy_and_control_tamper_are_detected(tmp_path) -> None:
    shadow = make_shadow(tmp_path / "s.sqlite")
    db = tmp_path / "f.sqlite"
    forward = make_forward(db, shadow.get_config().fingerprint)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE forward_policy SET policy_json = '{}' WHERE slot = 1")
        conn.commit()
    with pytest.raises(ForwardEvidenceIntegrityError):
        forward.get_policy()

    db2 = tmp_path / "f2.sqlite"
    forward2 = make_forward(db2, shadow.get_config().fingerprint)
    with sqlite3.connect(db2) as conn:
        conn.execute("UPDATE forward_control SET control_hash = ?", (h("forged"),))
        conn.commit()
    with pytest.raises(ForwardEvidenceIntegrityError):
        forward2.control_state()


class ErrorCloseConnection:
    def recv(self, *, timeout: float):
        raise OSError("network")

    def close(self):
        raise WebSocketException("close")


def test_websocket_session_normalizes_os_receive_and_close_errors() -> None:
    session = WebsocketsReadOnlySession(ErrorCloseConnection())  # type: ignore[arg-type]
    with pytest.raises(OSError):
        session.receive(timeout_seconds=1)
    with pytest.raises(OSError):
        session.close()


def test_websocket_transport_rejects_invalid_timeout_before_connect(monkeypatch) -> None:
    called = False

    def fake_connect(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not connect")

    monkeypatch.setattr(stream_transport, "websocket_connect", fake_connect)
    with pytest.raises(ValueError):
        WebsocketsReadOnlyTransport().open(
            StreamOpenRequest(
                "wss://data-stream.binance.vision/ws/btcusdt@kline_1s",
                31,
            )
        )
    assert called is False
