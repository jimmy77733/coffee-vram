"""Plain-text views of ``Monitor.status()`` for /vram, ``hermes coffee-vram`` and the vram_status tool."""

from __future__ import annotations

import time
from typing import Any

from .snapshot import GIB


def gb(value: int | float) -> str:
    return f"{value / GIB:.1f}"


def status_line(status: dict[str, Any]) -> str:
    total = status["total"]
    gauge = (f"GPU {gb(status['used'])}/{gb(total)} GB ({status['used_pct']:.0f}%)" if total
             else "GPU memory unknown")
    models = ", ".join(f"{m['name']} {gb(m['size_vram'])} GB" for m in status["loaded"]) or "no models loaded"
    queued = sum(1 for t in status["queue"] if t["state"] == "queued")
    parts = [gauge, models, f"queue {queued}", f"warn {status['warn_pct']}% · hold {status['hold_pct']}%"]
    if not status["ollama_up"]:
        parts.insert(1, "Ollama not answering")
    return " · ".join(parts)


def queue_table(status: dict[str, Any]) -> str:
    if not status["queue"]:
        return "Queue is empty."
    now = time.time()
    rows = []
    for index, ticket in enumerate(status["queue"], 1):
        since = ticket["started_at"] or ticket["enqueued_at"]
        rows.append(f"{index}. {ticket['state']:<7} {ticket['model']}  ~{gb(ticket['est_bytes'])} GB  "
                    f"{now - since:.0f}s  session {ticket['session_id'] or '-'}")
    return "\n".join(rows)


def models_table(status: dict[str, Any]) -> str:
    if not status["loaded"]:
        return "No models loaded."
    now = time.time()
    rows = []
    for m in status["loaded"]:
        expires = m.get("expires_at")
        left = ("pinned" if m["pinned"] else "until unloaded" if not expires
                else "stays loaded" if expires - now > 365 * 86400 else f"{max(0, expires - now) / 60:.0f} min left")
        spill = f", {gb(m['spill'])} GB in system RAM" if m["spill"] else ""
        if m.get("prompt_tokens") and m.get("context_length"):
            ctx = f", ctx {m['prompt_tokens']}/{m['context_length']}"
        elif m.get("context_length"):
            ctx = f", ctx {m['context_length']}"
        else:
            ctx = ""
        tps = f", {m['tps']} tok/s" if m.get("tps") else ""
        rows.append(f"{m['name']}  {gb(m['size_vram'])} GB{spill}{ctx}{tps}  ({left})")
    return "\n".join(rows)


def tool_payload(status: dict[str, Any]) -> dict[str, Any]:
    """What the model sees: free GPU room and what is resident, nothing per-session."""
    total = status["total"]
    free = max(0, total * status["hold_pct"] / 100 - status["used"]) if total else None
    host = status.get("host") or {}
    return {
        "gpu_total_gb": round(total / GIB, 1) if total else None,
        "gpu_used_gb": round(status["used"] / GIB, 1),
        "gpu_free_before_hold_gb": round(free / GIB, 1) if free is not None else None,
        "note": ("gpu_* is the NVIDIA carve-out Coffee budgets against; system_ram_* is "
                 "Windows-visible RAM (Task Manager → Memory). They are different pools."),
        "system_ram_total_gb": round(host["ram_total"] / GIB, 1) if host.get("ram_total") else None,
        "system_ram_used_gb": round(host["ram_used"] / GIB, 1) if host.get("ram_total") else None,
        "cpu_pct": host.get("cpu_pct"),
        "gpu_temp_c": host.get("gpu_temp_c"),
        "level": status["level"],
        "ollama_reachable": status["ollama_up"],
        "loaded_models": [{
            "name": m["name"], "gpu_gb": round(m["size_vram"] / GIB, 1),
            "context_length": m.get("context_length"), "prompt_tokens": m.get("prompt_tokens"),
            "tokens_per_sec": m.get("tps"),
        } for m in status["loaded"]],
        "queued_calls": sum(1 for t in status["queue"] if t["state"] == "queued"),
        "one_model_at_a_time": status["max_loaded"] == 1,
    }
