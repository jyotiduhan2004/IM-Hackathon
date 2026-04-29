"""Wiki page IO + extraction helpers.

Pure-IO and pure-text utilities for reading, summarizing, and writing
wiki pages. Extracted from the legacy `src/compile/compiler.py` in
Phase 1A.1 of the src/wiki/-vs-src/agent/ refactor; `src/compile/` was
fully retired in Phase 2B.

Nothing in this module imports from `src.agent.*` or `src.coordinator.*`
— the directionality test added in Phase 3 will enforce that.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from src.utils import extract_body as _extract_body
from src.utils import extract_frontmatter as _extract_frontmatter

# Source category list for wiki page lookup. Imported lazily inside
# `_find_page_by_slug` to avoid a circular dependency through
# `src.wiki.categories`.


def _atomic_write_text(path: Path, content: str) -> None:
    """Write `content` to `path` atomically (temp file + Path.replace).

    Prevents truncated/partial landing pages if the process dies mid-write
    or two coordinator entrypoints race (e.g. compile_all + watch_and_compile
    running at the same time). `Path.replace` is atomic on POSIX and Windows
    when src and dst are on the same filesystem — tempfile creates in the
    same directory, so the guarantee holds.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _page_summary(md_file: Path) -> dict[str, Any] | None:
    """Extract reader-facing summary fields from a wiki page.

    Returns None for pages with broken frontmatter so the caller can skip
    them. The `is_stub` flag is True when the page has no provenance
    evidence — neither per-message `sources:` nor post-Phase-A
    `source_threads:` — used to hide ghost person/system pages from the
    landing listings.
    """
    try:
        content = md_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm = _extract_frontmatter(content)
    if not fm or "title" not in fm:
        return None
    body = _extract_body(content)
    sources = fm.get("sources") or []
    # Post-Phase-A pages carry provenance as `source_threads:` — the agent
    # no longer writes `sources:`. Either field counts as "not a stub".
    source_threads = fm.get("source_threads") or []
    sources_list = sources if isinstance(sources, list) else []
    threads_list = source_threads if isinstance(source_threads, list) else []
    has_provenance = bool(sources_list) or bool(threads_list)
    last_compiled = str(fm.get("last_compiled", "") or "")
    # `last_compiled: stub` and `stub-backfilled` are the canonical stub
    # markers used by `scripts/backfill_stubs.py` — keep this rule in sync
    # with `scripts/backfill_stubs.py::_is_stub`.
    is_stub_marker = last_compiled in ("stub", "stub-backfilled")
    return {
        "slug": md_file.stem,
        "title": str(fm.get("title", md_file.stem)),
        "status": str(fm.get("status", "active")),
        "last_compiled": last_compiled,
        "summary": _first_paragraph(body),
        "sources_count": len(sources_list) + len(threads_list),
        "is_stub": not has_provenance or is_stub_marker,
    }


def _first_paragraph(body: str) -> str:
    """Return the first non-empty, non-heading paragraph from a page body.

    Used as the one-line summary next to each listing entry. Truncated to
    a single line (280 chars) so long intros don't blow up the listing.
    """
    current: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if current:
                break
            continue
        if not stripped:
            if current:
                break
            continue
        current.append(stripped)
    if not current:
        return ""
    para = " ".join(current)
    return para[:277] + "..." if len(para) > 280 else para


def _iter_content_pages(wiki_path: Path) -> list[Path]:
    """Return every topic + system page. Used by multiple generators.

    Skips the section `index.md` files — those are generated listings, not
    source pages for domain/glossary/decision rollups.
    """
    pages: list[Path] = []
    for category in ("topics", "systems"):
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            if md_file.name == "index.md":
                continue
            pages.append(md_file)
    return pages


def _read_page(md_file: Path) -> tuple[dict[str, Any], str] | None:
    """Return (frontmatter, body) for a page, or None on corrupt/unreadable.

    Single read per file — callers avoid duplicate disk hits for pages that
    drive several generators in the same pass.
    """
    try:
        content = md_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm = _extract_frontmatter(content)
    if not fm:
        return None
    body = _extract_body(content)
    return fm, body


