"""FIFO slots for local calls, plus the LRU and in-flight views the policy needs.

Synchronous on purpose: Hermes runs ``llm_execution`` middleware on the agent's worker thread.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field

QUEUED = "queued"
RUNNING = "running"


@dataclass
class Ticket:
    id: int
    model: str
    session_id: str
    est_bytes: int
    enqueued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    state: str = QUEUED


class Scheduler:
    def __init__(self, max_concurrent: int = 1) -> None:
        self._cond = threading.Condition()
        self._ids = itertools.count(1)
        self._queue: list[Ticket] = []
        self._running: list[Ticket] = []
        self._last_used: dict[str, float] = {}
        self._max = max(1, max_concurrent)

    def set_max_concurrent(self, value: int) -> None:
        with self._cond:
            self._max = max(1, value)
            self._cond.notify_all()

    def enqueue(self, model: str, session_id: str = "", est_bytes: int = 0) -> Ticket:
        with self._cond:
            ticket = Ticket(id=next(self._ids), model=model, session_id=session_id, est_bytes=est_bytes)
            self._queue.append(ticket)
            return ticket

    def _start(self, ticket: Ticket) -> None:
        if ticket in self._queue:
            self._queue.remove(ticket)
        if ticket not in self._running:
            ticket.state, ticket.started_at = RUNNING, time.time()
            self._running.append(ticket)
        self._cond.notify_all()

    def wait_turn(self, ticket: Ticket, timeout: float) -> bool:
        """Block until ``ticket`` heads the queue and a slot is free; False on timeout (still queued)."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while ticket.state == QUEUED:
                if self._queue and self._queue[0] is ticket and len(self._running) < self._max:
                    self._start(ticket)
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    def force_start(self, ticket: Ticket) -> None:
        """Run ``ticket`` without a slot: the fail-open path after a timeout."""
        with self._cond:
            self._start(ticket)

    def release(self, ticket: Ticket) -> None:
        with self._cond:
            if ticket in self._queue:
                self._queue.remove(ticket)
            if ticket in self._running:
                self._running.remove(ticket)
                self._last_used[ticket.model] = time.time()
            self._cond.notify_all()

    def wait_for_change(self, timeout: float) -> None:
        """Sleep until a slot opens, a call finishes, or ``poke`` — at most ``timeout`` seconds."""
        with self._cond:
            self._cond.wait(max(0.0, timeout))

    def poke(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def touch(self, model: str, when: float | None = None) -> None:
        with self._cond:
            self._last_used[model] = time.time() if when is None else when

    def in_flight(self) -> set[str]:
        with self._cond:
            return {t.model for t in self._running}

    def has_model(self, model: str) -> bool:
        """True when ``model`` already has a queued or running ticket."""
        with self._cond:
            return any(t.model == model for t in (*self._running, *self._queue))

    def last_used(self) -> dict[str, float]:
        with self._cond:
            return dict(self._last_used)

    def tickets(self) -> list[Ticket]:
        """Running first, then the queue in order; copies, safe to serialize."""
        with self._cond:
            return [Ticket(**t.__dict__) for t in (*self._running, *self._queue)]
