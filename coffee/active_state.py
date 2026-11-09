"""Cross-process active Hermes route: agent middleware writes, dashboard fallback reads."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_NAME = "coffee-vram-active.json"
# Ignore a stale file after a day — picker / config covers the cold start.
_MAX_AGE_S = 24 * 60 * 60


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        pass
    env = (os.environ.get("HERMES_HOME") or "").strip()
    if env:
        return Path(env)
    local = Path(os.environ.get("LOCALAPPDATA") or "") / "hermes"
    if local.is_dir():
        return local
    return Path.home() / ".hermes"


def state_path() -> Path:
    return _hermes_home() / _STATE_NAME


def write_active(payload: dict[str, Any]) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        fd, tmp = tempfile.mkstemp(prefix=".coffee-active-", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(raw)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        logger.debug("coffee-vram: could not persist active model", exc_info=True)


def read_active(*, now: float | None = None) -> dict[str, Any] | None:
    path = state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not str(data.get("model") or "").strip():
        return None
    at = data.get("at")
    if isinstance(at, (int, float)) and now is not None and now - float(at) > _MAX_AGE_S:
        return None
    return data
