"""Point-in-time view of the GPU fast pool and the models Ollama holds in it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

GIB = 1 << 30


@dataclass(frozen=True)
class LoadedModel:
    name: str
    size: int
    size_vram: int
    context_length: int | None = None
    expires_at: float | None = None

    @property
    def spill(self) -> int:
        """Bytes Ollama placed in system RAM instead of device memory."""
        return max(0, self.size - self.size_vram)


@dataclass(frozen=True)
class Snapshot:
    total: int
    used: int
    loaded: Mapping[str, LoadedModel] = field(default_factory=dict)
    # OLLAMA_MAX_LOADED_MODELS when known: at 1, Ollama unloads the resident model itself on a switch.
    max_loaded: int | None = None
    # Lowest non-Ollama usage seen recently: the desktop's share that never frees up. Usage above
    # it (ComfyUI, a game) is transient and worth waiting for.
    external_floor: int = 0
    source: str = "nvml"
    at: float = 0.0

    @property
    def ollama_bytes(self) -> int:
        return sum(m.size_vram for m in self.loaded.values())

    @property
    def external(self) -> int:
        return max(0, self.used - self.ollama_bytes)

    @property
    def used_pct(self) -> float:
        return self.used / self.total * 100 if self.total else 0.0
