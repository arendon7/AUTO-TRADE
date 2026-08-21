from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256

import autotrade.first_canary_recovery as canonical_recovery
from autotrade.brokers import alpaca_paper_crypto_lifecycle as base
from autotrade.brokers import alpaca_paper_crypto_reconciliation as recon


MAX_RECEIVED_ASSET_FEE_RATE = Decimal("0.0025")
POSITION_ROUNDING_TOLERANCE = Decimal("0.000000001")


class FirstCanaryFeeAwareRecoveryError(RuntimeError):
    pass


def _broker_position_symbol(canonical_symbol: str) -> str:
    symbol = recon.normalize_crypto_pair(canonical_symbol)
    base_asset, quote_asset = symbol.split("/", 1)
    return base_asset + quote_asset


def _canonical_position_response_symbol(raw_symbol: str, *, expected_symbol: str) -> str:
    expected = recon.normalize_crypto_pair(expected_symbol)
    observed = raw_symbol.strip().upper()
    if observed in {expected, _broker_position_symbol(expected)}:
        return expected
    raise recon.CryptoPaperReconciliationIntegrityError(
        "reconciled crypto position identity mismatch"
    )


def _parse_first_canary_position(
    *,
    response: recon.AlpacaPaperHttpResponse,
    expected_symbol: str,
    credential_reference: str,
    observed_at: datetime,
) -> recon.CryptoBrokerPositionSnapshot:
    if (
        not isinstance(credential_reference, str)
        or not recon._HASH_RE.fullmatch(credential_reference)
    ):
        raise recon.CryptoPaperReconciliationIntegrityError(
            "crypto position credential reference is invalid"
        )
    if response.status_code == 404:
        return recon.CryptoBrokerPositionSnapshot(
            symbol=expected_symbol,
            quantity=Decimal("0"),
            market_value=None,
            average_entry_price=None,
            credential_reference=credential_reference,
            request_id=recon._request_id(response),
            response_sha256=sha256(response.body).hexdigest(),
            observed_at=observed_at,
            absent=True,
        )
    if response.status_code != 200:
        raise recon.AlpacaPaperUnavailable(
            f"unexpected crypto position reconciliation status: {response.status_code}"
        )
    payload, request_id = recon._json_payload(
        response, "first-canary crypto position reconciliation"
    )
    symbol = _canonical_position_response_symbol(
        recon._string(payload, "symbol"), expected_symbol=expected_symbol
    )
    if recon._string(payload, "asset_class").lower() != "crypto":
        raise recon.CryptoPaperReconciliationIntegrityError(
            "reconciled crypto position identity mismatch"
        )
    quantity = recon._decimal(payload.get("qty"), "position qty", nonnegative=True)
    side = recon._string(payload, "side").lower()
    if quantity > 0 and side != "long":
        raise recon.CryptoPaperReconciliationIntegrityError(
            "R6 crypto position must be long-only"
        )
    market_value = recon._optional_decimal(
        payload.get("market_value"), "market_value", allow_negative=False
    )
    average_entry_price = recon._optional_decimal(
        payload.get("avg_entry_price"), "avg_entry_price", allow_negative=False
    )
    return recon.CryptoBrokerPositionSnapshot(
        symbol=symbol,
        quantity=quantity,
        market_value=market_value,
        average_entry_price=average_entry_price,
        credential_reference=credential_reference,
        request_id=request_id,
        response_sha256=sha256(response.body).hexdigest(),
        observed_at=observed_at,
        absent=False,
    )


