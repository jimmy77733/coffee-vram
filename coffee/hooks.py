"""Hermes registrations for the runtime half: the gate and the hooks that close its tickets."""

from __future__ import annotations

from typing import Any

from .gate import make_gate
from .monitor import Monitor


def register_runtime(ctx, monitor: Monitor) -> None:
    def on_api_done(*, api_request_id: str = "", model: str = "", usage: Any = None,
                    api_duration: float = 0, base_url: str = "", provider: str = "",
                    platform: str = "", session_id: str = "", **_: Any) -> None:
        monitor.finish(api_request_id)
        monitor.note_call(model, usage=usage if isinstance(usage, dict) else None,
                          api_duration=api_duration, base_url=base_url, provider=provider,
                          platform=platform, session_id=session_id)

    ctx.register_middleware("llm_execution", make_gate(monitor))
    ctx.register_hook("post_api_request", on_api_done)
    ctx.register_hook("api_request_error", on_api_done)
