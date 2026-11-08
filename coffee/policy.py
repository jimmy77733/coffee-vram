"""Whether a local call may load now, after evicting idle models, or has to wait.

Pure: the gate feeds it a snapshot, an estimate and the scheduler's LRU/in-flight view.

A model larger than the GPU room is not refused: Ollama fills the room and runs the rest on the
CPU from system RAM (RESEARCH.md §6). ``budget_pool`` picks which capacity the fit check uses:

- ``gpu`` — NVML carve-out (default).
- ``system_ram`` — Windows-visible RAM.
- ``combined`` — GPU free + system RAM free (remaining capacity). Never sum the two totals —
  on unified-memory machines (RTX Spark) that double-counts the same physical DRAM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Mapping

from .estimator import Estimate
from .settings import Settings
from .snapshot import GIB, Snapshot

GO = "go"        # fits now
EVICT = "evict"  # fits once ``evict`` is unloaded
WAIT = "wait"    # may fit later (busy or not-yet-idle models, transient external usage)
PASS = "pass"    # warn_only / empty-board spill: let it through and notify


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    need: int
    free: int
    after_pct: float
    warn: bool = False
    partial: bool = False  # Ollama will run part of the model on the CPU / system RAM
    evict: tuple[str, ...] = ()
    pool: str = "gpu"


def _cap(total: int, s: Settings) -> int:
    """Highest ``used`` allowed on one pool: reserve and hold line, whichever is lower."""
    if total <= 0:
        return 0
    return min(total - int(s.reserve_gb * GIB), int(total * s.hold_pct / 100))


def _gpu_free(snap: Snapshot, s: Settings) -> int:
    return max(0, _cap(snap.total, s) - snap.used) if snap.total else 0


def _ram_free(ram_total: int, ram_used: int, s: Settings) -> int:
    return max(0, _cap(ram_total, s) - ram_used) if ram_total else 0


def plan_eviction(snap: Snapshot, target: str, need: int, s: Settings, *,
                  last_used: Mapping[str, float], in_flight: Collection[str], now: float,
                  ram_total: int = 0, ram_used: int = 0) -> tuple[str, ...]:
    """Least-recently-used idle models whose unloading makes room for ``need``, or ()."""
    candidates = sorted(
        (m for m in snap.loaded.values()
         if m.name != target and m.name not in s.pinned_models and m.name not in in_flight
         and now - last_used.get(m.name, 0.0) >= s.evict_idle_s),
        key=lambda m: last_used.get(m.name, 0.0),
    )
    if s.budget_pool == "combined":
        room = _gpu_free(snap, s) + _ram_free(ram_total, ram_used, s)
        plan = []
        for model in candidates:
            if need <= room:
                break
            plan.append(model.name)
            room += model.size_vram
        return tuple(plan) if need <= room else ()

    if s.budget_pool == "system_ram" and ram_total > 0:
        limit, used = _cap(ram_total, s), ram_used
    else:
        limit, used = _cap(snap.total, s), snap.used
    plan = []
    for model in candidates:
        if used + need <= limit:
            break
        plan.append(model.name)
        used -= model.size_vram
    return tuple(plan) if used + need <= limit else ()


def decide(snap: Snapshot, est: Estimate, s: Settings, *, last_used: Mapping[str, float],
           in_flight: Collection[str], now: float,
           ram_total: int = 0, ram_used: int = 0) -> Decision:
    resident = snap.loaded.get(est.model)
    # Ollama's /v1 ignores num_ctx, so a resident model is never reloaded bigger by a call.
    raw_need = 0 if resident else est.total
    single_slot = snap.max_loaded == 1 and resident is None
    swapped = snap.ollama_bytes if single_slot else 0
    pinned = 0 if single_slot else sum(
        m.size_vram for m in snap.loaded.values() if m.name in s.pinned_models and m.name != est.model)

    pool = s.budget_pool if s.budget_pool in ("gpu", "system_ram", "combined") else "gpu"
    if pool == "system_ram" and ram_total <= 0:
        pool = "gpu"
    if pool == "combined" and (snap.total <= 0 or ram_total <= 0):
        pool = "gpu" if snap.total else "system_ram"

    if pool == "combined":
        gpu_free = max(0, _gpu_free(snap, s) - pinned)
        ram_free = _ram_free(ram_total, ram_used, s)
        free = gpu_free + ram_free
        need = raw_need  # do not clamp — combined room is the clamp
        partial = raw_need > gpu_free
        fits = need <= free
        gpu_after = (snap.used + min(need, gpu_free)) / snap.total * 100 if snap.total else 0.0
        ram_after = (ram_used + max(0, need - gpu_free)) / ram_total * 100 if ram_total else 0.0
        after = max(gpu_after, ram_after)
    elif pool == "system_ram":
        used = ram_used
        limit = _cap(ram_total, s)
        # System RAM has no Ollama "external floor"; reserve already protects the desktop.
        ceiling = max(0, limit - pinned - (resident.size_vram if resident else 0))
        partial = raw_need > ceiling
        need = min(raw_need, ceiling)
        free = max(0, limit - used)
        after = (used + need) / ram_total * 100 if ram_total else 100.0
        fits = used + need <= limit
    else:
        used = snap.used - swapped
        limit = _cap(snap.total, s)
        ceiling = max(0, limit - min(snap.external_floor, snap.external) - pinned
                      - (resident.size_vram if resident else 0))
        partial = raw_need > ceiling
        need = min(raw_need, ceiling)
        free = max(0, limit - used)
        after = (used + need) / snap.total * 100 if snap.total else 100.0
        fits = used + need <= limit

    def verdict(action: str, reason: str, **extra) -> Decision:
        return Decision(action=action, reason=reason, need=need, free=free, after_pct=after,
                        partial=partial, pool=pool, **extra)

    if fits:
        return verdict(GO, "fits", warn=need > 0 and after >= s.warn_pct)
    if s.on_overflow == "warn_only":
        return verdict(PASS, f"{est.model} needs {need / GIB:.1f} GB, {free / GIB:.1f} GB free", warn=True)
    if s.on_overflow == "queue" and not single_slot:
        plan = plan_eviction(snap, est.model, need, s, last_used=last_used, in_flight=in_flight, now=now,
                             ram_total=ram_total, ram_used=ram_used)
        if plan:
            return verdict(EVICT, f"unload {', '.join(plan)} to fit {est.model}", evict=plan)
    others = [m for m in snap.loaded.values() if m.name != est.model]
    if s.on_overflow != "wait" and not others:
        return verdict(
            PASS,
            f"{est.model} needs {need / GIB:.1f} GB, {free / GIB:.1f} GB free; "
            f"nothing to unload, letting Ollama spill",
            warn=True)
    return verdict(WAIT, f"{est.model} needs {need / GIB:.1f} GB, {free / GIB:.1f} GB free")
