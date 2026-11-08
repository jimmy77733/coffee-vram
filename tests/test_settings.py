from __future__ import annotations

from dataclasses import asdict, fields
from pathlib import Path

import yaml

from coffee_vram_plugin.coffee.settings import Settings, load_settings

MANIFEST = Path(__file__).resolve().parents[1] / "plugin.yaml"


def _getter(values: dict):
    return lambda key, default=None: values.get(key, default)


def test_code_defaults_match_the_manifest_schema():
    schema = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["config_schema"]
    defaults = asdict(Settings())
    assert set(schema) == {f.name for f in fields(Settings)}
    for key, spec in schema.items():
        expected = spec["default"]
        assert defaults[key] == (tuple(expected) if isinstance(expected, list) else expected), key


def test_unset_settings_use_defaults():
    assert load_settings(_getter({})) == Settings()


def test_valid_values_are_applied():
    settings = load_settings(_getter({"warn_pct": 70, "on_overflow": "wait", "pinned_models": ["qwen3:8b "],
                                      "reserve_gb": 4}))
    assert (settings.warn_pct, settings.on_overflow, settings.pinned_models) == (70, "wait", ("qwen3:8b",))
    assert settings.reserve_gb == 4.0 and isinstance(settings.reserve_gb, float)


def test_invalid_values_fall_back_to_defaults():
    settings = load_settings(_getter({"warn_pct": "high", "on_overflow": "block", "keep_warm": "yes",
                                      "hold_pct": 250, "pinned_models": "qwen"}))
    assert settings == Settings()


def test_warn_above_hold_warns_at_hold():
    settings = load_settings(_getter({"warn_pct": 98, "hold_pct": 90}))
    assert settings.warn_pct == settings.hold_pct == 90
