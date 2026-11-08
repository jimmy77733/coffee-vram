"""User settings: the ``config_schema`` keys in plugin.yaml, read through ``ctx.get_config``."""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from typing import Any, Callable

logger = logging.getLogger(__name__)

OVERFLOW_ACTIONS = ("queue", "wait", "warn_only")
MODES = ("simple", "advanced")
KV_CACHE_TYPES = ("f16", "q8_0", "q4_0")
ALERT_SOURCES = ("gpu", "system_ram")
BUDGET_POOLS = ("gpu", "system_ram", "combined")


@dataclass(frozen=True)
class Settings:
    mode: str = "simple"
    alert_source: str = "gpu"
    budget_pool: str = "gpu"
    warn_pct: int = 85
    hold_pct: int = 95
    on_overflow: str = "queue"
    keep_warm: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    reserve_gb: float = 2.0
    max_concurrent_local: int = 1
    queue_timeout_s: int = 600
    evict_idle_s: int = 20
    pinned_models: tuple[str, ...] = ()
    kv_cache_type: str = "f16"
    estimate_overhead_pct: int = 10
    sample_interval_ms: int = 1000
    watch_external: bool = True
    gpu_total_override_gb: float = 0.0
    show_system_ram: bool = True
    show_cpu: bool = True
    show_network: bool = True
    show_gpu_temp: bool = True
    show_model_stats: bool = True


_CHOICES: dict[str, tuple[str, ...]] = {
    "mode": MODES, "alert_source": ALERT_SOURCES, "budget_pool": BUDGET_POOLS,
    "on_overflow": OVERFLOW_ACTIONS, "kv_cache_type": KV_CACHE_TYPES,
}
_RANGES: dict[str, tuple[float, float]] = {
    "warn_pct": (1, 100), "hold_pct": (1, 100), "reserve_gb": (0, 1024),
    "max_concurrent_local": (1, 16), "queue_timeout_s": (0, 86_400), "evict_idle_s": (0, 86_400),
    "estimate_overhead_pct": (0, 200), "sample_interval_ms": (250, 60_000),
    "gpu_total_override_gb": (0, 4096),
}


def _coerce(name: str, raw: Any, default: Any) -> Any:
    """``raw`` as the field's type, or None when it does not fit the schema."""
    if isinstance(default, bool):
        return raw if isinstance(raw, bool) else None
    if isinstance(default, (int, float)):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        value = type(default)(raw)
        low, high = _RANGES.get(name, (float("-inf"), float("inf")))
        return value if low <= value <= high else None
    if isinstance(default, tuple):
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            return None
        return tuple(item.strip() for item in raw if item.strip())
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    return value if value in _CHOICES.get(name, (value,)) else None


def load_settings(get_config: Callable[[str, Any], Any]) -> Settings:
    """Build Settings from ``get_config(key, default)``; an invalid value falls back to its default."""
    defaults = Settings()
    values: dict[str, Any] = {}
    for f in fields(Settings):
        default = getattr(defaults, f.name)
        raw = get_config(f.name, None)
        if raw is None:
            continue
        value = _coerce(f.name, raw, default)
        if value is None:
            logger.warning("coffee-vram: ignoring invalid setting %s=%r; using %r", f.name, raw, default)
            continue
        values[f.name] = value
    settings = Settings(**{**{f.name: getattr(defaults, f.name) for f in fields(Settings)}, **values})
    if settings.warn_pct > settings.hold_pct:
        logger.warning("coffee-vram: warn_pct %d is above hold_pct %d; warning at hold_pct",
                       settings.warn_pct, settings.hold_pct)
        settings = Settings(**{**settings.__dict__, "warn_pct": settings.hold_pct})
    return settings
