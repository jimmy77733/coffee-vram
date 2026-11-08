from __future__ import annotations

from coffee_vram_plugin.coffee.estimator import Estimate
from coffee_vram_plugin.coffee.policy import EVICT, GO, PASS, WAIT, decide
from coffee_vram_plugin.coffee.settings import Settings
from coffee_vram_plugin.coffee.snapshot import GIB, LoadedModel, Snapshot

NOW = 10_000.0
# Defaults: reserve 2 GiB, hold 95% → on a 64 GiB pool loads may reach 60.8 GiB.
S = Settings()


def gib(n: float) -> int:
    return int(n * GIB)


def model(name: str, size_gib: float) -> LoadedModel:
    return LoadedModel(name, size=gib(size_gib), size_vram=gib(size_gib))


def snap(used_gib: float, *models: LoadedModel, max_loaded: int | None = 3,
         floor_gib: float | None = None) -> Snapshot:
    """``floor_gib`` defaults to all current non-Ollama usage being the desktop's permanent share."""
    ollama = sum(m.size_vram for m in models)
    floor = gib(used_gib) - ollama if floor_gib is None else gib(floor_gib)
    return Snapshot(total=gib(64), used=gib(used_gib), loaded={m.name: m for m in models},
                    max_loaded=max_loaded, external_floor=floor)


def need(name: str, size_gib: float) -> Estimate:
    return Estimate(model=name, context=8192, weights=gib(size_gib), kv=0, overhead=0, source="formula")


def run(snapshot, est, settings=S, last_used=None, in_flight=()):
    return decide(snapshot, est, settings, last_used=last_used or {}, in_flight=set(in_flight), now=NOW)


def test_fits_goes_and_warns_past_warn_line():
    assert run(snap(10), need("a", 10)).action == GO
    crowded = run(snap(40), need("a", 15))
    assert (crowded.action, crowded.warn) == (GO, True)


def test_resident_model_needs_nothing_even_near_the_hold_line():
    # The Spark's main model sits at 59.2 of 62.5 GiB once loaded (RESEARCH.md §6).
    decision = run(snap(59.2, model("main", 52.3)), need("main", 90))
    assert (decision.action, decision.need, decision.warn) == (GO, 0, False)


def test_single_slot_ollama_swaps_by_itself():
    assert run(snap(55, model("big", 50), max_loaded=1), need("other", 40)).action == GO


def test_evicts_least_recently_used_idle_models_only():
    loaded = (model("old", 10), model("newer", 10), model("pinned", 10), model("busy", 10), model("fresh", 10))
    settings = Settings(pinned_models=("pinned",))
    last_used = {"old": NOW - 500, "newer": NOW - 100, "pinned": NOW - 900, "busy": NOW - 900,
                 "fresh": NOW - 5}
    decision = run(snap(60, *loaded), need("target", 15), settings, last_used, in_flight={"busy"})
    assert (decision.action, decision.evict) == (EVICT, ("old", "newer"))


def test_waits_when_nothing_is_evictable_yet():
    decision = run(snap(55, model("busy", 40)), need("target", 15), in_flight={"busy"})
    assert decision.action == WAIT


def test_waits_for_transient_external_only_when_another_model_blocks_eviction():
    # Empty board: even a big external spike passes (nothing to unload). With a busy resident,
    # we still wait for that model to finish rather than spilling past it.
    assert run(snap(53, floor_gib=3), need("target", 20)).action == PASS
    assert run(snap(53, model("busy", 40), floor_gib=3), need("target", 20),
               in_flight={"busy"}).action == WAIT
    assert run(snap(3), need("target", 20)).action == GO


def test_passes_when_no_other_models_even_if_estimate_misses():
    # Empty board + desktop share: a sub-GiB estimate miss must not hold for queue_timeout_s.
    decision = run(snap(3.2, floor_gib=0.5), need("main", 60))
    assert decision.action == PASS and decision.warn is True


def test_combined_budget_uses_gpu_free_plus_ram_free_not_sum_of_totals():
    # Spark-shaped: 62.5 GiB GPU almost full, 64 GiB RAM half free — combined still has room.
    s = Settings(budget_pool="combined", reserve_gb=2.0, hold_pct=95)
    gpu = snap(59.0, floor_gib=3.0)  # ~3.5 GiB free under hold line after reserve
    # Monkey the total to 62.5-like 64 for easy math (Pool default).
    decision = decide(gpu, need("main", 20), s, last_used={}, in_flight=set(), now=NOW,
                      ram_total=gib(64), ram_used=gib(20))
    assert decision.pool == "combined"
    assert decision.action == GO
    assert decision.partial is True  # needs more than GPU free alone
    # Summing totals (~128) would always look huge; free room is what matters.
    assert decision.free < gib(80)


def test_system_ram_budget_ignores_gpu_occupancy():
    s = Settings(budget_pool="system_ram", reserve_gb=2.0, hold_pct=95)
    # GPU nearly full, but system RAM has room — system_ram pool should GO.
    decision = decide(snap(60, floor_gib=3), need("main", 10), s, last_used={}, in_flight=set(),
                      now=NOW, ram_total=gib(64), ram_used=gib(20))
    assert decision.pool == "system_ram" and decision.action == GO


def test_wait_mode_never_evicts():
    decision = run(snap(55, model("idle", 40)), need("target", 15), Settings(on_overflow="wait"))
    assert decision.action == WAIT


def test_warn_only_passes_with_warning():
    decision = run(snap(55, floor_gib=3), need("target", 15), Settings(on_overflow="warn_only"))
    assert (decision.action, decision.warn) == (PASS, True)


def test_model_bigger_than_the_room_goes_partly_to_cpu():
    huge = run(snap(3), need("huge", 90))
    assert (huge.action, huge.partial, huge.need) == (GO, True, gib(60.8) - gib(3))
    pinned = run(snap(45, model("main", 40)), need("mid", 25), Settings(pinned_models=("main",)))
    assert (pinned.action, pinned.partial) == (GO, True)


def test_single_slot_swap_of_a_huge_model_needs_no_wait():
    decision = run(snap(15, model("aux", 12), max_loaded=1), need("main", 90))
    assert (decision.action, decision.partial) == (GO, True)
