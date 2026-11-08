"""The Desktop backend must serve the plugin's own Monitor, so the pane shows the real queue."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from fakes import Pool, make_monitor

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

API = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"


@pytest.fixture
def served(monkeypatch):
    pool = Pool(total_gib=62.5, baseline_gib=3)
    pool.load("qwen3.5:9b", 6.1)
    monitor, client = make_monitor(pool)
    monkeypatch.setitem(sys.modules, "hermes_plugins.coffee_vram", types.SimpleNamespace(MONITOR=monitor))
    spec = importlib.util.spec_from_file_location("hermes_dashboard_plugin_coffee_vram", API)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/coffee-vram")
    yield TestClient(app), monitor, client
    monitor.stop()


def test_snapshot_comes_from_the_registered_monitor(served):
    http, monitor, _ = served
    ticket = monitor.scheduler.enqueue("qwen3.5:9b", "s1", 0)
    body = http.get("/api/plugins/coffee-vram/snapshot").json()
    assert body["shared_queue"] is True
    assert [t["id"] for t in body["queue"]] == [ticket.id]
    assert [m["name"] for m in body["loaded"]] == ["qwen3.5:9b"]


def test_events_are_returned_after_a_sequence_number(served):
    http, monitor, _ = served
    first = monitor.emit("test", "one")
    monitor.emit("test", "two", level="warn")
    body = http.get(f"/api/plugins/coffee-vram/events?since={first['seq']}").json()
    assert [e["message"] for e in body["events"]] == ["two"] and body["last"] == first["seq"] + 1


def test_evict_unloads_through_the_same_rules_as_the_command(served):
    http, _, client = served
    assert http.post("/api/plugins/coffee-vram/evict", json={"model": ""}).status_code == 400
    body = http.post("/api/plugins/coffee-vram/evict", json={"model": "qwen3.5:9b"}).json()
    assert body == {"ok": True, "message": "Unloaded qwen3.5:9b."}
    assert client.calls == [("unload", "qwen3.5:9b")]
