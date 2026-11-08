"""Coffee: a VRAM guard for local Ollama models in Hermes Agent."""

from __future__ import annotations

# The process's Monitor, published for dashboard/plugin_api.py, which Hermes imports as a separate
# top-level module and so cannot reach this package through a relative import.
MONITOR = None


def register(ctx) -> None:
    # Imported here, not at module top: pytest imports this file bare when it collects the repo.
    from .coffee.commands import register_commands
    from .coffee.hooks import register_runtime
    from .coffee.monitor import Monitor

    global MONITOR
    MONITOR = Monitor(ctx.get_config)
    register_runtime(ctx, MONITOR)
    register_commands(ctx, MONITOR)
