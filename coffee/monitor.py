"""Coffee's live state: sampling, keep-warm upkeep, the event log the UI polls, and history.

Nothing here touches the network or starts a thread until ``ensure_started`` (the doctor runs
``register()`` with sockets blocked).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import asdict
from typing import Any, Callable, Mapping

from . import active_state
from .estimator import Estimate, effective_context, estimate
from .gpu import GpuBackend, open_backend
from .hoststats import HostSampler
from .ollama import OllamaClient, OllamaError, same_endpoint
from .scheduler import Scheduler, Ticket
from .settings import Settings, load_settings
from .snapshot import GIB, LoadedModel, Snapshot

logger = logging.getLogger(__name__)

PLUGIN_ID = "coffee-vram"
KEEP_WARM_REFRESH = "10m"
KEEP_WARM_LEAD_S = 90
PENDING_MAX_AGE_S = 30 * 60
SETTINGS_TTL_S = 2.0
EXTERNAL_FLOOR_WINDOW_S = 15 * 60
# Same GPU-memory warning does not re-fire while usage stays high or flickers around the line.
LEVEL_ALERT_COOLDOWN_S = 5 * 60


def _env_int(env: Mapping[str, str], name: str) -> int | None:
    try:
        value = int(env.get(name, ""))
    except ValueError:
        return None
    return value if value > 0 else None


def _broadcast(event: str, payload: dict) -> None:
    """Live push where the gateway shares our process; the plugin host has no transport, so the
    UI also polls ``/events``."""
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event
    except ImportError:
        return
    try:
        broadcast_plugin_event(PLUGIN_ID, event, payload)
    except Exception:
        logger.debug("coffee-vram: broadcast of %s failed", event, exc_info=True)


class Monitor:
    def __init__(self, get_config: Callable[[str, Any], Any], *,
                 gpu_factory: Callable[[], GpuBackend] = open_backend,
                 client_factory: Callable[[str], OllamaClient] = OllamaClient,
                 env: Mapping[str, str] | None = None, broadcast: Callable[[str, dict], None] = _broadcast) -> None:
        self._get_config = get_config
        self._gpu_factory, self._client_factory = gpu_factory, client_factory
        self._env = os.environ if env is None else env
        self._broadcast = broadcast
        self.scheduler = Scheduler()
        self._lock = threading.RLock()
        self._settings: Settings | None = None
        self._settings_at = 0.0
        self._gpu: GpuBackend | None = None
        self._client: OllamaClient | None = None
        self._snap: Snapshot | None = None
        self._ollama_up = True
        self._observed: dict[str, LoadedModel] = {}
        self._active_at: dict[str, float] = {}
        # (t, used, total, ollama_bytes, gpu_util_pct|None)
        self._history: deque[tuple[float, int, int, int, float | None]] = deque(maxlen=900)
        self._events: deque[dict] = deque(maxlen=200)
        self._seq = 0
        self._level = "ok"
        self._level_alert_at = 0.0
        self._pending: dict[str, tuple[Ticket, float]] = {}
        self._partial_noted: set[str] = set()
        self._call_stats: dict[str, dict[str, Any]] = {}
        # Last Hermes LLM route seen (local Ollama or any cloud provider).
        self._active: dict[str, Any] | None = None
        self._host = HostSampler()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ── wiring ──────────────────────────────────────────────────────────────
    def settings(self, *, fresh: bool = False) -> Settings:
        with self._lock:
            now = time.monotonic()
            if fresh or self._settings is None or now - self._settings_at > SETTINGS_TTL_S:
                self._settings, self._settings_at = load_settings(self._get_config), now
            return self._settings

    def client(self) -> OllamaClient:
        url = self.settings().ollama_url
        with self._lock:
            if self._client is None or self._client.base_url != url.rstrip("/"):
                self._client = self._client_factory(url)
            return self._client

    def gpu(self) -> GpuBackend:
        with self._lock:
            if self._gpu is None:
                self._gpu = self._gpu_factory()
            return self._gpu

    def ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="coffee-monitor", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ── events ──────────────────────────────────────────────────────────────
    def emit(self, kind: str, message: str, *, level: str = "info", **data: Any) -> dict:
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "at": time.time(), "kind": kind, "level": level,
                     "message": message, **data}
            self._events.append(event)
        logger.log(logging.WARNING if level == "alert" else logging.INFO, "coffee-vram: %s", message)
        self._broadcast("event", event)
        return event

    def events(self, since: int = 0) -> list[dict]:
        with self._lock:
            return [e for e in self._events if e["seq"] > since]

    def history(self) -> list[tuple[float, int, int, int, float | None]]:
        with self._lock:
            return list(self._history)

    # ── sampling ────────────────────────────────────────────────────────────
    def sample(self) -> Snapshot:
        s = self.settings()
        reading = self.gpu().read()
        try:
            loaded = self.client().ps()
            up = True
        except OllamaError:
            loaded, up = {}, False
        now = time.time()
        ollama_bytes = sum(m.size_vram for m in loaded.values())
        total = int(s.gpu_total_override_gb * GIB) or (reading.total if reading else 0)
        used = reading.used if reading and s.watch_external else ollama_bytes
        util = self._host.read(self.gpu()).gpu_util_pct
        with self._lock:
            floor = min([used - ollama_bytes] + [u - o for t, u, _, o, *_ in self._history
                                                 if t >= now - EXTERNAL_FLOOR_WINDOW_S])
        snap = Snapshot(total=total, used=used, loaded=loaded,
                        max_loaded=_env_int(self._env, "OLLAMA_MAX_LOADED_MODELS"),
                        external_floor=max(0, floor), source=reading.source if reading else "ollama", at=now)
        with self._lock:
            flipped, self._ollama_up = up != self._ollama_up, up
            for name, model in loaded.items():
                before = self._observed.get(name)
                if before is None or (model.expires_at or 0) > (before.expires_at or 0) + 1:
                    self._active_at[name] = now
                self._observed[name] = model
            self._snap = snap
            self._history.append((now, used, total, ollama_bytes, util))
        if flipped:
            self.emit("ollama", "Ollama is reachable again" if up else f"Ollama is not answering at {s.ollama_url}",
                      level="info" if up else "warn")
        self._track_level(snap, s)
        return snap

    def snapshot(self, *, fresh: bool = False) -> Snapshot:
        with self._lock:
            snap = self._snap
            interval = self.settings().sample_interval_ms / 1000
        if fresh or snap is None or time.time() - snap.at > interval:
            return self.sample()
        return snap

    def host_stats(self, *, fresh: bool = False):
        return self._host.read(self.gpu(), fresh=fresh)

    def _alert_pct(self, snap: Snapshot, s: Settings) -> tuple[float, str] | None:
        """(pct, label) for chip/toasts. Fit checks use ``budget_pool`` separately."""
        if s.alert_source == "system_ram":
            host = self._host.read(self.gpu())
            if not host.ram_total:
                return None
            return host.ram_pct, "System memory"
        if not snap.total:
            return None
        return snap.used_pct, "GPU memory"

    def _track_level(self, snap: Snapshot, s: Settings) -> None:
        tracked = self._alert_pct(snap, s)
        if tracked is None:
            return
        pct, label = tracked
        level = "hold" if pct >= s.hold_pct else "warn" if pct >= s.warn_pct else "ok"
        with self._lock:
            previous, self._level = self._level, level
        if level == previous:
            return
        self._broadcast("level", {"level": level, "used_pct": round(pct, 1), "alert_source": s.alert_source})
        # Only the rising edge from ok gets a toast/event. warn↔hold flicker only moves the chip.
        if level == "ok" or previous != "ok":
            return
        now = time.monotonic()
        with self._lock:
            if now - self._level_alert_at < LEVEL_ALERT_COOLDOWN_S:
                return
            self._level_alert_at = now
        self.emit("level", f"{label} at {pct:.0f}%", level="warn" if level == "warn" else "alert",
                  used_pct=round(pct, 1), alert_source=s.alert_source)

    # ── estimates and actions ───────────────────────────────────────────────
    def estimate(self, model: str) -> Estimate:
        s = self.settings()
        self.snapshot()
        with self._lock:
            observed = self._observed.get(model)
        try:
            client = self.client()
            info = client.model_info(model)
            size = client.file_size(model)
        except OllamaError:
            if observed is None:
                return Estimate(model=model, context=0, weights=0, kv=0, overhead=0, source="unknown")
            info, size = {}, 0
        arch = info.get("general.architecture")
        model_max = info.get(f"{arch}.context_length") if arch else None
        context = effective_context(
            observed=observed.context_length if observed else None,
            server_default=_env_int(self._env, "OLLAMA_CONTEXT_LENGTH"),
            model_max=model_max if isinstance(model_max, int) else None)
        return estimate(model, file_size=size, model_info=info, context=context, kv_type=s.kv_cache_type,
                        parallel=_env_int(self._env, "OLLAMA_NUM_PARALLEL") or 1,
                        overhead_pct=s.estimate_overhead_pct, observed=observed)

    def last_active(self) -> dict[str, float]:
        """When each model was last used, by Hermes or by any other Ollama client (its expiry moved)."""
        used = self.scheduler.last_used()
        with self._lock:
            seen = dict(self._active_at)
        return {name: max(seen.get(name, 0.0), used.get(name, 0.0)) for name in seen.keys() | used.keys()}

    def note_partial(self, model: str) -> None:
        """Say once per model that Ollama will split it between GPU and CPU."""
        with self._lock:
            if model in self._partial_noted:
                return
            self._partial_noted.add(model)
        self.emit("partial", f"{model} is larger than the free GPU memory; Ollama runs the rest on the CPU",
                  model=model)

    def unload(self, model: str, reason: str) -> bool:
        try:
            self.client().unload(model)
        except OllamaError as exc:
            self.emit("evict_failed", f"Could not unload {model}: {exc}", level="warn", model=model)
            return False
        with self._lock:
            self._snap = None
        self.emit("evicted", f"Unloaded {model} ({reason})", model=model)
        self.scheduler.poke()
        return True

    def hold_until_done(self, api_request_id: str, ticket: Ticket) -> None:
        """Keep ``ticket``'s slot until the post/error hook for ``api_request_id`` (plugin host)."""
        with self._lock:
            self._pending[api_request_id or f"ticket-{ticket.id}"] = (ticket, time.monotonic())

    def finish(self, api_request_id: str) -> None:
        with self._lock:
            entry = self._pending.pop(api_request_id, None)
        if entry is not None:
            self.scheduler.release(entry[0])

    def note_active(self, model: str, *, provider: str | None = None, base_url: str | None = None,
                    platform: str | None = None, session_id: str | None = None) -> None:
        """Remember the Hermes route currently in use (Ollama or cloud)."""
        if not model:
            return
        local = same_endpoint(base_url, self.settings().ollama_url)
        provider_name = (provider or "").strip() or ("ollama" if local else "cloud")
        payload = {
            "model": model,
            "provider": provider_name,
            "base_url": (base_url or "").rstrip("/") or None,
            "platform": (platform or "").strip() or None,
            "session_id": (session_id or "").strip() or None,
            "local": local,
            "at": time.time(),
            "source": "live",
        }
        with self._lock:
            self._active = payload
        # Dashboard often runs in another process (shared_queue=false); persist for it.
        active_state.write_active(payload)

    def _persisted_active(self) -> dict[str, Any] | None:
        data = active_state.read_active(now=time.time())
        return dict(data) if data else None

    def _configured_active(self) -> dict[str, Any] | None:
        """Fallback when no LLM call has been observed yet: Hermes config default model."""
        try:
            from hermes_cli.config import load_config_readonly
            cfg = load_config_readonly() or {}
        except Exception:
            return None
        model_cfg = cfg.get("model") if isinstance(cfg, dict) else None
        if not isinstance(model_cfg, dict):
            return None
        name = str(model_cfg.get("default") or model_cfg.get("model") or "").strip()
        if not name:
            return None
        provider = str(model_cfg.get("provider") or "").strip() or "unknown"
        base = str(model_cfg.get("base_url") or "").strip() or None
        local = same_endpoint(base, self.settings().ollama_url) if base else provider in {
            "ollama", "custom", "llamacpp", "lmstudio", "vllm"
        }
        return {
            "model": name,
            "provider": provider,
            "base_url": base.rstrip("/") if base else None,
            "platform": None,
            "session_id": None,
            "local": bool(local),
            "at": None,
            "source": "config",
        }

    def note_call(self, model: str, *, usage: Mapping[str, Any] | None, api_duration: float,
                  base_url: str | None, provider: str | None = None,
                  platform: str | None = None, session_id: str | None = None) -> None:
        """Record call stats for local Ollama; always refresh the active-route display."""
        self.note_active(model, provider=provider, base_url=base_url,
                         platform=platform, session_id=session_id)
        if not model or not same_endpoint(base_url, self.settings().ollama_url):
            return
        usage = usage or {}
        prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        output = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        try:
            prompt, output, duration = int(prompt), int(output), float(api_duration or 0)
        except (TypeError, ValueError):
            return
        tps = (output / duration) if duration > 0 and output > 0 else None
        with self._lock:
            self._call_stats[model] = {
                "prompt_tokens": prompt or None, "output_tokens": output or None,
                "tps": round(tps, 1) if tps is not None else None, "at": time.time(),
            }

    # ── background upkeep ───────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("coffee-vram: monitor tick failed")
            self._stop.wait(self.settings().sample_interval_ms / 1000)

    def tick(self) -> None:
        snap = self.sample()
        s = self.settings()
        self._expire_pending()
        busy = self.scheduler.in_flight()
        last_active = self.last_active()
        now = time.time()
        for model in list(snap.loaded.values()):
            if model.name in busy:
                continue
            if s.keep_warm:
                if (model.expires_at and model.expires_at - now < KEEP_WARM_LEAD_S
                        and snap.total and snap.used_pct < s.warn_pct):
                    try:
                        self.client().keep_alive(model.name, KEEP_WARM_REFRESH)
                    except OllamaError:
                        logger.debug("coffee-vram: keep-warm for %s failed", model.name, exc_info=True)
            elif model.name not in s.pinned_models:
                active = last_active.get(model.name, now)
                if now - active >= s.evict_idle_s:
                    self.unload(model.name, f"idle {int(now - active)}s, keep warm off")

    def _expire_pending(self) -> None:
        cutoff = time.monotonic() - PENDING_MAX_AGE_S
        with self._lock:
            stale = [key for key, (_, started) in self._pending.items() if started < cutoff]
            tickets = [self._pending.pop(key)[0] for key in stale]
        for ticket in tickets:
            self.scheduler.release(ticket)

    # ── views ───────────────────────────────────────────────────────────────
    def status(self) -> dict:
        snap = self.snapshot()
        s = self.settings()
        host = self._host.read(self.gpu())
        with self._lock:
            stats = dict(self._call_stats)
            live = dict(self._active) if self._active else None
        active = None
        if s.show_active_model:
            active = live or self._persisted_active() or self._configured_active()
        loaded = []
        for m in snap.loaded.values():
            call = stats.get(m.name, {})
            loaded.append({
                **asdict(m), "spill": m.spill, "pinned": m.name in s.pinned_models,
                "prompt_tokens": call.get("prompt_tokens"),
                "tps": call.get("tps"),
            })
        return {
            "total": snap.total, "used": snap.used, "used_pct": round(snap.used_pct, 1),
            "ollama_bytes": snap.ollama_bytes, "external": snap.external,
            "external_floor": snap.external_floor, "source": snap.source, "at": snap.at,
            "level": self._level, "ollama_up": self._ollama_up, "max_loaded": snap.max_loaded,
            "warn_pct": s.warn_pct, "hold_pct": s.hold_pct, "mode": s.mode,
            "alert_source": s.alert_source,
            "budget_pool": s.budget_pool,
            "alert_pct": None if (tracked := self._alert_pct(snap, s)) is None else round(tracked[0], 1),
            "show_system_ram": s.show_system_ram, "show_cpu": s.show_cpu,
            "show_network": s.show_network, "show_gpu_temp": s.show_gpu_temp,
            "show_model_stats": s.show_model_stats,
            "show_active_model": s.show_active_model,
            "host": {
                "ram_total": host.ram_total, "ram_used": host.ram_used,
                "ram_pct": round(host.ram_pct, 1),
                "cpu_pct": None if host.cpu_pct is None else round(host.cpu_pct, 1),
                "net_bps": None if host.net_bps is None else round(host.net_bps),
                "gpu_temp_c": host.gpu_temp_c,
                "gpu_util_pct": None if host.gpu_util_pct is None else round(host.gpu_util_pct, 1),
                "gpu_name": host.gpu_name, "cpu_name": host.cpu_name, "ram_name": host.ram_name,
            },
            "active": active,
            "loaded": loaded,
            "queue": [asdict(t) for t in self.scheduler.tickets()],
            "last_event": self._seq,
        }
