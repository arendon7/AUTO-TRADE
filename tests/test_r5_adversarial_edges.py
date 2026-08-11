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


def sub(**overrides) -> ClosedKlineSubscription:
    values = {"instrument": instrument(), "interval": "1m", "enabled": True}
    values.update(overrides)
    return ClosedKlineSubscription(**values)


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"instrument": instrument(venue="ALPACA")}, "BINANCE_SPOT"),
        ({"instrument": instrument(symbol="btcusdt")}, "symbol"),
        ({"interval": "2m"}, "unsupported"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": 31}, "timeout_seconds"),
        ({"max_event_lag_seconds": 0}, "max_event_lag_seconds"),
        ({"max_event_lag_seconds": 301}, "max_event_lag_seconds"),
        ({"future_tolerance_seconds": -1}, "future_tolerance_seconds"),
        ({"future_tolerance_seconds": 6}, "future_tolerance_seconds"),
    ],
)
def test_subscription_rejects_invalid_authority_or_bounds(overrides, match) -> None:
    with pytest.raises(ValueError, match=match):
        sub(**overrides)


def test_subscription_derives_exact_market_data_url_and_timeframe() -> None:
    value = sub(interval="1s")
    assert value.stream_name == "btcusdt@kline_1s"
    assert value.url == "wss://data-stream.binance.vision/ws/btcusdt@kline_1s"
    assert value.timeframe_seconds == 1


@pytest.mark.parametrize(
    "request,stream_name,match",
    [
        (StreamOpenRequest("wss://data-stream.binance.vision/ws/btcusdt@kline_1m", 1, False), "btcusdt@kline_1m", "read-only"),
        (StreamOpenRequest("wss://data-stream.binance.vision/ws/btcusdt@kline_1m", 0), "btcusdt@kline_1m", "timeout"),
        (StreamOpenRequest("wss://data-stream.binance.vision/ws/btcusdt@kline_1m", 31), "btcusdt@kline_1m", "timeout"),
        (StreamOpenRequest("wss://data-stream.binance.vision/ws/btcusdt@kline_1m", 1), "BAD", "stream name"),
        (StreamOpenRequest("ws://data-stream.binance.vision/ws/btcusdt@kline_1m", 1), "btcusdt@kline_1m", "WSS"),
        (StreamOpenRequest("wss://user:pass@data-stream.binance.vision/ws/btcusdt@kline_1m", 1), "btcusdt@kline_1m", "credentials"),
        (StreamOpenRequest("wss://data-stream.binance.vision:8443/ws/btcusdt@kline_1m", 1), "btcusdt@kline_1m", "host"),
    ],
)
def test_stream_policy_rejects_noncanonical_requests(request, stream_name, match) -> None:
    with pytest.raises(StreamPolicyError, match=match):
        ClosedKlineStreamPolicy().validate(request, expected_stream_name=stream_name)


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

    def open(self, request: StreamOpenRequest):
        self.calls += 1
        return self.session


def test_open_is_idempotent_only_while_same_session_is_active() -> None:
    transport = PassiveTransport()
    stream = ClosedKlineStream(subscription=sub(), transport=transport)
    stream.open()
    stream.open()
    assert transport.calls == 1
    assert stream.state == StreamState.ACTIVE
    stream.close()
    assert transport.session.closed is True
    with pytest.raises(StreamUnavailable, match="closed stream"):
        stream.open()


def test_poll_and_direct_ingest_require_active_session() -> None:
    stream = ClosedKlineStream(subscription=sub(), transport=PassiveTransport())
    with pytest.raises(StreamUnavailable, match="ACTIVE"):
        stream.poll_once(received_at=T0)
    with pytest.raises(StreamUnavailable, match="ACTIVE"):
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

    def open(self, request: StreamOpenRequest):
        return self.session


def active_stream(payload: str | bytes) -> ClosedKlineStream:
    stream = ClosedKlineStream(subscription=sub(), transport=QueueTransport(payload))
    stream.open()
    return stream


