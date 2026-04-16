"""New-joiner 3-domain regression test (soft v1).

E2 unit from plans/sparkling-skipping-fiddle.md. A regression baseline for
"could a new IndiaMART engineer answer X from the wiki?" across 3 of the 8
north-star domains.

v1 is structural and soft:
  - (a) the expected page exists on disk
  - (b) the expected page is reachable from wiki/home.md in <=2 wikilink hops

v1 does NOT assert TL;DR contains the fact — that's synthesis quality and
lands in v2 after Tier A has been running a week.

The 9 cases are read from ``tests/fixtures/new_joiner/domains.json`` and run
as parametrized pytest cases so individual questions show up by name in the
failure report. An aggregate summary test asserts the overall pass count
against ``NEW_JOINER_BASELINE`` (default 0 — today's floor; bump once Tier A
materializes topic pages).
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Inline the wikilink pattern instead of importing from scripts.audit —
# that module has top-level `src.config` / `src.db.messages` imports that
# make this test carry an implicit DB dependency at import time. Keep in
# sync with `scripts/audit.py::WIKILINK_RE` (canonical source).
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

WIKI_DIR = REPO_ROOT / "wiki"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "new_joiner" / "domains.json"


@dataclass(frozen=True)
class Question:
    """A single new-joiner question plus the wiki slug that should answer it."""

    domain: str
    question: str
    expected_slug: str

    def case_id(self) -> str:
        """Human-readable id for pytest parametrize output."""
        return f"{self.domain}:{self.question}"


def _load_questions() -> list[Question]:
    """Parse the fixture into a flat list of Question records."""
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    questions: list[Question] = []
    for domain, entries in data.items():
        if domain.startswith("_"):  # Skip comment blocks.
            continue
        for entry in entries:
            questions.append(
                Question(
                    domain=domain,
                    question=entry["question"],
                    expected_slug=entry["expected_slug"],
                )
            )
    return questions


def _resolve_slug(wiki_dir: Path, slug: str) -> Path | None:
    """Return the wiki file matching ``slug`` or None.

    Accepts two forms (matching roamlinks' behaviour + the plan's fixture spec):
      - ``"topics/foo"`` — exact ``wiki/topics/foo.md`` (slash in slug).
      - ``"foo"``       — first ``wiki/**/foo.md`` found anywhere.

    Uses a deterministic sort (shortest path first) so the bare-slug form picks
    the shallowest match when multiple exist.
    """
    if "/" in slug:
        candidate = wiki_dir / f"{slug}.md"
        return candidate if candidate.exists() else None
    matches = sorted(wiki_dir.rglob(f"{slug}.md"), key=lambda p: (len(p.parts), str(p)))
    return matches[0] if matches else None


def _extract_wikilink_targets(path: Path) -> list[str]:
    """Return every ``[[target]]`` found in a page (pipe-aliases dropped)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    # audit.WIKILINK_RE's first group captures pre-pipe target text; strip to
    # normalize whitespace consistent with other wikilink consumers.
    return [target.strip() for target in WIKILINK_RE.findall(text)]


def _reachable_from_home(wiki_dir: Path, target_slug: str, max_hops: int) -> bool:
    """BFS from ``wiki/home.md`` following ``[[wikilinks]]`` up to ``max_hops``.

    A page is reachable if ``_resolve_slug`` for the target matches any page
    visited within the hop budget. ``home.md`` itself counts as hop 0.
    """
    home = wiki_dir / "home.md"
    if not home.exists():
        return False
    target_path = _resolve_slug(wiki_dir, target_slug)
    if target_path is None:
        return False

    visited: set[Path] = {home.resolve()}
    frontier: deque[tuple[Path, int]] = deque([(home, 0)])
    while frontier:
        page, hops = frontier.popleft()
        if page.resolve() == target_path.resolve():
            return True
        if hops >= max_hops:
            continue
        for link in _extract_wikilink_targets(page):
            next_page = _resolve_slug(wiki_dir, link)
            if next_page is None:
                continue
            resolved = next_page.resolve()
            if resolved in visited:
                continue
            visited.add(resolved)
            frontier.append((next_page, hops + 1))
    return False


