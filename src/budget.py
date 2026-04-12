"""LiteLLM proxy budget check — surfaces remaining budget for visibility.

The Intermesh LiteLLM proxy exposes /key/info which returns:
- `spend`: cumulative $ on this key
- `max_budget`: cap for the key (null = unlimited)
- `budget_reset_at`: when spend resets
- other metadata

We call this before and after compile runs so we can see cost trajectories
and abort if we're about to exceed the budget.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

from src.config import settings

logger = structlog.get_logger(__name__)


@dataclass
class BudgetSnapshot:
    key_alias: str
    spend: float
    max_budget: float | None
    remaining: float | None
    budget_reset_at: str | None

    @property
    def percent_used(self) -> float | None:
        if self.max_budget and self.max_budget > 0:
            return round((self.spend / self.max_budget) * 100, 2)
        return None

    def __str__(self) -> str:
        parts = [f"${self.spend:.4f} spent"]
        if self.max_budget:
            parts.append(f"of ${self.max_budget:.2f}")
            if self.remaining is not None:
                parts.append(f"(${self.remaining:.2f} left)")
        if self.percent_used is not None:
            parts.append(f"[{self.percent_used}%]")
        return " ".join(parts) + f" — key={self.key_alias}"


def fetch_budget() -> BudgetSnapshot | None:
    """Query the LiteLLM proxy for current key usage. Returns None on error."""
    if not settings.litellm_base_url or not settings.openai_api_key:
        return None

    url = settings.litellm_base_url.rstrip("/") + "/key/info"
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("budget check failed", error=str(e))
        return None

    info = data.get("info", {})
    spend = float(info.get("spend") or 0.0)
    max_b = info.get("max_budget")
    max_budget = float(max_b) if max_b is not None else None
    remaining = (max_budget - spend) if max_budget is not None else None

    return BudgetSnapshot(
        key_alias=info.get("key_alias", "?"),
        spend=spend,
        max_budget=max_budget,
        remaining=remaining,
        budget_reset_at=info.get("budget_reset_at"),
    )