class FirstCanaryCompactPositionReconciliationGateway(
    recon.AlpacaPaperCryptoReconciliationGateway
):
    """GET-only first-canary gateway using Alpaca's BASEQUOTE position identifier."""

    def reconcile(self, *, credentials, order, now):
        if not self._config.enabled:
            raise recon.CryptoPaperReconciliationDisabled(
                "crypto PAPER reconciliation is disabled"
            )
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if not recon._CLIENT_ID_RE.fullmatch(order.client_order_id):
            raise ValueError("crypto client_order_id is invalid")
        symbol = recon.normalize_crypto_pair(order.symbol)
        observed_at = now.astimezone(timezone.utc)

        order_url = (
            "https"
            + "://"
            + recon.ALPACA_PAPER_TRADING_HOST
            + recon.ORDER_BY_CLIENT_PATH
            + "?client_order_id="
            + order.client_order_id
        )
        order_policy = recon._ExactReadPolicy(order_url)
        order_transport = self._order_transport or recon.UrllibAlpacaPaperReadTransport(
            policy=order_policy,
            max_response_bytes=self._config.max_response_bytes,
        )
        order_response = order_transport.read(
            recon._request(
                credentials=credentials,
                url=order_url,
                timeout=self._config.timeout_seconds,
            )
        )
        order_policy.validate_final_url(order_response.final_url)
        if order_response.status_code == 404:
            broker_order = None
            order_absence = recon._parse_order_absence(
                response=order_response,
                expected_client_order_id=order.client_order_id,
                credential_reference=credentials.credential_reference,
                observed_at=observed_at,
            )
        else:
            broker_order = recon._parse_order(
                response=order_response,
                expected=order,
                observed_at=observed_at,
            )
            order_absence = None

        position_url = (
            "https"
            + "://"
            + recon.ALPACA_PAPER_TRADING_HOST
            + recon.POSITION_PATH_PREFIX
            + _broker_position_symbol(symbol)
        )
        position_policy = recon._ExactReadPolicy(position_url)
        position_transport = self._position_transport or recon.UrllibAlpacaPaperReadTransport(
            policy=position_policy,
            max_response_bytes=self._config.max_response_bytes,
        )
        position_response = position_transport.read(
            recon._request(
                credentials=credentials,
                url=position_url,
                timeout=self._config.timeout_seconds,
            )
        )
        position_policy.validate_final_url(position_response.final_url)
        position = _parse_first_canary_position(
            response=position_response,
            expected_symbol=symbol,
            credential_reference=credentials.credential_reference,
            observed_at=observed_at,
        )
        if order_absence is not None:
            return recon.CryptoBrokerUnknownReconciliation(
                order_absence=order_absence,
                position=position,
                observed_at=observed_at,
            )
        assert broker_order is not None
        return recon.CryptoBrokerReconciliation(
            order=broker_order,
            position=position,
            observed_at=observed_at,
        )


def _validate_fee_adjusted_net_position(
    *, filled_quantity: Decimal, confirmed_net_long_quantity: Decimal
) -> None:
    if filled_quantity == 0:
        if confirmed_net_long_quantity != 0:
            raise base.CryptoLifecycleIntegrityError(
                "zero entry fill requires zero confirmed net long position"
            )
        return
    if confirmed_net_long_quantity <= 0:
        raise base.CryptoLifecycleIntegrityError(
            "positive entry fill requires positive confirmed net long position"
        )
    if confirmed_net_long_quantity > filled_quantity:
        raise base.CryptoLifecycleIntegrityError(
            "confirmed net long position may not exceed cumulative entry fills"
        )
    deficit = filled_quantity - confirmed_net_long_quantity
    maximum = (
        filled_quantity * MAX_RECEIVED_ASSET_FEE_RATE
        + POSITION_ROUNDING_TOLERANCE
    )
    if deficit > maximum:
        raise base.CryptoLifecycleIntegrityError(
            "entry fill/net long position deficit exceeds conservative Alpaca received-asset fee allowance "
            f"(gross={base._decimal_text(filled_quantity)}, "
            f"net={base._decimal_text(confirmed_net_long_quantity)}, "
            f"deficit={base._decimal_text(deficit)}, max={base._decimal_text(maximum)})"
        )


