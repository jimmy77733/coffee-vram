"""In-memory stand-ins for the GPU and Ollama, sharing one pool so unloads free memory."""

from __future__ import annotations

from coffee_vram_plugin.coffee.gpu import GpuReading
from coffee_vram_plugin.coffee.monitor import Monitor
from coffee_vram_plugin.coffee.ollama import OllamaError
from coffee_vram_plugin.coffee.snapshot import GIB, LoadedModel


class Pool:
    def __init__(self, total_gib: float = 64, baseline_gib: float = 3) -> None:
        self.total = int(total_gib * GIB)
        self.baseline = int(baseline_gib * GIB)
        self.loaded: dict[str, LoadedModel] = {}
        self.extra = 0  # memory held outside Ollama (ComfyUI, games)

    @property
    def used(self) -> int:
        return self.baseline + self.extra + sum(m.size_vram for m in self.loaded.values())

    def load(self, name: str, size_gib: float, *, ctx: int = 8192, expires_at: float | None = None) -> None:
        size = int(size_gib * GIB)
        self.loaded[name] = LoadedModel(name, size=size, size_vram=size, context_length=ctx, expires_at=expires_at)


class FakeGpu:
    name = "fake"

    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    def read(self) -> GpuReading:
        return GpuReading(total=self.pool.total, used=self.pool.used, name="Fake GPU", source="fake")


class FakeClient:
    def __init__(self, pool: Pool, base_url: str = "http://127.0.0.1:11434") -> None:
        self.pool = pool
        self.base_url = base_url.rstrip("/")
        self.calls: list[tuple] = []
        self.sizes: dict[str, int] = {}
        self.down = False

    def ps(self) -> dict[str, LoadedModel]:
        if self.down:
            raise OllamaError("down")
        return dict(self.pool.loaded)

    def model_info(self, model: str) -> dict:
        if self.down:
            raise OllamaError("down")
        return {}

    def file_size(self, model: str) -> int:
        if self.down:
            raise OllamaError("down")
        return self.sizes.get(model, 0)

    def keep_alive(self, model: str, value) -> None:
        self.calls.append(("keep_alive", model, value))

    def unload(self, model: str) -> None:
        self.calls.append(("unload", model))
        self.pool.loaded.pop(model, None)


def make_monitor(pool: Pool, settings: dict | None = None, env: dict | None = None):
    client = FakeClient(pool)
    values = dict(settings or {})
    monitor = Monitor(lambda key, default=None: values.get(key, default), gpu_factory=lambda: FakeGpu(pool),
                      client_factory=lambda url: client, env=env or {}, broadcast=lambda *a: None)
    return monitor, client
