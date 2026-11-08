"""The ``llm_execution`` middleware: hold local Ollama calls until their model fits, never forever.

Fail-open by design: a timeout, a stop, missing telemetry or a model that can never fit lets the
call through with a notification. Calls to any other endpoint pass untouched.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .monitor import Monitor
from .ollama import same_endpoint
from .policy import EVICT, GO, PASS, decide
from .scheduler import RUNNING, Ticket
from .settings import Settings
from .snapshot import GIB

LONG_WAIT_S = 30

# Under plugins.isolation: host, ``next_call`` cannot cross the process boundary and arrives as a
# placeholder. Raising hands the call to the next layer (Hermes logs the first one, then DEBUG).
RELEASE_MESSAGE = "coffee-vram: admitted; continuing to the provider (plugin host mode)"


class Released(RuntimeError):
    pass


def _interrupted() -> bool:
    """The agent's stop button, when the gate runs on the agent's own thread (in-process)."""
    try:
        from tools.interrupt import is_interrupted
    except ImportError:
        return False
    return is_interrupted()


def _proceed(next_call: Any) -> Any:
    if callable(next_call):
        return next_call()
    raise Released(RELEASE_MESSAGE)


def admit(monitor: Monitor, s: Settings, model: str, session_id: str) -> Ticket:
    """Queue ``model``'s call and return its running ticket once it may go (or must, fail-open)."""
    sched = monitor.scheduler
    sched.set_max_concurrent(s.max_concurrent_local)
    # A second call for a model already queued/running shares that load — don't re-hold on VRAM.
    already = sched.has_model(model)
    est = monitor.estimate(model)
    ticket = sched.enqueue(model, session_id, est.total)
    try:
        if already:
            _wait_for_slot(monitor, s, ticket)
        else:
            _wait_for_room(monitor, s, ticket, est)
    except BaseException:
        sched.release(ticket)
        raise
    return ticket


def _wait_for_slot(monitor: Monitor, s: Settings, ticket: Ticket) -> None:
    """Concurrency only: same model is already in the queue, so skip a fresh VRAM estimate hold."""
    sched, model = monitor.scheduler, ticket.model
    deadline = time.monotonic() + s.queue_timeout_s
    while ticket.state != RUNNING:
        if _interrupted() or time.monotonic() >= deadline:
            break
        if sched.wait_turn(ticket, min(1.0, deadline - time.monotonic())):
            return
        sched.wait_for_change(min(1.0, deadline - time.monotonic()))
    if ticket.state != RUNNING:
        monitor.emit("timeout", f"{model} waited for a slot behind the same model; running anyway",
                     level="alert", model=model)
        sched.force_start(ticket)


def _wait_for_room(monitor: Monitor, s: Settings, ticket: Ticket, est) -> None:
    sched, model = monitor.scheduler, ticket.model
    started_at = time.monotonic()
    deadline = started_at + s.queue_timeout_s
    has_slot = queued_notice = long_notice = False
    while True:
        now = time.monotonic()
        if _interrupted():
            break
        if now >= deadline:
            monitor.emit("timeout", f"{model} waited {s.queue_timeout_s}s for GPU memory; running anyway",
                         level="alert", model=model)
            break
        if not long_notice and now - started_at >= LONG_WAIT_S:
            long_notice = True
            monitor.emit("long_wait", f"{model} has been waiting {LONG_WAIT_S}s for GPU memory",
                         level="alert", model=model)
        if not has_slot:
            has_slot = sched.wait_turn(ticket, min(1.0, deadline - now))
            if not has_slot:
                if not queued_notice:
                    queued_notice = True
                    monitor.emit("queued", f"{model} is queued behind other local calls", level="warn",
                                 model=model)
                continue
        snap = monitor.snapshot(fresh=True)
        if snap.total <= 0 or est.total <= 0:
            break
        host = monitor.host_stats()
        decision = decide(snap, est, s, last_used=monitor.last_active(), in_flight=sched.in_flight(),
                          now=time.time(), ram_total=host.ram_total, ram_used=host.ram_used)
        if decision.partial:
            monitor.note_partial(model)
        if decision.action == GO:
            if decision.warn:
                label = {"gpu": "GPU memory", "system_ram": "system memory",
                         "combined": "memory"}.get(decision.pool, "GPU memory")
                monitor.emit("warn", f"{model} brings {label} to {decision.after_pct:.0f}%", level="warn",
                             model=model)
            break
        if decision.action == PASS:
            monitor.emit("overflow", f"{decision.reason}; running anyway", level="warn", model=model,
                         need_gb=round(decision.need / GIB, 1))
            break
        if decision.action == EVICT:
            for name in decision.evict:
                monitor.unload(name, f"making room for {model}")
            sched.wait_for_change(0.5)
            continue
        if not queued_notice:
            queued_notice = True
            monitor.emit("queued", f"Holding {model}: {decision.reason}", level="warn", model=model,
                         need_gb=round(decision.need / GIB, 1), free_gb=round(decision.free / GIB, 1))
        sched.wait_for_change(min(2.0, deadline - now))
    if ticket.state != RUNNING:
        sched.force_start(ticket)


def make_gate(monitor: Monitor) -> Callable[..., Any]:
    def coffee_llm_gate(*, request: Any = None, next_call: Any = None, model: str | None = None,
                        base_url: str | None = None, session_id: str = "", api_request_id: str = "",
                        **_: Any) -> Any:
        s = monitor.settings()
        if not model or not same_endpoint(base_url, s.ollama_url):
            return _proceed(next_call)
        monitor.ensure_started()
        ticket = admit(monitor, s, model, session_id)
        if not callable(next_call):
            monitor.hold_until_done(api_request_id, ticket)
            raise Released(RELEASE_MESSAGE)
        try:
            return next_call()
        finally:
            monitor.scheduler.release(ticket)

    return coffee_llm_gate
