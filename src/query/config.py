"""Query agent configuration.

Reads from the same .env that Amit's compilation pipeline uses.
Falls back to sensible defaults for local development.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

WIKI_DIR = Path(os.getenv("WIKI_DIR", "wiki"))
RAW_DIR = Path(os.getenv("RAW_DIR", "raw"))
CHROMA_PERSIST_DIR = Path(os.getenv("CHROMA_PERSIST_DIR", ".chroma_index"))

LLM_MODEL = os.getenv("QUERY_LLM_MODEL", os.getenv("LLM_MODEL", ""))
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

WIKI_CATEGORIES = ["topics", "systems", "policies", "decisions", "people"]

DOMAIN_HUBS = [
    "buyer-experience",
    "seller-experience",
    "marketplace-discovery",
    "platform-reliability",
    "trust-safety",
    "ai-automation",
    "growth-monetization",
    "engineering-productivity",
]

MAX_RETRY_ATTEMPTS = 3
CHROMA_COLLECTION_NAME = "duckie_wiki"
SEARCH_TOP_K = 5


def create_llm(temperature: float = 0, model_override: str | None = None) -> "ChatOpenAI":
    """Create a ChatOpenAI instance that works with Amit's LiteLLM proxy or direct API.

    If LITELLM_BASE_URL is set, routes through the proxy.
    Otherwise uses the API key directly.
    Centralizes model creation so all files use the same config.
    """
    from langchain_openai import ChatOpenAI

    model = model_override or LLM_MODEL
    kwargs: dict = {
        "model": model,
        "temperature": temperature,
    }

    if LITELLM_BASE_URL:
        kwargs["base_url"] = LITELLM_BASE_URL
        if OPENAI_API_KEY:
            kwargs["api_key"] = OPENAI_API_KEY
    elif OPENAI_API_KEY:
        kwargs["api_key"] = OPENAI_API_KEY

    return ChatOpenAI(**kwargs)
