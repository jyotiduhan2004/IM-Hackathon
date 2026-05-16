"""Duckie Query Agent v3 — G-Brain-Inspired Skills Architecture.

Single LLM session with IndiaMART-specific skills loaded on demand.
No sub-agents. Skills inject knowledge the LLM doesn't have.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from src.query.config import create_llm
from src.query.agents._tools import ALL_TOOLS, clear_page_cache

REPO_ROOT = Path(__file__).resolve().parents[2]
GAPS_LOG = REPO_ROOT / "logs" / "query_gaps.jsonl"
ENTITY_LOG = REPO_ROOT / "logs" / "entity_mentions.jsonl"

SYSTEM_PROMPT = """\
You are Duckie, IndiaMART's internal knowledge assistant. You navigate a wiki of 2,300+ pages \
compiled from internal launch emails (Jan-mid Feb 2026) covering products, systems, people, \
and initiatives across 8 business domains.

## Your Tools (10)
- **Search:** qmd_search(query), grep(query), keyword_search(query)
- **Read:** cat(slug), head(slug), ls(path)
- **Filter:** find(domain, page_type)
- **Graph:** related_pages(slug) — follow wikilink connections between pages
- **Knowledge:** load_skill(name) — inject IndiaMART-specific knowledge (see below)
- **Verify:** quality_check(question, answer, pages_read) — optional answer verification

## Skills (IndiaMART-specific knowledge — call load_skill when needed)
| Skill | When to load |
|-------|-------------|
| search-strategy | You need to search effectively (acronyms, search tips, QMD vs grep) |
| wiki-navigation | You need to understand wiki structure, domains, page types, person slugs |
| qmd-usage | You're about to use qmd_search (QMD is a hybrid search engine the LLM doesn't know) |
| indiamart-context | User needs organizational context about IndiaMART, or info is missing |

## How to Answer
1. If you need IndiaMART-specific knowledge, call load_skill() first
2. Search with qmd_search() for comprehensive results, grep() for semantic, keyword_search() for exact titles
3. Read pages with head() to scan, cat() for detail
4. Use related_pages() to discover connected pages via wikilinks
5. Answer ONLY from wiki facts. Never invent.
6. Cite sources: Sources: [[slug-1]], [[slug-2]]. The system will auto-attach the most relevant source email subjects.
7. If not found: be honest, state coverage window (Jan-mid Feb 2026), suggest related pages you did find

