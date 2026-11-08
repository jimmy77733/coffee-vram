"""One-off hardware and Ollama measurements behind RESEARCH.md (stdlib only; read-only except
for loading and then unloading the test model through Ollama's own API).

    python scripts/measure_spark.py --model qwen3.5:9b --out measurements/raw/run.json
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GIB = 1 << 30


# ── OS memory ────────────────────────────────────────────────────────────────

class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def os_memory() -> dict:
    if os.name != "nt":
        return {"error": "windows only"}
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"error": "GlobalMemoryStatusEx failed"}
    return {"ram_total": status.ullTotalPhys, "ram_available": status.ullAvailPhys,
            "commit_total": status.ullTotalPageFile, "commit_available": status.ullAvailPageFile}


# ── NVML (ctypes) ────────────────────────────────────────────────────────────

class _NvmlMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _NvmlMemoryV2(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("total", ctypes.c_ulonglong), ("reserved", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _NvmlProcess(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_uint), ("usedGpuMemory", ctypes.c_ulonglong),
                ("gpuInstanceId", ctypes.c_uint), ("computeInstanceId", ctypes.c_uint)]


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


def _load_nvml():
    for name in ("nvml.dll", "libnvidia-ml.so.1"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


def nvml_snapshot() -> dict:
    lib = _load_nvml()
    if lib is None:
        return {"error": "nvml library not found"}
    out: dict = {}
    rc = lib.nvmlInit_v2()
    if rc != 0:
        return {"error": f"nvmlInit_v2 rc={rc}"}
    try:
        buf = ctypes.create_string_buffer(96)
        if lib.nvmlSystemGetDriverVersion(buf, 96) == 0:
            out["driver"] = buf.value.decode()
        count = ctypes.c_uint()
        out["count_rc"] = lib.nvmlDeviceGetCount_v2(ctypes.byref(count))
        out["devices"] = []
        for index in range(count.value):
            dev: dict = {"index": index}
            handle = ctypes.c_void_p()
            if lib.nvmlDeviceGetHandleByIndex_v2(index, ctypes.byref(handle)) != 0:
                dev["error"] = "handle"
                out["devices"].append(dev)
                continue
            if lib.nvmlDeviceGetName(handle, buf, 96) == 0:
                dev["name"] = buf.value.decode()
            mem = _NvmlMemory()
            dev["memory_v1_rc"] = lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem))
            if dev["memory_v1_rc"] == 0:
                dev["memory_v1"] = {"total": mem.total, "free": mem.free, "used": mem.used}
            mem2 = _NvmlMemoryV2()
            mem2.version = ctypes.sizeof(_NvmlMemoryV2) | (2 << 24)
            getter = getattr(lib, "nvmlDeviceGetMemoryInfo_v2", None)
            if getter is not None:
                dev["memory_v2_rc"] = getter(handle, ctypes.byref(mem2))
                if dev["memory_v2_rc"] == 0:
                    dev["memory_v2"] = {"total": mem2.total, "reserved": mem2.reserved,
                                        "free": mem2.free, "used": mem2.used}
            util = _NvmlUtilization()
            dev["utilization_rc"] = lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util))
            if dev["utilization_rc"] == 0:
                dev["utilization"] = {"gpu": util.gpu, "memory": util.memory}
            procs = (_NvmlProcess * 64)()
            n = ctypes.c_uint(64)
            getter = getattr(lib, "nvmlDeviceGetComputeRunningProcesses_v3", None)
            if getter is not None:
                dev["processes_rc"] = getter(handle, ctypes.byref(n), procs)
                if dev["processes_rc"] == 0:
                    dev["processes"] = [{"pid": procs[i].pid, "used": procs[i].usedGpuMemory}
                                        for i in range(n.value)]
            out["devices"].append(dev)
    finally:
        lib.nvmlShutdown()
    return out


# ── CUDA driver (ctypes) ─────────────────────────────────────────────────────

_CU_DEVICE_ATTRIBUTE_INTEGRATED = 18


def cuda_snapshot() -> dict:
    for name in ("nvcuda.dll", "libcuda.so.1"):
        try:
            cuda = ctypes.CDLL(name)
            break
        except OSError:
            continue
    else:
        return {"error": "cuda driver not found"}
    out: dict = {"init_rc": cuda.cuInit(0)}
    if out["init_rc"] != 0:
        return out
    dev = ctypes.c_int()
    if cuda.cuDeviceGet(ctypes.byref(dev), 0) != 0:
        return {**out, "error": "cuDeviceGet"}
    buf = ctypes.create_string_buffer(256)
    if cuda.cuDeviceGetName(buf, 256, dev) == 0:
        out["name"] = buf.value.decode()
    total = ctypes.c_size_t()
    out["total_mem_rc"] = cuda.cuDeviceTotalMem_v2(ctypes.byref(total), dev)
    out["total_mem"] = total.value
    attr = ctypes.c_int()
    out["integrated_rc"] = cuda.cuDeviceGetAttribute(ctypes.byref(attr), _CU_DEVICE_ATTRIBUTE_INTEGRATED, dev)
    out["integrated"] = bool(attr.value)
    ctx = ctypes.c_void_p()
    if cuda.cuDevicePrimaryCtxRetain(ctypes.byref(ctx), dev) == 0:
        try:
            if cuda.cuCtxSetCurrent(ctx) == 0:
                free, tot = ctypes.c_size_t(), ctypes.c_size_t()
                out["mem_get_info_rc"] = cuda.cuMemGetInfo_v2(ctypes.byref(free), ctypes.byref(tot))
                out["mem_get_info"] = {"free": free.value, "total": tot.value}
        finally:
            cuda.cuDevicePrimaryCtxRelease(dev)
    return out


# ── nvidia-smi ───────────────────────────────────────────────────────────────

def _smi(*args: str) -> dict:
    try:
        done = subprocess.run(["nvidia-smi", *args], capture_output=True, text=True, timeout=15,
                              creationflags=NO_WINDOW, shell=False, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}
    return {"rc": done.returncode, "stdout": done.stdout.strip(), "stderr": done.stderr.strip()}


def smi_snapshot() -> dict:
    return {
        "gpu": _smi("--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,driver_version",
                    "--format=csv,noheader,nounits"),
        "apps": _smi("--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"),
    }


# ── Ollama ───────────────────────────────────────────────────────────────────

def _http(url: str, body: dict | None = None, timeout: float = 600) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                     method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, str(exc)


def ollama_ps(base: str) -> object:
    return _http(f"{base}/api/ps", timeout=5)[1]


def expires_in_s(ps: object, model: str) -> float | None:
    if not isinstance(ps, dict):
        return None
    for entry in ps.get("models") or []:
        if entry.get("name") == model or entry.get("model") == model:
            stamp = entry.get("expires_at", "")
            try:
                when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                return None
            return round((when - dt.datetime.now(dt.timezone.utc)).total_seconds(), 1)
    return None


def snapshot(label: str, base: str) -> dict:
    return {"label": label, "at": time.time(), "os": os_memory(), "nvml": nvml_snapshot(),
            "cuda": cuda_snapshot(), "smi": smi_snapshot(), "ollama_ps": ollama_ps(base)}


def keep_alive_trials(base: str, model: str) -> list[dict]:
    """Each trial sets a distinctive keep_alive and reads back /api/ps expires_at (default is 5m)."""
    chat = {"model": model, "messages": [{"role": "user", "content": "Reply with: ok"}], "max_tokens": 1,
            "stream": False}
    trials = [
        ("v1_top_level_string", f"{base}/v1/chat/completions", {**chat, "keep_alive": "2m"}, 120),
        ("v1_top_level_seconds", f"{base}/v1/chat/completions", {**chat, "keep_alive": 420}, 420),
        ("v1_no_keep_alive", f"{base}/v1/chat/completions", chat, None),
        ("v1_options_num_ctx", f"{base}/v1/chat/completions", {**chat, "options": {"num_ctx": 8192}}, None),
        ("native_generate", f"{base}/api/generate", {"model": model, "keep_alive": "3m"}, 180),
    ]
    results = []
    for name, url, body, expected in trials:
        started = time.time()
        status, payload = _http(url, body)
        ps = ollama_ps(base)
        results.append({
            "trial": name, "url": url.removeprefix(base), "keep_alive_sent": body.get("keep_alive"),
            "expected_s": expected, "status": status, "elapsed_s": round(time.time() - started, 2),
            "expires_in_s": expires_in_s(ps, model), "ollama_ps": ps,
            "error": payload if status != 200 else None,
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    base = args.ollama.rstrip("/")

    tags = _http(f"{base}/api/tags", timeout=10)[1]
    names = {m.get("name") for m in (tags.get("models") or [])} if isinstance(tags, dict) else set()
    if args.model not in names:
        print(f"model {args.model!r} is not pulled; available: {sorted(names)}", file=sys.stderr)
        return 2
    show = _http(f"{base}/api/show", {"model": args.model}, timeout=30)[1]
    model_info = show.get("model_info", {}) if isinstance(show, dict) else {}
    tag_size = next((m.get("size") for m in tags["models"] if m.get("name") == args.model), None)

    report = {
        "host": {"platform": sys.platform, "machine": os.environ.get("PROCESSOR_ARCHITECTURE")},
        "ollama_version": _http(f"{base}/api/version", timeout=5)[1],
        "ollama_env": {k: os.environ.get(k) for k in ("OLLAMA_KV_CACHE_TYPE", "OLLAMA_NUM_PARALLEL",
                                                       "OLLAMA_CONTEXT_LENGTH", "OLLAMA_FLASH_ATTENTION")},
        "model": args.model, "model_file_size": tag_size,
        "model_info": {k: v for k, v in model_info.items() if not isinstance(v, list)},
        "snapshots": [snapshot("baseline", base)],
    }
    report["keep_alive"] = keep_alive_trials(base, args.model)
    report["snapshots"].append(snapshot("model_loaded", base))
    report["unload"] = _http(f"{base}/api/generate", {"model": args.model, "keep_alive": 0}, timeout=60)[0]
    time.sleep(3)
    report["snapshots"].append(snapshot("after_unload", base))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
