from __future__ import annotations

import threading
import time

import pytest

from coffee_vram_plugin.coffee.gate import Released, make_gate
from coffee_vram_plugin.coffee.snapshot import GIB

from fakes import Pool, make_monitor

LOCAL = "http://localhost:11434/v1"


class Opaque:
    """What ``next_call`` becomes inside the plugin host: a non-callable placeholder."""


def call(gate, model="m", base_url=LOCAL, next_call=None, api_request_id="r1"):
    calls = []

    def downstream(payload=None):
        calls.append(model)
        return {"ok": model}

    result = gate(request={"model": model}, next_call=next_call or downstream, model=model, base_url=base_url,
                  session_id="s", api_request_id=api_request_id)
    return result, calls


def test_remote_calls_pass_untouched():
    monitor, client = make_monitor(Pool())
    result, calls = call(make_gate(monitor), base_url="https://openrouter.ai/api/v1")
    assert result == {"ok": "m"} and calls == ["m"] and client.calls == []


def test_cloud_call_records_active_without_gating():
    monitor, client = make_monitor(Pool())
    gate = make_gate(monitor)
    result = gate(request={}, next_call=lambda: {"ok": True}, model="anthropic/claude-sonnet-4",
                  base_url="https://openrouter.ai/api/v1", provider="openrouter",
                  platform="desktop", session_id="s1")
    assert result == {"ok": True} and client.calls == []
    active = monitor.status()["active"]
    assert active["model"] == "anthropic/claude-sonnet-4"
    assert active["provider"] == "openrouter" and active["local"] is False
    assert active["platform"] == "desktop"


def test_local_call_that_fits_runs_once_and_frees_its_slot():
    monitor, _ = make_monitor(Pool())
    result, calls = call(make_gate(monitor))
    assert result == {"ok": "m"} and calls == ["m"]
    assert monitor.scheduler.tickets() == [] and "m" in monitor.scheduler.last_used()


def test_idle_models_are_unloaded_to_make_room():
    pool = Pool()
    pool.load("old", 40)
    monitor, client = make_monitor(pool, {"evict_idle_s": 0}, env={"OLLAMA_MAX_LOADED_MODELS": "3"})
    client.sizes["new"] = 30 * GIB
    _, calls = call(make_gate(monitor), model="new")
    assert ("unload", "old") in client.calls and calls == ["new"]


def test_model_another_ollama_client_just_used_is_not_evicted():
    pool = Pool()
    pool.load("theirs", 40, expires_at=time.time() + 300)
    monitor, client = make_monitor(pool, {"evict_idle_s": 60, "queue_timeout_s": 1},
                                   env={"OLLAMA_MAX_LOADED_MODELS": "3"})
    monitor.sample()
    pool.load("theirs", 40, expires_at=time.time() + 600)
    client.sizes["new"] = 30 * GIB
    _, calls = call(make_gate(monitor), model="new")
    assert ("unload", "theirs") not in client.calls and calls == ["new"]


def test_single_slot_ollama_is_left_to_swap_by_itself():
    pool = Pool()
    pool.load("old", 40)
    monitor, client = make_monitor(pool, env={"OLLAMA_MAX_LOADED_MODELS": "1"})
    client.sizes["new"] = 30 * GIB
    _, calls = call(make_gate(monitor), model="new")
    assert client.calls == [] and calls == ["new"]


def test_held_call_runs_once_memory_frees_up():
    # A busy resident blocks eviction; the new call waits until that model finishes (and room
    # appears), not forever on an empty board.
    pool = Pool()
    pool.load("busy", 40)
    monitor, client = make_monitor(pool, {"evict_idle_s": 60}, env={"OLLAMA_MAX_LOADED_MODELS": "3"})
    monitor.sample()
    monitor.scheduler.touch("busy")  # keep it "in use" for eviction idle checks via last_used
    # Mark busy as in-flight so policy waits instead of evicting.
    busy_ticket = monitor.scheduler.enqueue("busy", "s0", 0)
    monitor.scheduler.force_start(busy_ticket)
    client.sizes["new"] = 30 * GIB
    started = time.monotonic()

    def free_busy():
        monitor.scheduler.release(busy_ticket)
        pool.loaded.pop("busy", None)

    threading.Timer(0.3, free_busy).start()
    _, calls = call(make_gate(monitor), model="new")
    # Held behind the busy concurrency slot until it frees (~0.3s).
    assert calls == ["new"] and time.monotonic() - started >= 0.3


