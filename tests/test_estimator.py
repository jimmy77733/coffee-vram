from __future__ import annotations

from coffee_vram_plugin.coffee.estimator import (DEFAULT_CONTEXT, effective_context, estimate,
                                            kv_bytes_per_token)
from coffee_vram_plugin.coffee.snapshot import LoadedModel

# qwen3.5:9b Q4_K_M as measured on the Spark (RESEARCH.md §1, §4).
QWEN35_INFO = {
    "general.architecture": "qwen35", "qwen35.block_count": 32, "qwen35.attention.head_count": 16,
    "qwen35.attention.head_count_kv": 4, "qwen35.attention.key_length": 256,
    "qwen35.attention.value_length": 256, "qwen35.embedding_length": 4096,
    "qwen35.full_attention_interval": 4, "qwen35.context_length": 262144,
}
QWEN35_FILE = 6_550_825_550
QWEN35_OLLAMA_SIZE = {8192: 5_862_210_928, 131072: 10_626_982_215}
QWEN35_NVML_DELTA_131072 = 11_689_459_712

LLAMA3_8B_INFO = {
    "general.architecture": "llama", "llama.block_count": 32, "llama.attention.head_count": 32,
    "llama.attention.head_count_kv": 8, "llama.embedding_length": 4096,
}


def test_hybrid_attention_caches_only_full_attention_layers():
    assert kv_bytes_per_token(QWEN35_INFO) == 8 * 4 * (256 + 256) * 2


def test_dense_gqa_uses_kv_heads_and_derived_head_dim():
    assert kv_bytes_per_token(LLAMA3_8B_INFO) == 2 * 32 * 8 * 128 * 2


def test_per_layer_kv_head_arrays_skip_cache_free_layers():
    info = {"general.architecture": "x", "x.block_count": 4, "x.attention.head_count": 8,
            "x.attention.head_count_kv": [2, 0, 2, 0], "x.attention.key_length": 64}
    assert kv_bytes_per_token(info) == 4 * (64 + 64) * 2


def test_quantized_kv_cache_is_smaller():
    f16, q8, q4 = (kv_bytes_per_token(LLAMA3_8B_INFO, t) for t in ("f16", "q8_0", "q4_0"))
    assert f16 > q8 > q4 > 0


def test_formula_matches_spark_measurement_and_never_undershoots_ollama():
    est = estimate("qwen3.5:9b", file_size=QWEN35_FILE, model_info=QWEN35_INFO, context=131072)
    assert est.source == "formula"
    assert abs(est.total - QWEN35_NVML_DELTA_131072) / QWEN35_NVML_DELTA_131072 < 0.10
    for context, size in QWEN35_OLLAMA_SIZE.items():
        assert estimate("qwen3.5:9b", file_size=QWEN35_FILE, model_info=QWEN35_INFO,
                        context=context).total >= size


def test_unknown_shape_falls_back_to_weights():
    est = estimate("mystery", file_size=1000, model_info={}, context=8192, overhead_pct=0)
    assert (est.source, est.total) == ("weights-only", 1000)


def test_observed_size_beats_formula_and_grows_with_context():
    seen = LoadedModel("qwen3.5:9b", size=QWEN35_OLLAMA_SIZE[8192], size_vram=QWEN35_OLLAMA_SIZE[8192],
                       context_length=8192)
    same = estimate("qwen3.5:9b", file_size=QWEN35_FILE, model_info=QWEN35_INFO, context=8192,
                    overhead_pct=0, observed=seen)
    assert (same.source, same.total) == ("observed", QWEN35_OLLAMA_SIZE[8192])
    bigger = estimate("qwen3.5:9b", file_size=QWEN35_FILE, model_info=QWEN35_INFO, context=16384,
                      overhead_pct=0, observed=seen)
    assert bigger.total == QWEN35_OLLAMA_SIZE[8192] + 8192 * kv_bytes_per_token(QWEN35_INFO)


def test_effective_context_prefers_observed_then_server_capped_by_model():
    assert effective_context(observed=8192, server_default=131072) == 8192
    assert effective_context(server_default=131072, model_max=32768) == 32768
    assert effective_context() == DEFAULT_CONTEXT
