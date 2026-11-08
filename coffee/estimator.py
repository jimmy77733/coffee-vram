"""How much device memory a model will take once Ollama loads it at a given context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .snapshot import LoadedModel

# Bytes per cached element; q8_0/q4_0 carry one f16 scale per 32-element block.
KV_BYTES = {"f16": 2.0, "q8_0": 34 / 32, "q4_0": 18 / 32}

# Ollama's own default when neither the server nor the model says otherwise.
DEFAULT_CONTEXT = 4096


@dataclass(frozen=True)
class Estimate:
    model: str
    context: int
    weights: int
    kv: int
    overhead: int
    source: str  # "observed" | "formula" | "weights-only"

    @property
    def total(self) -> int:
        return self.weights + self.kv + self.overhead


def _first_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        ints = [v for v in value if isinstance(v, int) and not isinstance(v, bool)]
        return max(ints) if ints else None
    return None


def kv_bytes_per_token(model_info: Mapping[str, Any], kv_type: str = "f16") -> int | None:
    """KV cache bytes one token of context costs, or None when ``model_info`` lacks the shape.

    Hybrid models (Qwen3.5/Qwen-Next linear attention) cache only every
    ``full_attention_interval``-th layer; per-layer ``head_count_kv`` arrays mark cache-free
    layers with 0. Counting every layer overestimates those models about 4x.
    """
    arch = model_info.get("general.architecture")
    if not arch:
        return None

    def key(name: str) -> Any:
        return model_info.get(f"{arch}.{name}")

    layers = _first_int(key("block_count"))
    heads = _first_int(key("attention.head_count"))
    raw_kv_heads = key("attention.head_count_kv")
    embedding = _first_int(key("embedding_length"))
    key_len = _first_int(key("attention.key_length"))
    if key_len is None and embedding and heads:
        key_len = embedding // heads
    value_len = _first_int(key("attention.value_length")) or key_len
    if not layers or not key_len or not value_len:
        return None

    if isinstance(raw_kv_heads, list):
        head_layers = sum(h for h in raw_kv_heads if isinstance(h, int) and h > 0)
    else:
        kv_heads = _first_int(raw_kv_heads) or heads
        if not kv_heads:
            return None
        interval = _first_int(key("full_attention_interval"))
        cached_layers = layers // interval if interval and interval > 1 else layers
        head_layers = kv_heads * cached_layers
    return int(head_layers * (key_len + value_len) * KV_BYTES.get(kv_type, KV_BYTES["f16"]))


def effective_context(*, observed: int | None = None, server_default: int | None = None,
                      model_max: int | None = None) -> int:
    """The context Ollama will actually load with.

    Ollama's OpenAI-compatible ``/v1`` ignores a request's ``num_ctx`` (RESEARCH.md §3), so the
    request is no guide: the last context seen in ``/api/ps`` wins, then OLLAMA_CONTEXT_LENGTH,
    capped by what the model supports.
    """
    for value in (observed, server_default):
        if value and value > 0:
            return min(value, model_max) if model_max else value
    return min(DEFAULT_CONTEXT, model_max) if model_max else DEFAULT_CONTEXT


def estimate(model: str, *, file_size: int, model_info: Mapping[str, Any], context: int,
             kv_type: str = "f16", parallel: int = 1, overhead_pct: int = 10,
             observed: LoadedModel | None = None) -> Estimate:
    """Device bytes ``model`` needs at ``context``; a prior ``/api/ps`` reading beats the formula."""
    per_token = kv_bytes_per_token(model_info, kv_type)
    parallel = max(1, parallel)
    if observed is not None and observed.size > 0:
        base, source = observed.size, "observed"
        grow = context - (observed.context_length or context)
        kv = max(0, grow) * parallel * per_token if per_token else 0
    else:
        base = max(0, file_size)
        kv = context * parallel * per_token if per_token else 0
        source = "formula" if per_token else "weights-only"
    overhead = int((base + kv) * max(0, overhead_pct) / 100)
    return Estimate(model=model, context=context, weights=base, kv=kv, overhead=overhead, source=source)