def test_timeout_fails_open_with_an_alert():
    pool = Pool()
    pool.load("busy", 40)
    monitor, client = make_monitor(pool, {"queue_timeout_s": 1, "evict_idle_s": 60},
                                   env={"OLLAMA_MAX_LOADED_MODELS": "3"})
    monitor.sample()
    busy_ticket = monitor.scheduler.enqueue("busy", "s0", 0)
    monitor.scheduler.force_start(busy_ticket)
    client.sizes["new"] = 30 * GIB
    _, calls = call(make_gate(monitor), model="new")
    assert calls == ["new"]
    assert [e for e in monitor.events() if e["kind"] == "timeout"][0]["level"] == "alert"


def test_model_bigger_than_the_gpu_runs_and_is_noted_once():
    pool = Pool()
    monitor, client = make_monitor(pool)
    client.sizes["huge"] = 90 * GIB
    gate = make_gate(monitor)
    _, calls = call(gate, model="huge")
    pool.load("huge", 52)
    call(gate, model="huge")
    assert calls == ["huge"]
    kinds = [e["kind"] for e in monitor.events()]
    assert kinds.count("partial") == 1 and kinds.count("warn") == 1


def test_ollama_outage_does_not_block():
    monitor, client = make_monitor(Pool())
    client.down = True
    _, calls = call(make_gate(monitor))
    assert calls == ["m"]


def test_plugin_host_mode_hands_off_and_keeps_the_slot_until_the_hook():
    monitor, _ = make_monitor(Pool())
    gate = make_gate(monitor)
    with pytest.raises(Released):
        gate(request={}, next_call=Opaque(), model="m", base_url=LOCAL, api_request_id="r9")
    assert monitor.scheduler.in_flight() == {"m"}
    monitor.finish("r9")
    assert monitor.scheduler.in_flight() == set()
    with pytest.raises(Released):
        gate(request={}, next_call=Opaque(), model="m", base_url="https://api.openai.com/v1")


def test_second_call_waits_for_the_first():
    monitor, _ = make_monitor(Pool())
    gate = make_gate(monitor)
    release_first = threading.Event()
    order: list[str] = []

    def slow(payload=None):
        order.append("first-start")
        release_first.wait(5)
        order.append("first-end")

    first = threading.Thread(target=gate, kwargs=dict(request={}, next_call=slow, model="a", base_url=LOCAL))
    first.start()
    time.sleep(0.2)
    second = threading.Thread(target=gate, kwargs=dict(
        request={}, next_call=lambda payload=None: order.append("second"), model="b", base_url=LOCAL))
    second.start()
    time.sleep(0.3)
    release_first.set()
    first.join(5)
    second.join(5)
    assert order == ["first-start", "first-end", "second"]


def test_empty_gpu_near_hold_line_passes_instead_of_queueing():
    pool = Pool(total_gib=64, baseline_gib=1.5)
    monitor, client = make_monitor(pool)
    client.sizes["main"] = 60 * GIB
    # Historical floor below current external (lag): used+capped need misses the hold line.
    monitor._history.append((time.time(), int(0.5 * GIB), pool.total, 0, None))
    _, calls = call(make_gate(monitor), model="main")
    assert calls == ["main"]
    assert any(e["kind"] == "overflow" for e in monitor.events())
    assert "queued" not in [e["kind"] for e in monitor.events()]


def test_second_call_for_same_model_skips_vram_rehold():
    pool = Pool()
    monitor, client = make_monitor(pool, {"queue_timeout_s": 5})
    monitor.sample()
    pool.extra = 50 * GIB
    client.sizes["main"] = 20 * GIB
    gate = make_gate(monitor)
    order: list[str] = []
    release_first = threading.Event()

    def slow(payload=None):
        order.append("first")
        release_first.wait(5)

    first = threading.Thread(target=gate, kwargs=dict(
        request={}, next_call=slow, model="main", base_url=LOCAL, api_request_id="r1"))
    first.start()
    time.sleep(0.25)
    # First call is held on VRAM; a second call for the same model must not re-estimate hold —
    # it only waits for the concurrency slot, then runs once memory frees / first finishes.
    threading.Timer(0.2, lambda: setattr(pool, "extra", 0)).start()
    second_done = threading.Event()

    def second_call():
        gate(request={}, next_call=lambda payload=None: order.append("second"), model="main",
             base_url=LOCAL, api_request_id="r2")
        second_done.set()

    second = threading.Thread(target=second_call)
    second.start()
    time.sleep(0.5)
    release_first.set()
    first.join(5)
    second.join(5)
    assert order == ["first", "second"]
    assert second_done.is_set()
    # No second "Holding main" / queued storm from re-estimating the same model.
    holds = [e for e in monitor.events() if e["kind"] == "queued" and e.get("model") == "main"]
    assert len(holds) <= 1
