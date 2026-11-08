"""The few Ollama endpoints Coffee needs: what is loaded, model shapes, keep-alive and unload."""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from .snapshot import LoadedModel

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


class OllamaError(RuntimeError):
    pass


def _epoch(stamp: Any) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_ps(payload: Any) -> dict[str, LoadedModel]:
    loaded: dict[str, LoadedModel] = {}
    for entry in (payload or {}).get("models") or []:
        name = entry.get("name") or entry.get("model")
        if not name:
            continue
        size = int(entry.get("size") or 0)
        loaded[name] = LoadedModel(name=name, size=size, size_vram=int(entry.get("size_vram", size) or 0),
                                   context_length=entry.get("context_length") or None,
                                   expires_at=_epoch(entry.get("expires_at")))
    return loaded


def same_endpoint(base_url: str | None, ollama_url: str) -> bool:
    """Whether a provider ``base_url`` (``http://localhost:11434/v1``) points at ``ollama_url``."""
    if not base_url:
        return False
    a, b = urlsplit(base_url), urlsplit(ollama_url)
    host_a, host_b = (a.hostname or "").lower(), (b.hostname or "").lower()
    if host_a in _LOCAL_HOSTS and host_b in _LOCAL_HOSTS:
        host_a = host_b
    return host_a == host_b and (a.port or 11434) == (b.port or 11434)


class OllamaClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._shapes: dict[str, dict[str, Any]] = {}
        self._sizes: dict[str, int] = {}
        self._lock = threading.Lock()

    def _call(self, path: str, body: dict | None = None, timeout: float = 3.0) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"{self.base_url}{path}", data=data,
                                         headers={"Content-Type": "application/json"},
                                         method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                raw = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaError(f"{path}: {exc}") from exc
        return json.loads(raw) if raw else None

    def ps(self) -> dict[str, LoadedModel]:
        return parse_ps(self._call("/api/ps", timeout=2.0))

    def file_size(self, model: str) -> int:
        with self._lock:
            if model in self._sizes:
                return self._sizes[model]
        tags = self._call("/api/tags", timeout=5.0) or {}
        sizes = {m.get("name"): int(m.get("size") or 0) for m in tags.get("models") or [] if m.get("name")}
        with self._lock:
            self._sizes.update(sizes)
            return self._sizes.get(model, 0)

    def model_info(self, model: str) -> dict[str, Any]:
        with self._lock:
            if model in self._shapes:
                return self._shapes[model]
        info = (self._call("/api/show", {"model": model}, timeout=10.0) or {}).get("model_info") or {}
        with self._lock:
            self._shapes[model] = info
        return info

    def keep_alive(self, model: str, value: str | int) -> None:
        """Set how long Ollama keeps ``model`` resident; it sticks until the next request sets one.

        Never call this for a model that is not loaded: a bare ``/api/generate`` loads it.
        """
        self._call("/api/generate", {"model": model, "keep_alive": value}, timeout=10.0)

    def unload(self, model: str) -> None:
        self.keep_alive(model, 0)
