from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import autotrade.first_canary_recovery as canonical_recovery
from autotrade.brokers import alpaca_paper_crypto_lifecycle as base


# Alpaca publishes a Tier-1 taker fee of 25 bps and charges a crypto BUY fee
# from the credited base asset. One broker quantity tick is allowed solely for
# decimal representation/rounding. This adapter is GET-only recovery scope: it
# cannot prepare, authorize, submit, or retry any broker POST.
MAX_RECEIVED_ASSET_FEE_RATE = Decimal("0.0025")
POSITION_ROUNDING_TOLERANCE = Decimal("0.000000001")


class FirstCanaryFeeAwareRecoveryError(RuntimeError):
    pass


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
    """Narrow GET-only recovery lifecycle for an already-burned first canary.

    Generic lifecycle rules stay unchanged. Only recovery of a first-canary BUY
    may reconcile Alpaca's gross filled_qty against a slightly smaller observed
    BTC position when the difference is bounded by the published received-asset
    crypto fee. Gross fill remains execution truth; net position remains exposure.
    """

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
    """Run canonical GET-only recovery with a narrow lifecycle adapter."""
    original = canonical_recovery.SQLiteCryptoPaperLifecycle
    if original is not base.SQLiteCryptoPaperLifecycle:
        raise FirstCanaryFeeAwareRecoveryError(
            "canonical recovery lifecycle was unexpectedly replaced"
        )
    canonical_recovery.SQLiteCryptoPaperLifecycle = FirstCanaryFeeAwareRecoveryLifecycle
    try:
        return canonical_recovery.recover_first_canary(**kwargs)
    finally:
        canonical_recovery.SQLiteCryptoPaperLifecycle = original


__all__ = [
    "FirstCanaryFeeAwareRecoveryError",
    "FirstCanaryFeeAwareRecoveryLifecycle",
    "MAX_RECEIVED_ASSET_FEE_RATE",
    "POSITION_ROUNDING_TOLERANCE",
    "recover_first_canary_fee_aware",
]
