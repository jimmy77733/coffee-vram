"""``/vram`` in sessions, ``hermes coffee-vram`` in the terminal, and the ``vram_status`` tool."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any

from . import VERSION
from .monitor import Monitor
from .report import models_table, queue_table, status_line, tool_payload

USAGE = "Usage: /vram [queue | models | settings | evict <model>]"

VRAM_STATUS_SCHEMA = {
    "name": "vram_status",
    "description": ("GPU memory on this machine and the local Ollama models loaded in it. Check this before "
                    "switching to a different local model: loading one that does not fit unloads others "
                    "or runs partly on the CPU."),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


def evict(monitor: Monitor, model: str) -> str:
    model = model.strip()
    if not model:
        return "Usage: /vram evict <model>"
    loaded = {m["name"] for m in monitor.status()["loaded"]}
    if model not in loaded:
        return f"{model} is not loaded. Loaded: {', '.join(sorted(loaded)) or 'none'}."
    if model in monitor.scheduler.in_flight():
        return f"{model} is answering a call right now; try again when it finishes."
    return f"Unloaded {model}." if monitor.unload(model, "requested by the user") else f"Could not unload {model}."


def handle_vram(monitor: Monitor, raw_args: str) -> str:
    command, _, rest = raw_args.strip().partition(" ")
    command = command.lower()
    if command in ("", "status"):
        return f"Coffee {VERSION} · {status_line(monitor.status())}"
    if command == "queue":
        return queue_table(monitor.status())
    if command == "models":
        return models_table(monitor.status())
    if command == "settings":
        return "\n".join(f"{key}: {value}" for key, value in asdict(monitor.settings(fresh=True)).items())
    if command == "evict":
        return evict(monitor, rest)
    return USAGE


def setup_cli(parser: argparse.ArgumentParser) -> None:
    subs = parser.add_subparsers(dest="coffee_command")
    status = subs.add_parser("status", help="GPU memory, loaded models and settings")
    status.add_argument("--json", action="store_true", help="Machine-readable output")
    unload = subs.add_parser("evict", help="Unload a model from Ollama")
    unload.add_argument("model")
    unload.add_argument("--yes", action="store_true", help="Do not ask for confirmation")


def run_cli(monitor: Monitor, args: argparse.Namespace) -> int:
    if getattr(args, "coffee_command", None) == "evict":
        if not args.yes and input(f"Unload {args.model} from Ollama? [y/N] ").strip().lower() != "y":
            return 1
        message = evict(monitor, args.model)
        print(message)
        return 0 if message.startswith("Unloaded") else 1
    status = monitor.status()
    if getattr(args, "json", False):
        print(json.dumps({"version": VERSION, "status": status,
                          "settings": asdict(monitor.settings())}, indent=2, default=str))
    else:
        print(f"Coffee {VERSION} · {status_line(status)}")
        print(models_table(status))
    return 0


def register_commands(ctx, monitor: Monitor) -> None:
    def vram(raw_args: str = "", **_: Any) -> str:
        return handle_vram(monitor, raw_args)

    def vram_status(args: dict | None = None, **_: Any) -> str:
        return json.dumps(tool_payload(monitor.status()))

    ctx.register_command("vram", vram, description="GPU memory, loaded local models and Coffee's queue",
                         args_hint="[queue|models|settings|evict <model>]")
    ctx.register_cli_command("coffee-vram", help="Coffee VRAM guard", setup_fn=setup_cli,
                             handler_fn=lambda args: run_cli(monitor, args),
                             description="Inspect GPU memory and local Ollama models; unload one.")
    ctx.register_tool("vram_status", "coffee-vram", VRAM_STATUS_SCHEMA, vram_status,
                      description="GPU memory and loaded local models", emoji="◆")
