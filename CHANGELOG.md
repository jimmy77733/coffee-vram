# Changelog

All notable changes to Coffee VRAM Guard will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] - 2026-11-10

### Fixed
- **Catalog compliance**: Removed `readDesktopProvider()`, which read the Desktop app's internal localStorage (`hermes.desktop.composer.provider*` keys), to comply with Hermes plugin catalog rule 8. The provider label in the Desktop panel now relies solely on backend data from `snap.active.provider` (provided by `coffee/monitor.py` via `note_active()`).

### Changed
- Desktop plugin no longer probes `localStorage` for app-internal composer state. The pet visibility preference `coffee-vram.pet` is unchanged.
- Provider display now uses only SDK-provided state and backend monitoring data.
- Removed the unrecognized `provides_middleware` field from `plugin.yaml`. Middleware is still registered in code via `ctx.register_middleware()` in `coffee/hooks.py`. This clears the Hermes v0.23 warning about an unknown manifest field.
- Resized `assets/cover.jpg` from 1280×720 (16:9) to 1200×600 (2:1), about 112 KB, for the catalog image ratio.

## [0.1.2] - 2026-11-09

### Added
- Desktop chip and panel show the active Hermes model (cloud or local) in an **In use** section, separate from Ollama's loaded-models list.
- Provider label on that section, from `snap.active.provider`.
- Context usage and tokens/second for the active route on the chip and under In use (current conversation usage and, for a matching loaded model, last-call stats).
- Cross-process active-route file `coffee-vram-active.json` in the Hermes home directory, so the Desktop or dashboard process can read the model the agent middleware recorded.
- Setting `show_active_model` (default on).

### Changed
- Status chip can include the active model name alongside the existing memory and host metrics.
- Panel layout puts the active model section above the loaded models list.

## [0.1.1] - 2026-11-08

### Added
- Desktop **Performance** pet with decorative weather (clear, rain, snow, thunder, fog). Colour and run speed follow GPU compute utilization. A panel toggle stores on/off in the `localStorage` key `coffee-vram.pet` (on by default).
- Status chip also shows GPU temperature, system RAM, CPU, and network throughput when those settings are on, plus context usage and tok/s for a loaded Ollama model.
- Marketplace cover art (`assets/cover.jpg`).

### Changed
- Host-stats polish: panel rows can show GPU, CPU, and RAM capacity or device names.
- Redacted local paths from measurement dumps.

### Removed
- 15-minute memory/compute history sparkline from the Desktop panel. The backend still keeps samples and serves `GET /history`.

## [0.1.0] - 2026-11-08

### Added
- Initial release of Coffee VRAM Guard
- VRAM fit-check before local Ollama calls
- Call queue management with configurable timeout
- Keep-warm functionality for loaded models
- Idle model eviction with LRU strategy
- Desktop status bar chip with GPU memory percentage
- Desktop panel with memory gauges, loaded models, and queue status
- CLI commands: `hermes coffee-vram status`, `hermes coffee-vram evict`
- Chat commands: `/vram`, `/vram queue`, `/vram models`, `/vram settings`, `/vram evict`
- Agent tool: `vram_status` for programmatic memory checks
- Support for NVIDIA GPUs with NVML telemetry
- Configurable warning and hold thresholds
- Pinned models that are never unloaded
- Advanced mode with history and event log
- Host system stats (CPU, RAM, network, GPU temperature)
