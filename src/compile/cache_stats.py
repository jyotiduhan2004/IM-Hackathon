"""Per-batch token + tool stats, built on LangChain's standard callback.

LangChain ships `UsageMetadataCallbackHandler` (langchain-core ≥0.3.49) that
aggregates `input_tokens` / `output_tokens` / `input_token_details.cache_read`
/ `cache_creation` per model name, exactly the breakdown we want for the
prompt-cache verification work and per-batch model A/B. We compose with it
instead of duplicating: `BatchStatsCallback` *is-a*
`UsageMetadataCallbackHandler` plus a tool-call counter (which the standard
handler doesn't track).

The `model` arg is just a label written into the snapshot for log lines —
the standard handler already aggregates by the LLM's reported model name,
which may differ from the configured pool entry (e.g. provider-prefix
stripping by litellm). Keeping both lets us see what we asked for vs what
we got.
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler


class BatchStatsCallback(UsageMetadataCallbackHandler):
    """Standard usage-metadata aggregation + a tool-call counter."""

    def __init__(self, model: str = "") -> None:
        super().__init__()
        self.requested_model = model
        self.tool_calls = 0
        self.turns = 0

    def on_tool_start(self, *_args: Any, **_kwargs: Any) -> None:
        self.tool_calls += 1

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        super().on_llm_end(response, **kwargs)
        for gen_list in getattr(response, "generations", []) or []:
            for g in gen_list:
                msg = getattr(g, "message", None)
                if msg is None or getattr(msg, "usage_metadata", None) is None:
                    continue
                self.turns += 1

    def snapshot(self) -> dict[str, Any]:
        """Flatten per-model usage_metadata into a per-batch summary line."""
        prompt = 0
        cached = 0
        cache_creation = 0
        completion = 0
        served_models: list[str] = []
        for model_name, meta in (self.usage_metadata or {}).items():
            served_models.append(model_name)
            prompt += int(meta.get("input_tokens") or 0)
            completion += int(meta.get("output_tokens") or 0)
            details = meta.get("input_token_details") or {}
            cached += int(details.get("cache_read") or 0)
            cache_creation += int(details.get("cache_creation") or 0)

        cache_pct = (cached / prompt * 100.0) if prompt else 0.0
        tools_per_turn = (self.tool_calls / self.turns) if self.turns else 0.0
        return {
            "requested_model": self.requested_model,
            "served_models": served_models,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tools_per_turn": round(tools_per_turn, 2),
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "cache_creation_tokens": cache_creation,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "cache_pct": round(cache_pct, 1),
        }


# Backward-compat alias so callers that imported the old name don't break.
CacheStatsCallback = BatchStatsCallback