class FirstCanaryFeeAwareRecoveryLifecycle(base.SQLiteCryptoPaperLifecycle):
    """Narrow GET-only recovery lifecycle for an already-burned first canary."""

    def reconcile_entry(
        self,
        lifecycle_id: str,
        *,
        broker_order_id: str,
        broker_status: str,
        filled_quantity: Decimal,
        confirmed_net_long_quantity: Decimal,
        at: datetime,
    ) -> base.CryptoLifecycleState:
        status = broker_status.strip().lower()
        payload = {
            "broker_order_id": broker_order_id,
            "broker_status": status,
            "filled_quantity": base._decimal_text(filled_quantity),
            "confirmed_net_long_quantity": base._decimal_text(
                confirmed_net_long_quantity
            ),
            "fee_aware_first_canary_recovery": True,
        }

        def transition(
            binding: base.CryptoLifecycleBinding,
            state: base.CryptoLifecycleState,
            _payload: dict[str, object],
        ) -> base.CryptoLifecycleState:
            if state.status not in {
                base.CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN,
                base.CryptoLifecycleStatus.ENTRY_ACKNOWLEDGED,
                base.CryptoLifecycleStatus.ENTRY_PARTIALLY_FILLED,
            }:
                raise base.CryptoLifecycleBlocked(
                    "entry reconciliation is not valid from current lifecycle state"
                )
            base._validate_id(broker_order_id, "entry broker_order_id")
            base._nonnegative(filled_quantity, "filled_quantity")
            base._nonnegative(
                confirmed_net_long_quantity, "confirmed_net_long_quantity"
            )
            if status not in base._ENTRY_OPEN | base._ENTRY_TERMINAL:
                raise base.CryptoLifecycleIntegrityError(
                    "unsupported entry broker status"
                )
            if state.entry_broker_order_id not in (None, broker_order_id):
                raise base.CryptoLifecycleIntegrityError(
                    "entry broker order id changed"
                )
            if filled_quantity < state.entry_filled_quantity:
                raise base.CryptoLifecycleIntegrityError(
                    "entry cumulative filled quantity regressed"
                )
            if filled_quantity > binding.entry_quantity:
                raise base.CryptoLifecycleIntegrityError(
                    "entry cumulative fill exceeds intended quantity"
                )
            _validate_fee_adjusted_net_position(
                filled_quantity=filled_quantity,
                confirmed_net_long_quantity=confirmed_net_long_quantity,
            )
            terminal = status in base._ENTRY_TERMINAL
            if status == "filled" and filled_quantity != binding.entry_quantity:
                raise base.CryptoLifecycleIntegrityError(
                    "filled entry status requires exact intended quantity"
                )
            if status in {"accepted", "pending_new", "new"} and filled_quantity != 0:
                raise base.CryptoLifecycleIntegrityError(
                    "unfilled entry status may not report cumulative fill"
                )
            if status == "partially_filled" and not (
                Decimal("0") < filled_quantity < binding.entry_quantity
            ):
                raise base.CryptoLifecycleIntegrityError(
                    "partially_filled entry requires strict partial quantity"
                )

            if terminal and filled_quantity == 0:
                next_status = base.CryptoLifecycleStatus.ENTRY_TERMINAL_NO_FILL
            elif terminal and filled_quantity > 0:
                next_status = base.CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED
            elif filled_quantity > 0:
                next_status = base.CryptoLifecycleStatus.ENTRY_PARTIALLY_FILLED
            else:
                next_status = base.CryptoLifecycleStatus.ENTRY_ACKNOWLEDGED
            return replace(
                state,
                status=next_status,
                entry_broker_order_id=broker_order_id,
                entry_broker_status=status,
                entry_filled_quantity=filled_quantity,
                entry_terminal=terminal,
                confirmed_net_long_quantity=confirmed_net_long_quantity,
            )

        return self._mutate(
            lifecycle_id,
            at=at,
            event_type=base.CryptoLifecycleEventType.ENTRY_RECONCILED,
            payload=payload,
            transition=transition,
        )


def recover_first_canary_fee_aware(**kwargs):
    """Run canonical GET-only recovery with narrow lifecycle and position adapters."""
    original = canonical_recovery.SQLiteCryptoPaperLifecycle
    if original is not base.SQLiteCryptoPaperLifecycle:
        raise FirstCanaryFeeAwareRecoveryError(
            "canonical recovery lifecycle was unexpectedly replaced"
        )

    # The real recovery receives the compact-position gateway. Tests that replace
    # the canonical recovery function keep their historical kwargs contract.
    is_real_recovery = (
        canonical_recovery.recover_first_canary.__module__
        == canonical_recovery.__name__
    )
    if is_real_recovery and kwargs.get("reconciliation_gateway") is None:
        kwargs["reconciliation_gateway"] = FirstCanaryCompactPositionReconciliationGateway(
            config=recon.AlpacaPaperGatewayConfig(enabled=True)
        )

    canonical_recovery.SQLiteCryptoPaperLifecycle = FirstCanaryFeeAwareRecoveryLifecycle
    try:
        return canonical_recovery.recover_first_canary(**kwargs)
    finally:
        canonical_recovery.SQLiteCryptoPaperLifecycle = original


__all__ = [
    "FirstCanaryCompactPositionReconciliationGateway",
    "FirstCanaryFeeAwareRecoveryError",
    "FirstCanaryFeeAwareRecoveryLifecycle",
    "MAX_RECEIVED_ASSET_FEE_RATE",
    "POSITION_ROUNDING_TOLERANCE",
    "recover_first_canary_fee_aware",
]
