# Changelog

All notable changes to Coffee VRAM Guard will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] - 2026-11-10

### Fixed
- **Catalog compliance**: Removed reading of Desktop app's internal localStorage (`hermes.desktop.composer.provider*` keys) to comply with Hermes plugin catalog rule 8. The provider label in the Desktop panel now relies solely on backend data from `snap.active.provider` (provided by `coffee/monitor.py` via `note_active()`).

### Changed
- Desktop plugin no longer probes `localStorage` for app-internal composer state
- Provider display now uses only SDK-provided state and backend monitoring data

## [0.1.2] - 2026-11-10

### Added
- Desktop chip and panel now show the active Hermes model (cloud or local)
- Provider label display (Ollama, Anthropic, OpenAI, etc.)
- Conversation context usage and tokens/second statistics
- Performance pet visualization showing GPU utilization with weather effects

### Changed
- Enhanced status chip with model name, provider, and performance metrics
- Improved panel layout with active model section separate from loaded models list

## [0.1.1] - 2026-11-09

### Changed
- Documentation improvements
- Internal refactoring for better code organization

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
