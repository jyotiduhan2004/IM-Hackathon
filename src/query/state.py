"""Simplified query state — no router, no graders, just agent + result."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class QueryState(TypedDict):
    """LangGraph state for the query pipeline."""

    original_question: str
    chat_history: list[dict]

    answer: str
    citations: list[str]
    wiki_pages_read: list[str]

    tool_calls: Annotated[list[dict], operator.add]