## Important
- The wiki covers Jan-mid Feb 2026 IndiaMART launch emails
- Use head() to scan many pages cheaply (8x fewer tokens than cat)
- For domain questions, read the domain hub first: cat('domains/ai-automation')
- Person slugs are email-based: amit-agarwal-indiamart-com
- Be professional but approachable — like a helpful senior colleague"""


def run_main_agent(
    question: str,
    chat_history: list[dict] | None = None,
    max_iterations: int = 50,
    tool_callback=None,
) -> dict:
    clear_page_cache()

    llm = create_llm()
    llm_with_tools = llm.bind_tools(ALL_TOOLS)

    history_text = ""
    if chat_history:
        history_lines = []
        for entry in chat_history[-10:]:
            role = entry.get("role", "user")
            content = entry.get("content", "")
            if len(content) > 1000:
                content = content[:1000] + "..."
            history_lines.append(f"{role.upper()}: {content}")
        history_text = "\n".join(history_lines)

    system = SYSTEM_PROMPT
    if history_text:
        system += f"\n\n## Previous conversation:\n{history_text}"

    messages = [
        SystemMessage(content=system),
        HumanMessage(content=question),
    ]

    tool_calls_log = []
    pages_read = set()
    tool_map = {t.name: t for t in ALL_TOOLS}

    def _execute_tool_calls(response):
        for tc in response.tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]
            tool_fn = tool_map.get(tool_name)
            if tool_fn:
                tool_result = tool_fn.invoke(tool_args)
            else:
                tool_result = f"Unknown tool: {tool_name}"
            messages.append(ToolMessage(content=str(tool_result), tool_call_id=tc["id"]))
            tool_calls_log.append({
                "tool": tool_name,
                "input": str(tool_args)[:200],
                "output": str(tool_result)[:300],
            })
            if tool_callback:
                tool_callback(tool_name, tool_args, str(tool_result)[:200])
            if tool_name in ("cat", "head"):
                slug = tool_args.get("slug", "")
                if slug:
                    pages_read.add(slug)

    for _step in range(max_iterations):
        response = llm_with_tools.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            break
        _execute_tool_calls(response)

    answer = response.content if response.content else ""

    # Fallback: auto-synthesize from cached pages if answer is bad
    if not answer or answer.startswith("Let me") or answer.startswith("I'll"):
        from src.query.agents._tools import _page_cache
        if pages_read:
            from src.query.indexer import parse_frontmatter
            summaries = []
            for slug in sorted(pages_read):
                content = _page_cache.get(slug, "")
                if content:
                    fm, body = parse_frontmatter(content)
                    title = fm.get("title", slug)
                    lead_lines = [l for l in body.strip().split("\n") if l.strip() and not l.startswith("#")]
                    lead = lead_lines[0][:200] if lead_lines else ""
                    domain = fm.get("domain", "")
                    domain_str = f" [{domain}]" if domain else ""
                    summaries.append(f"- **[[{slug}]]** — {title}{domain_str}: {lead}")
            answer = (
                f"Here's what I found across {len(pages_read)} relevant pages:\n\n"
                + "\n".join(summaries)
                + "\n\nAsk about any specific page for more details."
            )
        else:
            from src.query.tools.search import search_wiki
            try:
                related = search_wiki(question, top_k=5)
                related_links = [f"[[{r['slug']}]] - {r['title']}" for r in related]
            except Exception:
                related_links = []
            if related_links:
                answer = (
                    f"I couldn't find a specific page about this in the wiki.\n\n"
                    f"**Related pages you might find helpful:**\n"
                    + "\n".join(f"- {link}" for link in related_links)
                    + "\n\nThe wiki covers Jan-mid Feb 2026 IndiaMART emails."
                )
            else:
                answer = (
                    "I couldn't find any relevant wiki pages for this question.\n\n"
                    "The wiki covers Jan-mid Feb 2026 IndiaMART launch emails. "
                    "Try different keywords or ask about a specific product/system name."
                )

    citations = list(pages_read)

    # Extract raw email references and resolve subjects
    from src.query.agents._tools import _page_cache
    from src.query.config import RAW_DIR
    email_refs = set()
    raw_paths = {}  # msg-hash -> raw file path (from footnote defs)
    for slug in pages_read:
        content = _page_cache.get(slug, "")
        for match in re.finditer(r'\[\^(msg-[a-f0-9]+)\]', content):
            email_refs.add(match.group(1))
        for match in re.finditer(r'\[\^(msg-[a-f0-9]+)\]:\s*`([^`]+)`', content):
            raw_paths[match.group(1)] = match.group(2)

    email_sources = _resolve_email_subjects(email_refs, raw_paths, RAW_DIR)

    # Silent gap logging
    _log_gaps(question, answer, citations, tool_calls_log)

    return {
        "answer": answer,
        "citations": citations,
        "wiki_pages_read": citations,
        "email_refs": sorted(email_refs),
        "email_sources": email_sources,
        "tool_calls": tool_calls_log,
    }


def _resolve_email_subjects(
    email_refs: set[str],
    raw_paths: dict[str, str],
    raw_dir: Path,
) -> list[dict]:
    """Resolve msg-hash refs to email subjects by reading raw file frontmatter."""
    from src.query.indexer import parse_frontmatter

    results = []
    for ref in sorted(email_refs):
        msg_hash = ref.replace("msg-", "")
        raw_path = None

        if ref in raw_paths:
            candidate = raw_dir.parent / raw_paths[ref]
            if candidate.exists():
                raw_path = candidate

        if not raw_path:
            matches = list(raw_dir.glob(f"*_{msg_hash}.md"))
            if matches:
                raw_path = matches[0]

        if not raw_path or not raw_path.exists():
            results.append({"ref": ref, "subject": None, "from": None, "date": None})
            continue

        try:
            text = raw_path.read_text(encoding="utf-8", errors="replace")
            fm, _ = parse_frontmatter(text)
            results.append({
                "ref": ref,
                "subject": fm.get("subject", ""),
                "from": fm.get("from", ""),
                "date": fm.get("date", ""),
            })
        except Exception:
            results.append({"ref": ref, "subject": None, "from": None, "date": None})

    return results


def _log_gaps(question: str, answer: str, citations: list, tool_calls: list):
    """Silently log queries where info was missing (for future skillify)."""
    is_gap = (
        "couldn't find" in answer.lower()
        or "not in the wiki" in answer.lower()
        or "may not" in answer.lower()
        or not citations
    )
    if not is_gap:
        return
    try:
        GAPS_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "question": question,
            "pages_found": citations,
            "searches": [tc["input"] for tc in tool_calls if tc["tool"] in ("grep", "qmd_search", "keyword_search")],
            "answer_preview": answer[:200],
        }
        with open(GAPS_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
