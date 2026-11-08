from __future__ import annotations

import time

from coffee_vram_plugin.coffee.gpu import parse_smi
from coffee_vram_plugin.coffee.ollama import parse_ps, same_endpoint
from coffee_vram_plugin.coffee.snapshot import GIB

from fakes import Pool, make_monitor

SPARK_SMI = ("0, NVIDIA RTX Spark N1X (6144-core Blackwell RTX GPU), 64000, 14395\n"
             "1, NVIDIA NPU, [N/A], [N/A]")


def test_smi_skips_the_npu_row():
    reading = parse_smi(SPARK_SMI)
    assert (reading.total, reading.used) == (64000 << 20, 14395 << 20)
    assert parse_smi("1, NVIDIA NPU, [N/A], [N/A]") is None


def test_ps_payload_from_ollama_0_40():
    loaded = parse_ps({"models": [{
        "name": "qwen3.5:9b", "model": "qwen3.5:9b", "size": 10626982215, "size_vram": 10626982215,
        "expires_at": "2026-11-05T19:10:47.5531681+08:00", "context_length": 131072}]})
    model = loaded["qwen3.5:9b"]
    assert (model.size_vram, model.context_length, model.spill) == (10626982215, 131072, 0)
    assert model.expires_at is not None


def test_endpoint_match_treats_loopback_names_alike():
    assert same_endpoint("http://localhost:11434/v1", "http://127.0.0.1:11434")
    assert not same_endpoint("http://localhost:8080/v1", "http://127.0.0.1:11434")
    assert not same_endpoint("https://openrouter.ai/api/v1", "http://127.0.0.1:11434")
    assert not same_endpoint(None, "http://127.0.0.1:11434")


def test_sample_counts_external_memory_unless_told_not_to():
    pool = Pool()
    pool.load("a", 10)
    pool.extra = 20 * GIB
    monitor, _ = make_monitor(pool)
    assert monitor.sample().used == pool.used
    blind, _ = make_monitor(pool, {"watch_external": False})
    assert blind.sample().used == 10 * GIB


def test_total_override_and_ollama_outage_are_reported():
    pool = Pool()
    monitor, client = make_monitor(pool, {"gpu_total_override_gb": 100.0})
    assert monitor.sample().total == 100 * GIB
    client.down = True
    monitor.sample()
    assert monitor.events()[-1]["kind"] == "ollama"


def test_crossing_the_warn_line_emits_one_event():
    pool = Pool()
    monitor, _ = make_monitor(pool)
    monitor.sample()
    pool.load("big", 55)
    monitor.sample()
    monitor.sample()
    assert [e["kind"] for e in monitor.events()] == ["level"]


def test_level_alerts_do_not_repeat_inside_the_cooldown(monkeypatch):
    pool = Pool()
    monitor, _ = make_monitor(pool)
    monitor.sample()
    pool.load("big", 55)
    monitor.sample()
    assert len(monitor.events()) == 1

    pool.loaded.clear()
    monitor.sample()  # back to ok — chip updates, but no new alert yet
    pool.load("big", 60)
    monitor.sample()
    assert [e["kind"] for e in monitor.events()] == ["level"]

    monkeypatch.setattr(monitor, "_level_alert_at", monitor._level_alert_at - 6 * 60)
    pool.loaded.clear()
    monitor.sample()
    pool.load("big", 60)
    monitor.sample()
    assert [e["kind"] for e in monitor.events()] == ["level", "level"]


def test_warn_hold_flicker_does_not_emit_again():
    pool = Pool(total_gib=100, baseline_gib=0)
    monitor, _ = make_monitor(pool)
    monitor.sample()
    pool.load("warn", 86)   # ok → warn: one event
    monitor.sample()
    pool.load("warn", 96)   # warn → hold: chip only
    monitor.sample()
    pool.load("warn", 86)   # hold → warn: chip only
    monitor.sample()
    assert [e["kind"] for e in monitor.events()] == ["level"]
    assert monitor.status()["level"] == "warn"


def test_keep_warm_refreshes_models_about_to_expire():
    pool = Pool()
    pool.load("soon", 10, expires_at=time.time() + 30)
    pool.load("later", 10, expires_at=time.time() + 1800)
    monitor, client = make_monitor(pool)
    monitor.tick()
    assert client.calls == [("keep_alive", "soon", "10m")]


def test_keep_warm_off_unloads_idle_models_but_not_pinned_ones():
    pool = Pool()
    pool.load("idle", 10, expires_at=time.time() + 1800)
    pool.load("main", 10, expires_at=time.time() + 1800)
    monitor, client = make_monitor(pool, {"keep_warm": False, "evict_idle_s": 0, "pinned_models": ["main"]})
    monitor.tick()
    assert client.calls == [("unload", "idle")]
    assert monitor.events()[-1]["kind"] == "evicted"
