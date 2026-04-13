"""LangChain callback that captures prompt-caching stats per batch.

Hooks `on_llm_end` on every LLM turn the agent makes. Extracts
`usage_metadata.input_token_details.cache_read` (LangChain's rename of
OpenAI's `prompt_tokens_details.cached_tokens`) plus basic prompt /
completion token counts. Consumers call `snapshot()` after a batch to get a
single dict suitable for logging.

Context: z-ai/glm-4.6 supports OpenRouter's prompt cache; z-ai/glm-5 and
z-ai/glm-5.1 do NOT (verified 2026-04-13, see
`docs/reviews/prompt-caching-20260413.md`). Without per-batch stats we'd
never notice when a model silently stops caching. With them, every batch
log includes the cache-hit rate and the dipstick report aggregates across
batches.
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


class CacheStatsCallback(BaseCallbackHandler):
    """Accumulate token usage across every LLM call in one agent run."""

    def __init__(self) -> None:
        self.turns = 0
        self.prompt_tokens = 0
        self.cached_tokens = 0
        self.completion_tokens = 0

    def on_llm_end(self, response: Any, **_kwargs: Any) -> None:
        for gen_list in getattr(response, "generations", []) or []:
            for g in gen_list:
                msg = getattr(g, "message", None)
                if msg is None:
                    continue
                u = getattr(msg, "usage_metadata", None)
                if not u:
                    continue
                self.turns += 1
                self.prompt_tokens += int(u.get("input_tokens") or 0)
                self.completion_tokens += int(u.get("output_tokens") or 0)
                details = u.get("input_token_details") or {}
                self.cached_tokens += int(details.get("cache_read") or 0)

    def snapshot(self) -> dict[str, Any]:
        pct = (self.cached_tokens / self.prompt_tokens * 100.0) if self.prompt_tokens else 0.0
        return {
            "turns": self.turns,
            "prompt_tokens": self.prompt_tokens,
            "cached_tokens": self.cached_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_pct": round(pct, 1),
        }