@pytest.mark.parametrize(
    "payload,match",
    [
        (b"\xff", "UTF-8"),
        ("[]", "root"),
        ('{"value":NaN}', "strict JSON"),
        ('{"e":"kline","s":"BTCUSDT"}', "missing object"),
        ('{"e":"trade","s":"BTCUSDT","k":{}}', "event type"),
    ],
)
def test_stream_payload_envelope_errors_degrade(payload, match) -> None:
    stream = active_stream(payload)
    with pytest.raises(StreamIntegrityError, match=match):
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
    "payload,match",
    [
        (closed_payload(k_s="ETHUSDT"), "kline symbol"),
        (closed_payload(k_x="yes"), "closed flag"),
        (closed_payload(k_t=int(T0.timestamp() * 1000) + 1), "interval-aligned"),
        (closed_payload(E=int(T0.timestamp() * 1000) + 59_998), "precedes kline close"),
        (closed_payload(k_o=100), "decimal string"),
        (closed_payload(k_o="bad"), "valid decimal"),
        (closed_payload(k_h="98"), "invalid closed kline"),
    ],
)
def test_closed_kline_semantic_errors_fail_closed(payload, match) -> None:
    stream = active_stream(payload)
    with pytest.raises(StreamIntegrityError, match=match):
        stream.poll_once(received_at=T0 + timedelta(minutes=1))
    assert stream.state == StreamState.DEGRADED


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"config_id": ""}, "config_id"),
        ({"activated_at": datetime(2026, 8, 11, 7, 0)}, "timezone-aware"),
        ({"initial_nav": Decimal("0")}, "initial_nav"),
        ({"initial_nav": Decimal("NaN")}, "initial_nav"),
        ({"source_config_hash": "bad"}, "source_config_hash"),
        ({"strategy_weights": {}}, "cannot be empty"),
        ({"strategy_weights": {"bad id!": Decimal("1")}}, "strategy_id"),
        ({"strategy_weights": {"a": Decimal("0"), "b": Decimal("1")}}, "finite and > 0"),
    ],
)
def test_shadow_config_validation_edges(kwargs, match) -> None:
    base = {
        "config_id": "cfg",
        "activated_at": T0,
        "initial_nav": Decimal("100"),
        "strategy_weights": {"a": Decimal("0.5"), "b": Decimal("0.5")},
        "source_config_hash": h("cfg"),
    }
    base.update(kwargs)
    with pytest.raises(ValueError, match=match):
        FrozenShadowConfig(**base)


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"strategy_id": "bad id!"}, "strategy_id"),
        ({"period_started_at": datetime(2026, 8, 11, 7, 0)}, "timezone-aware"),
        ({"period_ended_at": T0}, "positive duration"),
        ({"return_fraction": Decimal("NaN")}, "finite Decimal"),
        ({"return_fraction": Decimal("-1")}, "greater than -1"),
        ({"source_fingerprint": "bad"}, "source_fingerprint"),
    ],
)
def test_shadow_observation_validation_edges(kwargs, match) -> None:
    base = {
        "strategy_id": "a",
        "period_started_at": T0,
        "period_ended_at": T0 + timedelta(minutes=1),
        "return_fraction": Decimal("0.01"),
        "source_fingerprint": h("source"),
    }
    base.update(kwargs)
    with pytest.raises(ValueError, match=match):
        StrategyShadowObservation(**base)


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


def shadow_obs(strategy: str, start: datetime, value: str = "0.01") -> StrategyShadowObservation:
    return StrategyShadowObservation(
        strategy_id=strategy,
        period_started_at=start,
        period_ended_at=start + timedelta(minutes=1),
        return_fraction=Decimal(value),
        source_fingerprint=h(f"{strategy}:{start}:{value}"),
    )


def test_uninitialized_shadow_registry_fails_closed() -> None:
    registry = SQLitePortfolioShadowRegistry(":memory:")
    with pytest.raises(ShadowIntegrityError, match="not initialized"):
        registry.get_config()
    with pytest.raises(ShadowIntegrityError, match="not initialized"):
        registry.list_records()
    with pytest.raises(ShadowIntegrityError, match="not initialized"):
        registry.control_state()


def test_shadow_duplicate_strategy_observation_fails_closed(tmp_path) -> None:
    registry = make_shadow(tmp_path / "s.sqlite")
    with pytest.raises(ShadowIntegrityError, match="duplicate strategy"):
        registry.append_period((shadow_obs("a", T0), shadow_obs("a", T0)))


def test_shadow_missing_control_anchor_is_detected(tmp_path) -> None:
    db = tmp_path / "s.sqlite"
    registry = make_shadow(db)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM shadow_control")
        conn.commit()
    with pytest.raises(ShadowIntegrityError, match="control anchor is missing"):
        registry.get_config()


