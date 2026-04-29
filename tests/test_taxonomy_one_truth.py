"""Taxonomy must be one truth across prompt, browse tool, and category registry.

Codex audit P2 (2026-04-17): three forked taxonomies shipped — the
system prompt said "4 visible + 2 lazy, no timelines/conflicts",
`list_wiki_pages` browsed `(topics, entities, people, systems,
policies)`, and `categories.py::WIKI_CATEGORIES` still carried
entities + timelines + conflicts + domains + decisions. This pins
the canonical agent-visible list + asserts the prompt's page_types
stay aligned with it."""

from __future__ import annotations

from src.agent.prompts import COMPILER_SYSTEM_PROMPT
from src.agent.tools.sources import list_wiki_pages
from src.wiki.categories import AGENT_VISIBLE_CATEGORIES
from src.wiki.categories import WIKI_CATEGORIES


def test_agent_visible_categories_is_a_strict_subset_of_disk_categories() -> None:
    assert set(AGENT_VISIBLE_CATEGORIES).issubset(set(WIKI_CATEGORIES))


def test_agent_visible_categories_matches_prompt_page_types() -> None:
    """The five agent-browseable categories are the four visible page
    types in the prompt + the lazy `people` directory. Glossary is a
    single file (not a directory) so it's intentionally absent here."""
    assert set(AGENT_VISIBLE_CATEGORIES) == {
        "topics",
        "systems",
        "policies",
        "decisions",
        "people",
    }


def test_list_wiki_pages_browses_agent_visible_categories(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Sanity: the browse tool exposes the same 5 categories the
    prompt teaches, not a different hardcoded list. Using tmp_path
    (empty wiki) checks the tool returns the right keys even on
    empty scan.

    v10-U6: the category-keyed shape lives in the `detailed` format.
    Concise returns `{"pages": [...]}` — a flat inventory that drops
    the category grouping to save tokens."""
    result = list_wiki_pages.invoke({"response_format": "detailed", "wiki_dir": str(tmp_path)})
    assert set(result.keys()) == set(AGENT_VISIBLE_CATEGORIES)


def test_retired_categories_hidden_from_agent_browse() -> None:
    """Prompt says 'no timelines / conflicts' and domains are
    coordinator-generated. None of those should appear in the agent's
    browse surface, even though they may still exist on disk (see
    WIKI_CATEGORIES)."""
    assert "timelines" not in AGENT_VISIBLE_CATEGORIES
    assert "conflicts" not in AGENT_VISIBLE_CATEGORIES
    assert "domains" not in AGENT_VISIBLE_CATEGORIES
    # `entities` is being migrated to `people`; agent only sees the
    # destination, not the legacy source.
    assert "entities" not in AGENT_VISIBLE_CATEGORIES


def test_prompt_page_types_align_with_agent_categories() -> None:
    """The prompt's <page_types> block lists topic/system/policy +
    lazy decision/person. The browse tool exposes the pluralised
    directory names for those same concepts. Either direction drifting
    would confuse the agent."""
    # Each agent-visible category name has a singular form that the
    # prompt describes.
    start = COMPILER_SYSTEM_PROMPT.find("<page_types>")
    end = COMPILER_SYSTEM_PROMPT.find("</page_types>")
    assert start != -1 and end != -1
    block = COMPILER_SYSTEM_PROMPT[start:end]
    for plural in AGENT_VISIBLE_CATEGORIES:
        singular = plural.rstrip("s")
        # Allow `**topic**` / `**system**` bold-marked page-type name
        # OR the plural dir path `/wiki/topics/`. One of the two
        # conventions must show.
        assert f"**{singular}**" in block or f"/wiki/{plural}/" in block, (
            f"agent-visible category {plural!r} not taught in prompt <page_types>"
        )