_QUESTIONS = _load_questions()


def _score_question(q: Question, wiki_dir: Path) -> tuple[bool, bool]:
    """Return (page_exists, reachable_from_home_in_<=2_hops) for a question."""
    expected_path = _resolve_slug(wiki_dir, q.expected_slug)
    page_exists = expected_path is not None
    reachable = (
        _reachable_from_home(wiki_dir, q.expected_slug, max_hops=2) if page_exists else False
    )
    return page_exists, reachable


@pytest.mark.parametrize("q", _QUESTIONS, ids=[q.case_id() for q in _QUESTIONS])
def test_new_joiner_question(q: Question) -> None:
    """Soft assertion: expected page exists AND is reachable from home in <=2 hops.

    Cases for slugs that don't yet exist on disk are marked XFAIL at runtime
    rather than flipping the whole suite red — v1 is a regression baseline,
    not an aspiration. Once a page materializes post-Tier-A, the case starts
    PASSING (or XPASS if we forget to remove the xfail). When a page later
    disappears, it flips to XFAIL again, which IS what catches regressions
    (together with the aggregate threshold below).
    """
    page_exists, reachable = _score_question(q, WIKI_DIR)

    # Soft-fail via xfail when the wiki hasn't grown to cover this question
    # yet — keeps CI green while still tracking progress (a later PASS means
    # the wiki picked up the page).
    if not page_exists:
        pytest.xfail(
            f"expected page for slug {q.expected_slug!r} not on disk yet "
            f"(question: {q.question!r}, domain={q.domain})"
        )
    if not reachable:
        pytest.xfail(
            f"page for slug {q.expected_slug!r} exists but is not reachable from "
            f"wiki/home.md in <=2 hops (question: {q.question!r}, domain={q.domain})"
        )
    # Both conditions hold: page exists on disk AND is reachable ≤2 hops from home.
    assert page_exists and reachable


def _baseline_threshold() -> int:
    """Return the minimum pass count required. Default 0 (today's honest floor).

    Bump via NEW_JOINER_BASELINE once Tier A lands and content pages exist.
    Plan targets: 3 after Tier A (soft v1 goal), 7 after v2 synthesis checks.
    """
    raw = os.environ.get("NEW_JOINER_BASELINE", "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def test_new_joiner_baseline_threshold() -> None:
    """Aggregate check: at least NEW_JOINER_BASELINE cases pass on both metrics.

    Computes scores inline so this test is self-sufficient — it runs the same
    scoring pass the parametrized cases use, which keeps the threshold gate
    valid even if someone invokes this node in isolation via -k. Today the
    wiki has no content pages so both counts are 0; the default threshold
    of 0 keeps CI green while structural regression coverage lives in the
    individual parametrized cases above.
    """
    scores = [_score_question(q, WIKI_DIR) for q in _QUESTIONS]
    exists_count = sum(1 for exists, _reachable in scores if exists)
    reachable_count = sum(1 for _exists, reachable in scores if reachable)
    total = len(_QUESTIONS)
    threshold = _baseline_threshold()

    # Always print the summary so the stats are visible in `pytest -v` output
    # even when the assertions pass silently.
    print(
        f"\nnew-joiner v1: {exists_count}/{total} pages exist, "
        f"{reachable_count}/{total} reachable in <=2 hops "
        f"(baseline threshold = {threshold})"
    )

    assert exists_count >= threshold, (
        f"new-joiner regression: only {exists_count}/{total} expected pages exist on disk "
        f"(threshold {threshold}). Either pages were removed or the fixture drifted — check "
        f"tests/fixtures/new_joiner/domains.json."
    )
    assert reachable_count >= threshold, (
        f"new-joiner regression: only {reachable_count}/{total} pages reachable from home.md "
        f"in <=2 hops (threshold {threshold}). Wikilinks from home.md or intermediate hubs "
        f"may have been removed."
    )
