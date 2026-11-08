from __future__ import annotations

import json

from fakes import Pool, make_monitor

from coffee_vram_plugin.coffee.commands import VRAM_STATUS_SCHEMA, handle_vram, register_commands


class FakeCtx:
    def __init__(self) -> None:
        self.commands, self.cli, self.tools = {}, {}, {}

    def register_command(self, name, handler, **_):
        self.commands[name] = handler

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, **_):
        self.cli[name] = (setup_fn, handler_fn)

    def register_tool(self, name, toolset, schema, handler, **_):
        self.tools[name] = (toolset, schema, handler)


def test_status_line_shows_gauge_models_and_thresholds():
    pool = Pool(total_gib=62.5, baseline_gib=3)
    pool.load("qwen3.5:9b", 6.1)
    monitor, _ = make_monitor(pool)
    line = handle_vram(monitor, "")
    assert "GPU 9.1/62.5 GB" in line and "qwen3.5:9b 6.1 GB" in line
    assert "queue 0" in line and "warn 85% · hold 95%" in line


def test_queue_lists_waiting_calls_in_order():
    monitor, _ = make_monitor(Pool())
    first = monitor.scheduler.enqueue("a:7b", "s1", 0)
    monitor.scheduler.wait_turn(first, 0)
    monitor.scheduler.enqueue("b:7b", "s2", 0)
    rows = handle_vram(monitor, "queue").splitlines()
    assert rows[0].startswith("1. running a:7b") and rows[1].startswith("2. queued  b:7b")


def test_evict_unloads_idle_model_and_refuses_busy_or_unknown_ones():
    pool = Pool()
    pool.load("idle:7b", 5)
    pool.load("busy:7b", 5)
    monitor, client = make_monitor(pool)
    ticket = monitor.scheduler.enqueue("busy:7b", "s1", 0)
    monitor.scheduler.wait_turn(ticket, 0)

    assert "answering a call" in handle_vram(monitor, "evict busy:7b")
    assert "is not loaded" in handle_vram(monitor, "evict ghost:7b")
    assert handle_vram(monitor, "evict idle:7b") == "Unloaded idle:7b."
    assert client.calls == [("unload", "idle:7b")]


def test_settings_and_unknown_subcommands():
    monitor, _ = make_monitor(Pool(), {"warn_pct": 70})
    assert "warn_pct: 70" in handle_vram(monitor, "settings")
    assert handle_vram(monitor, "bogus").startswith("Usage:")


def test_cli_status_json_carries_status_and_settings(capsys):
    import argparse

    from coffee_vram_plugin.coffee.commands import run_cli, setup_cli

    pool = Pool()
    pool.load("qwen3.5:9b", 6.1)
    monitor, _ = make_monitor(pool)
    parser = argparse.ArgumentParser()
    setup_cli(parser)
    assert run_cli(monitor, parser.parse_args(["status", "--json"])) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"]["loaded"][0]["name"] == "qwen3.5:9b" and out["settings"]["hold_pct"] == 95


def test_vram_status_tool_reports_room_before_hold():
    pool = Pool(total_gib=100, baseline_gib=10)
    pool.load("qwen3.5:9b", 6)
    monitor, _ = make_monitor(pool)
    ctx = FakeCtx()
    register_commands(ctx, monitor)

    toolset, schema, handler = ctx.tools["vram_status"]
    payload = json.loads(handler({}))
    assert (toolset, schema) == ("coffee-vram", VRAM_STATUS_SCHEMA)
    assert payload["gpu_used_gb"] == 16.0 and payload["gpu_free_before_hold_gb"] == 79.0
    assert payload["loaded_models"][0]["name"] == "qwen3.5:9b"
    assert payload["loaded_models"][0]["gpu_gb"] == 6.0
    assert "system_ram" in payload["note"]
    assert "vram" in ctx.commands and "coffee-vram" in ctx.cli
