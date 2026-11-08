"""Load one model through /v1, sampling NVML, PDH and OS memory every second, then unload it.

    python scripts/measure_load.py --model qwen3.8-flash-next-huihui:iq3s --out measurements/raw/main.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from measure_spark import _http, cuda_snapshot, os_memory  # noqa: E402


def _plugin():
    spec = importlib.util.spec_from_file_location("coffee_vram_plugin", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["coffee_vram_plugin"] = module
    spec.loader.exec_module(module)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--keep", action="store_true", help="leave the model loaded")
    args = parser.parse_args()
    base = args.ollama.rstrip("/")

    _plugin()
    from coffee_vram_plugin.coffee.gpu import NvmlBackend
    from winpdh import AdapterMemory

    nvml, pdh = NvmlBackend.open(), AdapterMemory()

    def point(label: str) -> dict:
        reading = nvml.read() if nvml else None
        dedicated, shared = pdh.busiest() or (0, 0)
        mem = os_memory()
        return {"label": label, "t": time.time(), "nvml_used": reading.used if reading else None,
                "nvml_total": reading.total if reading else None, "pdh_dedicated": dedicated,
                "pdh_shared": shared, "ram_available": mem.get("ram_available"),
                "commit_available": mem.get("commit_available")}

    report = {"model": args.model, "cuda": cuda_snapshot(), "points": [point("baseline")],
              "ps_baseline": _http(f"{base}/api/ps", timeout=5)[1]}
    done = threading.Event()

    def sampler() -> None:
        while not done.wait(1.0):
            report["points"].append(point("loading"))

    thread = threading.Thread(target=sampler, daemon=True)
    started = time.time()
    thread.start()
    status, payload = _http(f"{base}/v1/chat/completions", {
        "model": args.model, "messages": [{"role": "user", "content": "Reply with: ok"}],
        "max_tokens": 1, "stream": False}, timeout=900)
    done.set()
    thread.join()
    report["load"] = {"status": status, "seconds": round(time.time() - started, 1),
                      "error": payload if status != 200 else None}
    time.sleep(2)
    report["points"].append(point("loaded"))
    report["ps_loaded"] = _http(f"{base}/api/ps", timeout=5)[1]
    if not args.keep:
        report["unload_status"] = _http(f"{base}/api/generate", {"model": args.model, "keep_alive": 0},
                                        timeout=120)[0]
        time.sleep(5)
        report["points"].append(point("after_unload"))
        report["ps_after"] = _http(f"{base}/api/ps", timeout=5)[1]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