_REFERENCED_BY_RE = re.compile(r"(## Referenced by\n)(.*?)(?=\n## |\n<!--|\Z)", re.DOTALL)


def _replace_referenced_by(body: str, refs: list[str]) -> str:
    """Rewrite the "## Referenced by" block in `body` with `refs`.

    Appends the block if missing. Keeps the surrounding body untouched so
    a human-authored decision page retains its free-form content above.
    """
    block_lines = ["\n"]
    block_lines.extend(f"- [[{r}]]\n" for r in refs)
    block_body = "".join(block_lines)

    if _REFERENCED_BY_RE.search(body):
        return _REFERENCED_BY_RE.sub(lambda m: m.group(1) + block_body, body, count=1)

    separator = "\n\n" if body and not body.endswith("\n\n") else ""
    return f"{body}{separator}## Referenced by\n{block_body}"


def _find_page_by_slug(slug: str, wiki_dir: str = "wiki") -> Path | None:
    """Locate `wiki/<category>/<slug>.md` across known category dirs.

    Returns the first match or None. Keep this private — the public tools
    only expose the summary/patch result, never the filesystem path, so
    callers can't treat the path as an agent-visible identifier.
    """
    from src.wiki.categories import WIKI_CATEGORIES

    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return None
    for category in WIKI_CATEGORIES:
        candidate = wiki_path / category / f"{slug}.md"
        if candidate.is_file():
            return candidate
    return None


def _first_paragraph_capped(body: str, cap: int = 200) -> str:
    """Return the first non-heading paragraph from `body`, hard-capped at `cap` chars.

    Mirrors the convention in `_first_paragraph` above but with a tighter
    cap so `get_page_summary` never floods the agent context. Headings
    (`#`), blank lines, and blockquotes are skipped when they lead.
    """
    current: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if current:
                break
            continue
        if not stripped:
            if current:
                break
            continue
        current.append(stripped)
    if not current:
        return ""
    para = " ".join(current)
    if len(para) > cap:
        return para[: cap - 3].rstrip() + "..."
    return para


def _extract_h2_headings(body: str) -> list[str]:
    """Return the text of every `## H2` heading in document order."""
    headings: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            headings.append(line[3:].strip())
    return headings


_TLDR_MAX_CHARS = 400


def _extract_tldr(body: str) -> str | None:
    """Return a one-glance summary for the page.

    Prefers the explicit `## TL;DR` (or `## TLDR`) H2 when present —
    case-insensitive — collecting everything between that heading and
    the next `##` (or EOF). Falls back to the lead paragraph (the prose
    between frontmatter end and the first H2) when no TL;DR section
    exists, per the 2026-04-28 prompt-review decision (Q7.2): "lead
    paragraph IS the summary". The runtime decouples the wiki authoring
    convention from the tool's return shape — pages no longer need a
    `## TL;DR` heading, but legacy pages that have one keep working.

    Returns None only when the page has neither a TL;DR section nor a
    lead paragraph (empty body).

    Capped at `_TLDR_MAX_CHARS` chars with an ellipsis suffix when
    longer — a runaway summary must not blow the concise token budget.
    Full content stays accessible via `read_file`.
    """
    in_tldr = False
    collected: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            if not in_tldr and heading in ("tl;dr", "tldr"):
                in_tldr = True
                continue
            # Non-TL;DR H2: if we're inside the TL;DR section, we're done;
            # otherwise we haven't entered yet and keep scanning.
            if in_tldr:
                break
            continue
        if in_tldr:
            collected.append(line)
    text = "\n".join(collected).strip()
    if not text:
        # No `## TL;DR` H2 — fall back to the lead paragraph. The
        # 400-char cap mirrors the explicit-TL;DR branch so callers
        # see a single bounded contract regardless of source.
        text = _first_paragraph_capped(body, cap=_TLDR_MAX_CHARS)
        if not text:
            return None
        return text
    if len(text) > _TLDR_MAX_CHARS:
        # Leave room for the ellipsis marker. Preserve trailing
        # whitespace before the truncation point — `rstrip()` + `…`
        # reads more naturally than a mid-word cut.
        return text[: _TLDR_MAX_CHARS - 1].rstrip() + "…"
    return text
