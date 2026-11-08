from __future__ import annotations

from coffee_vram_plugin.coffee.hoststats import read_ram
from coffee_vram_plugin.coffee.snapshot import GIB

from fakes import Pool, make_monitor


def test_system_ram_is_the_windows_visible_pool_not_the_gpu_carve_out():
    total, used = read_ram()
    assert total > 0 and 0 <= used <= total
    # On the Spark, Windows-visible RAM is ~64 GiB; the GPU carve-out is a separate 62.5 GiB.
    assert 8 * GIB < total < 200 * GIB


def test_status_includes_host_stats_and_respects_toggles():
    monitor, _ = make_monitor(Pool())
    monitor._host.read(monitor.gpu())  # prime CPU meter
    body = monitor.status()
    assert body["show_system_ram"] is True and "host" in body
    assert body["alert_source"] == "gpu"
    assert body["host"]["ram_total"] > 0
    assert set(body["host"]) >= {
        "ram_total", "ram_used", "ram_pct", "cpu_pct", "net_bps", "gpu_temp_c", "gpu_util_pct",
        "gpu_name", "cpu_name", "ram_name"}
    assert body["host"]["gpu_name"] == "Fake GPU"

    monitor, _ = make_monitor(Pool(), {"show_system_ram": False, "show_cpu": False,
                                       "show_network": False, "show_gpu_temp": False})
    flags = monitor.status()
    assert flags["show_system_ram"] is False and flags["show_cpu"] is False


def test_alert_source_system_ram_drives_level_without_changing_gpu_budget(monkeypatch):
    class FakeHost:
        ram_total = 64 * GIB
        ram_used = 60 * GIB
        ram_pct = 93.75
        cpu_pct = 10.0
        net_bps = 0.0
        gpu_temp_c = 40
        gpu_util_pct = 12.0
        gpu_name = "Fake GPU"
        cpu_name = "Fake CPU"
        ram_name = "64 GB"

    pool = Pool()
    monitor, _ = make_monitor(pool, {"alert_source": "system_ram", "warn_pct": 85, "hold_pct": 95})
    monkeypatch.setattr(monitor._host, "read", lambda _gpu, fresh=False: FakeHost())
    monitor.sample()
    assert monitor.status()["level"] == "warn"
    assert monitor.status()["alert_pct"] == 93.8 or monitor.status()["alert_pct"] == 93.75
    events = [e for e in monitor.events() if e["kind"] == "level"]
    assert events and events[0]["message"].startswith("System memory")
    # GPU carve-out is still the fit budget even when toasts track system RAM.
    assert monitor.status()["total"] == pool.total


def test_history_records_gpu_util_alongside_memory():
    monitor, _ = make_monitor(Pool())
    monitor._host.read(monitor.gpu(), fresh=True)
    monitor.sample()
    point = monitor.history()[-1]
    assert len(point) == 5
    _t, used, total, _ollama, util = point
    assert total > 0 and 0 <= used <= total
    assert util is None or 0 <= util <= 100


def test_note_call_records_context_and_tokens_per_second():
    pool = Pool()
    pool.load("qwen3.5:9b", 6)
    monitor, _ = make_monitor(pool)
    monitor.note_call("qwen3.5:9b", usage={"prompt_tokens": 1200, "output_tokens": 80},
                      api_duration=2.0, base_url="http://127.0.0.1:11434/v1")
    monitor.note_call("cloud", usage={"prompt_tokens": 99, "output_tokens": 9},
                      api_duration=1.0, base_url="https://openrouter.ai/api/v1")
    row = monitor.status()["loaded"][0]
    assert row["prompt_tokens"] == 1200 and row["tps"] == 40.0
    assert "cloud" not in monitor._call_stats
