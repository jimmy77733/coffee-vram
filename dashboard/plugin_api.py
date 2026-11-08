"""Coffee's backend for the Desktop pane: ``/api/plugins/coffee-vram/*``.

Hermes imports this file as its own top-level module, so it finds the plugin's live Monitor (and
with it the real queue) through ``sys.modules``. A process that serves the dashboard without
having registered the plugin gets a Monitor of its own: gauge and models, no queue.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()

_PACKAGE = "hermes_plugins.coffee_vram"
_FALLBACK = "coffee_vram_dashboard_runtime"
# Keep every 1 s sample for the sparkline (monitor deque is already capped at 15 min).
_HISTORY_POINTS = 900
_lock = threading.Lock()


def _registered_monitor():
    bare = sys.modules.get(_PACKAGE)
    if getattr(bare, "MONITOR", None) is not None:
        return bare.MONITOR
    for name, module in list(sys.modules.items()):
        if name.startswith(_PACKAGE + "__home_") and getattr(module, "MONITOR", None) is not None:
            return module.MONITOR
    return None


def _settings_getter():
    from hermes_cli.config import load_config_readonly

    def get(key: str, default: Any = None) -> Any:
        entry = ((load_config_readonly() or {}).get("plugins") or {}).get("entries") or {}
        settings = (entry.get("coffee-vram") or {}).get("settings") or {}
        return settings.get(key, default) if isinstance(settings, dict) else default
    return get


def _fallback_monitor():
    with _lock:
        package = sys.modules.get(_FALLBACK)
        if package is None:
            root = Path(__file__).resolve().parents[1]
            spec = importlib.util.spec_from_file_location(_FALLBACK, root / "__init__.py",
                                                          submodule_search_locations=[str(root)])
            package = importlib.util.module_from_spec(spec)
            sys.modules[_FALLBACK] = package
            spec.loader.exec_module(package)
        if package.MONITOR is None:
            from importlib import import_module
            package.MONITOR = import_module(f"{_FALLBACK}.coffee.monitor").Monitor(_settings_getter())
        return package.MONITOR


def monitor():
    found = _registered_monitor() or _fallback_monitor()
    found.ensure_started()
    return found


@router.get("/snapshot")
def snapshot() -> dict:
    m = monitor()
    return {**m.status(), "shared_queue": m is _registered_monitor()}


@router.get("/events")
def events(since: int = 0) -> dict:
    m = monitor()
    items = m.events(since)
    return {"events": items, "last": items[-1]["seq"] if items else since}


@router.get("/history")
def history() -> dict:
    # No averaging / rounding: pass raw NVML byte samples so flat stretches still show jitter.
    points = monitor().history()[-_HISTORY_POINTS:]
    return {"points": [{"t": t, "used": used, "total": total, "ollama": ollama, "util": util}
                       for t, used, total, ollama, util in points]}


@router.get("/estimate")
def estimate(model: str) -> dict:
    found = monitor().estimate(model)
    return {**asdict(found), "total": found.total}


@router.post("/evict")
def evict(payload: dict) -> dict:
    from importlib import import_module

    m = monitor()
    model = str(payload.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "model is required")
    commands = import_module(type(m).__module__.rsplit(".", 1)[0] + ".commands")
    message = commands.evict(m, model)
    return {"ok": message.startswith("Unloaded"), "message": message}
