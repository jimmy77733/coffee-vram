"""Run Coffee's gate through Hermes' real llm_execution chain against the live GPU and Ollama.

The provider at the end of the chain is a stub, so no model is loaded. Run it with Hermes' own
Python and a throwaway HERMES_HOME:

    $env:HERMES_HOME = (New-Item -ItemType Directory "$env:TEMP\\coffee-e2e" -Force).FullName
    & <hermes python> tests/e2e/middleware_chain.py <path to hermes-agent>
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    sys.path.insert(0, sys.argv[1])
    assert "coffee-e2e" in os.environ.get("HERMES_HOME", ""), "use a throwaway HERMES_HOME"
    spec = importlib.util.spec_from_file_location("coffee_vram_plugin", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    plugin = importlib.util.module_from_spec(spec)
    sys.modules["coffee_vram_plugin"] = plugin
    spec.loader.exec_module(plugin)

    from hermes_cli.middleware import run_llm_execution_middleware
    from hermes_cli.plugins import _delivery_manager
    from coffee_vram_plugin.coffee.gate import make_gate
    from coffee_vram_plugin.coffee.monitor import Monitor

    monitor = Monitor(lambda key, default=None: default)
    _delivery_manager()._middleware["llm_execution"] = [make_gate(monitor)]
    seen: list[dict] = []

    def provider(request):
        seen.append(request)
        return {"choices": [{"message": {"content": "stub"}}]}

    for label, base_url, model in (("local", "http://localhost:11434/v1", "qwen3.5:9b"),
                                   ("remote", "https://openrouter.ai/api/v1", "some/cloud-model")):
        started = time.perf_counter()
        response = run_llm_execution_middleware(
            {"model": model, "messages": []}, provider, original_request={"model": model},
            task_id="t", turn_id="u", api_request_id=f"req-{label}", session_id="e2e", platform="cli",
            model=model, provider="custom", base_url=base_url, api_mode="chat_completions",
            api_call_count=1, middleware_trace=[])
        print(f"{label}: {response['choices'][0]['message']['content']} in "
              f"{(time.perf_counter() - started) * 1000:.0f} ms")
    print("provider calls:", len(seen), "| queue after:", monitor.scheduler.tickets())
    print("events:", [(e["kind"], e["message"]) for e in monitor.events()])
    status = monitor.status()
    print(f"status: {status['used'] / 2**30:.2f}/{status['total'] / 2**30:.2f} GiB via {status['source']}, "
          f"level {status['level']}, loaded {[m['name'] for m in status['loaded']]}")
    monitor.stop()
    return 0 if len(seen) == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
