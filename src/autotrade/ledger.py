from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Mapping, Protocol


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    event_id: str
    event_type: str
    occurred_at: datetime
    payload: Mapping[str, str]


class EventLedger(Protocol):
    def append(self, event: LedgerEvent) -> None: ...

    def all_events(self) -> tuple[LedgerEvent, ...]: ...


class DuplicateLedgerEvent(ValueError):
    pass


class InMemoryEventLedger:
    """Append-only ledger for tests and paper mode.

    Production persistence will replace this implementation while preserving
    the same contract.
    """

    def __init__(self) -> None:
        self._events: list[LedgerEvent] = []
        self._ids: set[str] = set()
        self._lock = RLock()

    def append(self, event: LedgerEvent) -> None:
        with self._lock:
            if event.event_id in self._ids:
                raise DuplicateLedgerEvent(event.event_id)
            self._events.append(event)
            self._ids.add(event.event_id)

    def all_events(self) -> tuple[LedgerEvent, ...]:
        with self._lock:
            return tuple(self._events)
