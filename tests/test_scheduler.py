from __future__ import annotations

import threading
import time

from coffee_vram_plugin.coffee.scheduler import QUEUED, RUNNING, Scheduler


def test_calls_run_in_arrival_order_one_at_a_time():
    sched = Scheduler(max_concurrent=1)
    order: list[str] = []
    first = sched.enqueue("a")
    assert sched.wait_turn(first, 1)
    tickets = [sched.enqueue(name) for name in ("b", "c")]

    def worker(ticket):
        assert sched.wait_turn(ticket, 5)
        order.append(ticket.model)
        sched.release(ticket)

    threads = [threading.Thread(target=worker, args=(t,)) for t in reversed(tickets)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    assert order == []
    sched.release(first)
    for t in threads:
        t.join(5)
    assert order == ["b", "c"]


def test_timeout_leaves_ticket_queued_and_force_start_runs_it():
    sched = Scheduler(max_concurrent=1)
    holder = sched.enqueue("a")
    sched.wait_turn(holder, 1)
    late = sched.enqueue("b")
    assert sched.wait_turn(late, 0.05) is False and late.state == QUEUED
    sched.force_start(late)
    assert sched.in_flight() == {"a", "b"}


def test_release_records_last_use_and_frees_the_slot():
    sched = Scheduler(max_concurrent=1)
    ticket = sched.enqueue("a")
    sched.wait_turn(ticket, 1)
    assert ticket.state == RUNNING and sched.in_flight() == {"a"}
    before = time.time()
    sched.release(ticket)
    assert sched.in_flight() == set() and sched.last_used()["a"] >= before
    nxt = sched.enqueue("b")
    assert sched.wait_turn(nxt, 0.5)


def test_more_slots_admit_concurrent_calls():
    sched = Scheduler(max_concurrent=2)
    a, b = sched.enqueue("a"), sched.enqueue("b")
    assert sched.wait_turn(a, 1) and sched.wait_turn(b, 1)
    assert [t.state for t in sched.tickets()] == [RUNNING, RUNNING]