def test_shadow_orphaned_record_without_config_is_detected(tmp_path) -> None:
    db = tmp_path / "s.sqlite"
    registry = make_shadow(db)
    registry.append_period((shadow_obs("a", T0), shadow_obs("b", T0)))
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM shadow_config")
        conn.commit()
    with pytest.raises(ShadowIntegrityError, match="records exist without config"):
        registry.list_records()


def test_shadow_control_hash_mutation_is_detected(tmp_path) -> None:
    db = tmp_path / "s.sqlite"
    registry = make_shadow(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE shadow_control SET control_hash = ?", (h("forged"),))
        conn.commit()
    with pytest.raises(ShadowIntegrityError, match="control hash mismatch"):
        registry.control_state()


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"campaign_id": ""}, "campaign_id"),
        ({"activated_at": datetime(2026, 8, 11, 7, 0)}, "timezone-aware"),
        ({"shadow_config_fingerprint": "bad"}, "shadow_config_fingerprint"),
        ({"frozen_parameters_hash": "bad"}, "frozen_parameters_hash"),
        ({"source_code_hash": "bad"}, "source_code_hash"),
    ],
)
def test_forward_policy_validation_edges(kwargs, match) -> None:
    base = {
        "campaign_id": "forward",
        "activated_at": T0,
        "shadow_config_fingerprint": h("shadow"),
        "frozen_parameters_hash": h("params"),
        "source_code_hash": h("code"),
    }
    base.update(kwargs)
    with pytest.raises(ValueError, match=match):
        FrozenForwardPolicy(**base)


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


def test_uninitialized_forward_registry_fails_closed() -> None:
    registry = SQLiteForwardEvidenceRegistry(":memory:")
    with pytest.raises(ForwardEvidenceIntegrityError, match="not initialized"):
        registry.get_policy()
    with pytest.raises(ForwardEvidenceIntegrityError, match="not initialized"):
        registry.list_records()
    with pytest.raises(ForwardEvidenceIntegrityError, match="not initialized"):
        registry.control_state()


def test_forward_missing_control_anchor_is_detected(tmp_path) -> None:
    shadow = make_shadow(tmp_path / "s.sqlite")
    db = tmp_path / "f.sqlite"
    forward = make_forward(db, shadow.get_config().fingerprint)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM forward_control")
        conn.commit()
    with pytest.raises(ForwardEvidenceIntegrityError, match="control anchor is missing"):
        forward.get_policy()


def test_forward_policy_tamper_is_detected(tmp_path) -> None:
    shadow = make_shadow(tmp_path / "s.sqlite")
    db = tmp_path / "f.sqlite"
    forward = make_forward(db, shadow.get_config().fingerprint)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE forward_policy SET policy_json = '{}' WHERE slot = 1")
        conn.commit()
    with pytest.raises(ForwardEvidenceIntegrityError, match="invalid persisted forward policy"):
        forward.get_policy()


def test_forward_control_hash_mutation_is_detected(tmp_path) -> None:
    shadow = make_shadow(tmp_path / "s.sqlite")
    db = tmp_path / "f.sqlite"
    forward = make_forward(db, shadow.get_config().fingerprint)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE forward_control SET control_hash = ?", (h("forged"),))
        conn.commit()
    with pytest.raises(ForwardEvidenceIntegrityError, match="control hash mismatch"):
        forward.control_state()


class ErrorCloseConnection:
    def recv(self, *, timeout: float):
        raise OSError("network")

    def close(self):
        raise WebSocketException("close")


def test_websocket_session_normalizes_os_receive_and_close_errors() -> None:
    session = WebsocketsReadOnlySession(ErrorCloseConnection())  # type: ignore[arg-type]
    with pytest.raises(OSError, match="receive failed"):
        session.receive(timeout_seconds=1)
    with pytest.raises(OSError, match="close failed"):
        session.close()


def test_websocket_transport_rejects_invalid_timeout_before_connect(monkeypatch) -> None:
    called = False

    def fake_connect(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not connect")

    monkeypatch.setattr(stream_transport, "websocket_connect", fake_connect)
    transport = WebsocketsReadOnlyTransport()
    with pytest.raises(ValueError, match="open timeout"):
        transport.open(
            StreamOpenRequest(
                "wss://data-stream.binance.vision/ws/btcusdt@kline_1s",
                31,
            )
        )
    assert called is False
