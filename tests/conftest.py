"""Import the plugin as the package ``coffee_vram_plugin`` and give every test an empty HERMES_HOME."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/fakes.py


def _load_plugin_package() -> None:
    if "coffee_vram_plugin" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "coffee_vram_plugin", PLUGIN_ROOT / "__init__.py", submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["coffee_vram_plugin"] = module
    spec.loader.exec_module(module)


_load_plugin_package()


@pytest.fixture(autouse=True)
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home
