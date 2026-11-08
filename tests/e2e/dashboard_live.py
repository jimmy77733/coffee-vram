"""Serve dashboard/plugin_api.py against the live GPU and Ollama, read-only (no /evict).

No plugin is registered in this process, so this exercises the dashboard's own Monitor. Run it
with Hermes' own Python and a throwaway HERMES_HOME:

    $env:HERMES_HOME = (New-Item -ItemType Directory "$env:TEMP\\coffee-e2e" -Force).FullName
    & <hermes python> tests/e2e/dashboard_live.py <path to hermes-agent> [model]
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[2] / "dashboard" / "plugin_api.py"


def main() -> int:
    sys.path.insert(0, sys.argv[1])
    assert "coffee-e2e" in os.environ.get("HERMES_HOME", ""), "use a throwaway HERMES_HOME"
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    spec = importlib.util.spec_from_file_location("hermes_dashboard_plugin_coffee_vram", API)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/coffee-vram")
    http = TestClient(app)

    snap = http.get("/api/plugins/coffee-vram/snapshot").json()
    print(f"snapshot: {snap['used'] / 2**30:.2f}/{snap['total'] / 2**30:.2f} GiB via {snap['source']}, "
          f"level {snap['level']}, ollama_up {snap['ollama_up']}, shared_queue {snap['shared_queue']}, "
          f"loaded {[m['name'] for m in snap['loaded']]}")
    print("history points:", len(http.get("/api/plugins/coffee-vram/history").json()["points"]))
    print("events:", http.get("/api/plugins/coffee-vram/events?since=0").json())
    if len(sys.argv) > 2:
        found = http.get("/api/plugins/coffee-vram/estimate", params={"model": sys.argv[2]}).json()
        print(f"estimate {sys.argv[2]}: {found['total'] / 2**30:.2f} GiB ({found['source']}, ctx {found['context']})")
    module.monitor().stop()
    return 0 if snap["total"] and snap["shared_queue"] is False else 1


if __name__ == "__main__":
    sys.exit(main())
