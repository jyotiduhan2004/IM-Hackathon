"""Post-write critique of wiki pages — agent-facing `check_my_work` tool.

Flow: after writing/updating wiki pages, the compiler agent calls
`check_my_work(raw_path)` directly. The tool scans pages cited via the
raw email (and any recently-modified pages with broken frontmatter —
caught via mtime window so corrupted output can't silently return
`status: clean`), then surfaces blockers (broken wikilinks, duplicate
H2, malformed frontmatter, stray markdown, H1-in-body).

The agent either fixes the page and retries (critique re-runs clean)
or passes `acknowledge=['issue_id', ...]` to proceed anyway. Every
round is logged to `docs/audits/critique-<ISO>-<msgid>.md` so we can
later sample how often the agent skipped hints. `scripts/compile_all.py`
still flips `compile_state` based on deterministic citation evidence —
`check_my_work` is advisory, not a gate.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from src.utils import extract_frontmatter
from src.utils import split_frontmatter
from src.utils.wikilinks import WIKILINK_RE
from src.utils.wikilinks import parse_wikilink_target
from src.wiki.references import FOOTNOTE_DEF_RE
from src.wiki.references import FOOTNOTE_USE_RE
from src.wiki.sections import ANTI_PATTERN_H2_LOWER
from src.wiki.sections import SUGGESTED_SECTIONS

# Broken pages (unparseable frontmatter) count as touched when their mtime
# is within this window — catches pages the agent just corrupted without
# nagging about pre-existing breakage from other batches.
_BROKEN_PAGE_STALENESS_SECONDS = 600

# Categories we scan for known-slug resolution on wikilinks. `timelines/` and
# `conflicts/` are empty on disk (retired in Tier P); dropping them from the
# scan stops the known-slugs set from growing with nothing to hit. People
# pages live under `people/` post v9-U5; `entities/` is kept as a legacy
# fallback so any unmigrated stragglers still resolve. Shim retired in #67.
_WIKI_CATEGORIES = ("topics", "entities", "people", "systems", "policies", "decisions")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+[^#].+$", re.MULTILINE)
# Email-shaped people slug: ``aa-indiamart-com``, ``neeraj-gmail-com`` — the
# ``email_to_slug`` output. Used by ``_check_broken_wikilinks`` to demote a
# missing-people-page from ``blocker`` to ``warning``: the agent can't
# create people stubs by hand (see #167); auto-stub creation fires
# elsewhere. Pattern mirrors ``src.wiki.scoring._EMAIL_SLUG_WIKILINK_RE``.
_PEOPLE_SLUG_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*-(?:indiamart-com|gmail-com|amazon-com)$",
    re.IGNORECASE,
)
@dataclass
class Issue:
    id: str
    severity: str  # "blocker" | "warning"
    check: str
    page: str  # relpath from repo root, e.g. "wiki/topics/foo.md"
    message: str


@dataclass
class CritiqueResult:
    issues: list[Issue]
    pages_critiqued: list[str]

    @property
    def blockers(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "blocker"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]


def _issue_id(page: str, check: str, message: str) -> str:
    return hashlib.sha1(f"{page}|{check}|{message}".encode()).hexdigest()[:8]


def _relpath(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _read_raw_thread_id(raw_path: str) -> str | None:
    """Best-effort: return the raw email's `thread_id:` frontmatter value.

    Phase A U5 introduces `source_threads:` on wiki pages — pages may cite
    via thread_id only (no `sources:`). So `find_touched_pages` needs the
    raw email's thread_id to match those pages. Any failure to resolve
    (missing file, corrupt frontmatter, no thread_id field) returns None
    and the caller falls back to the legacy `sources:` basename match only.
    """
    try:
        content = Path(raw_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    thread_id = extract_frontmatter(content).get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def find_touched_pages(
    raw_path: str,
    wiki_dir: Path,
    batch_touched: set[str] | list[str] | None = None,
) -> list[Path]:
    """Return wiki pages whose frontmatter cites this raw email.

    Matches `sources:` by basename (legacy citation) OR `source_threads:` by
    thread_id (Phase A U5 page-level citation). Either shape counts; the
    caller doesn't have to normalize the "raw/" prefix since the match
    compares basenames — `raw/foo.md`, `./raw/foo.md`, and `foo.md` all hit.

    Broken-frontmatter pages (unparseable YAML) are surfaced ONLY when their
    path is in ``batch_touched`` — the coordinator-supplied set of page
    paths/stems the agent wrote or edited this batch. Without that scope the
    broken-page fallback pulls unrelated whole-wiki churn (``wiki/index.md``,
    ``wiki/log.md``, merge candidates) into a per-batch critique — the
    "poisoned input set" finding from Codex's audit (#169).

    Args:
        raw_path: path to the raw email (legacy shape).
        wiki_dir: root of the wiki to scan.
        batch_touched: optional set of wiki-page paths/stems this agent
            batch actually touched. Used to scope the recently-broken fallback.
            When ``None`` (legacy callers), the broken-page fallback is
            disabled entirely — never whole-wiki.
    """
    raw_basename = Path(raw_path).name
    raw_thread_id = _read_raw_thread_id(raw_path)
    pages: list[Path] = []
    broken: list[Path] = []
    touched_set = _normalize_batch_touched(batch_touched)
    if not wiki_dir.exists():
        return pages
    for md in wiki_dir.rglob("*.md"):
        try:
            content = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fm_text, _ = split_frontmatter(content)
        if not fm_text:
            if _is_recently_broken_and_in_batch(md, touched_set):
                broken.append(md)
            continue
        try:
            fm = yaml.safe_load(fm_text) or {}
        except yaml.YAMLError:
            if _is_recently_broken_and_in_batch(md, touched_set):
                broken.append(md)
            continue
        if not isinstance(fm, dict):
            continue

        matched = False
        sources = fm.get("sources") or []
        if isinstance(sources, list):
            for s in sources:
                if isinstance(s, str) and Path(s).name == raw_basename:
                    matched = True
                    break

        if not matched and raw_thread_id:
            source_threads = fm.get("source_threads") or []
            if isinstance(source_threads, list):
                for t in source_threads:
                    if isinstance(t, str) and t == raw_thread_id:
                        matched = True
                        break

        if matched:
            pages.append(md)
    # Recently-broken pages go to the front so their blockers are obvious
    # in the audit output even when they don't cite this raw email.
    return broken + pages


def _normalize_batch_touched(
    batch_touched: set[str] | list[str] | None,
) -> set[str] | None:
    """Flatten caller-provided touched paths into a set of identifiers we
    can match ``Path``s against. Accepts stems (``a-touched``), filenames
    (``a-touched.md``), or full relpaths (``wiki/topics/a-touched.md``)."""
    if batch_touched is None:
        return None
    result: set[str] = set()
    for entry in batch_touched:
        if not isinstance(entry, str):
            continue
        result.add(entry)
        p = Path(entry)
        result.add(p.name)
        result.add(p.stem)
    return result


def _is_recently_broken_and_in_batch(md: Path, touched: set[str] | None) -> bool:
    """Gate the broken-frontmatter fallback on BOTH mtime recency AND
    agent-batch scope. ``touched=None`` means the caller didn't pass a
    batch scope — skip the fallback entirely rather than polluting the
    per-batch critique with unrelated whole-wiki churn (#169)."""
    if touched is None:
        return False
    if md.name not in touched and md.stem not in touched and str(md) not in touched:
        return False
    try:
        age = time.time() - md.stat().st_mtime
    except OSError:
        return False
    return age <= _BROKEN_PAGE_STALENESS_SECONDS


def _check_frontmatter(page: Path, repo_root: Path, content: str) -> list[Issue]:
    relp = _relpath(page, repo_root)
    issues: list[Issue] = []

    # Use split_frontmatter as the delimiter check — raw `---` count false-
    # positives on horizontal rules in the body (a valid `---` separator in
    # body content is legitimate, not a frontmatter fence). split_frontmatter
    # stops at the second `---` line, so an empty result means the fences are
    # missing or malformed.
    fm_text, body = split_frontmatter(content)
    if not fm_text:
        msg = "no parseable YAML frontmatter block (check --- delimiters)"
        issues.append(
            Issue(_issue_id(relp, "fence-count", msg), "blocker", "fence-count", relp, msg)
        )
        return issues

    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        msg = f"YAML integrity: {exc}"
        issues.append(
            Issue(_issue_id(relp, "yaml-integrity", msg), "blocker", "yaml-integrity", relp, msg)
        )
        return issues

    if not isinstance(fm, dict):
        msg = "frontmatter is not a mapping"
        issues.append(
            Issue(_issue_id(relp, "yaml-integrity", msg), "blocker", "yaml-integrity", relp, msg)
        )
        return issues

    for f in ("title", "page_type", "status"):
        if f not in fm:
            msg = f"missing required field: {f}"
            issues.append(
                Issue(
                    _issue_id(relp, "required-field", msg), "blocker", "required-field", relp, msg
                )
            )

    if not body.strip():
        msg = "empty body"
        issues.append(Issue(_issue_id(relp, "empty-body", msg), "blocker", "empty-body", relp, msg))

    return issues


def _check_duplicate_h2(page: Path, repo_root: Path, body: str) -> list[Issue]:
    headings = [h.strip() for h in _H2_RE.findall(body)]
    counts: dict[str, int] = {}
    for h in headings:
        counts[h] = counts.get(h, 0) + 1
    dups = sorted(h for h, n in counts.items() if n > 1)
    if not dups:
        return []
    relp = _relpath(page, repo_root)
    msg = f"duplicate H2 heading(s): {dups} — merge the sections instead of appending"
    return [Issue(_issue_id(relp, "duplicate-h2", msg), "blocker", "duplicate-h2", relp, msg)]


def _check_broken_wikilinks(
    page: Path, repo_root: Path, body: str, known_slugs: set[str]
) -> list[Issue]:
    """Split broken wikilinks into two severities: blocker for real concept
    references (the agent must fix or create those), warning for email-
    shaped people slugs (``aa-indiamart-com``) where the people page
    doesn't exist yet — the agent can't hand-create those; auto-stub
    creation fires elsewhere (#167)."""
    broken_hard: list[str] = []
    broken_people: list[str] = []
    for link in WIKILINK_RE.findall(body):
        target = parse_wikilink_target(link)
        # CLAUDE.md teaches prefix-style wikilinks as canonical
        # (`[[system/foo]]`, `[[decisions/bar]]`). `known_slugs` is keyed
        # on bare stem, so strip the category prefix before lookup.
        slug = target.rsplit("/", 1)[-1] if target else target
        if not slug or slug in known_slugs:
            continue
        if _PEOPLE_SLUG_RE.match(slug):
            broken_people.append(target)
        else:
            broken_hard.append(target)

    issues: list[Issue] = []
    relp = _relpath(page, repo_root)
    if broken_hard:
        preview = ", ".join(broken_hard[:5])
        if len(broken_hard) > 5:
            preview += f" (+{len(broken_hard) - 5} more)"
        msg = f"{len(broken_hard)} broken wikilink(s): {preview}"
        issues.append(
            Issue(_issue_id(relp, "broken-wikilink", msg), "blocker", "broken-wikilink", relp, msg)
        )
    if broken_people:
        preview = ", ".join(broken_people[:5])
        if len(broken_people) > 5:
            preview += f" (+{len(broken_people) - 5} more)"
        msg = (
            f"{len(broken_people)} legacy people-slug wikilink(s) without a people "
            f"page: {preview}. Auto-stub creation handles these; no agent action required."
        )
        issues.append(
            Issue(
                _issue_id(relp, "broken-people-slug", msg),
                "warning",
                "broken-people-slug",
                relp,
                msg,
            )
        )
    return issues


def _check_h1_in_body(page: Path, repo_root: Path, body: str) -> list[Issue]:
    if not _H1_RE.search(body):
        return []
    relp = _relpath(page, repo_root)
    msg = "H1 heading in body — title belongs in frontmatter, body starts at H2"
    return [Issue(_issue_id(relp, "h1-in-body", msg), "warning", "h1-in-body", relp, msg)]


def _check_stray_bracket(page: Path, repo_root: Path, body: str) -> list[Issue]:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped in ("]", "]]", "["):
            relp = _relpath(page, repo_root)
            msg = f"stray bracket on its own line: {stripped!r} — likely a malformed list"
            return [
                Issue(_issue_id(relp, "stray-bracket", msg), "blocker", "stray-bracket", relp, msg)
            ]
    return []


def _check_footnote_defs(page: Path, repo_root: Path, body: str) -> list[Issue]:
    """Surface ``[^msg-x]`` body refs that have no matching definition.

    The post-batch coordinator backfill is the primary fix for this: it
    runs deterministically after every batch in ``compile_all.py`` and
    rewrites the def block on disk. This check is the safety net for
    non-coordinator entrypoints (``watch_and_compile.py``,
    ``compile_parallel.py``) which call ``run_compilation`` directly
    and skip the post-batch hook chain. Severity is ``warning`` so it
    surfaces the gap without blocking the agent's check_my_work loop —
    the contradiction with the "agent doesn't author References" prompt
    rule was the failure mode that motivated the deterministic fix.
    """
    uses = {m.group(1).lower() for m in FOOTNOTE_USE_RE.finditer(body)}
    defs = {m.group(1).lower() for m in FOOTNOTE_DEF_RE.finditer(body)}
    missing = sorted(uses - defs)
    if not missing:
        return []
    relp = _relpath(page, repo_root)
    preview = ", ".join(f"[^{m}]" for m in missing[:5])
    if len(missing) > 5:
        preview += f" (+{len(missing) - 5} more)"
    msg = (
        f"{len(missing)} footnote usage(s) without a matching definition: "
        f"{preview}. The post-batch coordinator hook backfills these in "
        f"the standard `compile_all.py` flow; if you're seeing this, the "
        f"page was compiled through a non-coordinator entrypoint."
    )
    return [
        Issue(
            _issue_id(relp, "footnote-missing-def", msg),
            "warning",
            "footnote-missing-def",
            relp,
            msg,
        )
    ]


# V12 deep-audit fix-A — anti-pattern H2 + summary-staleness checks.
_DECISION_PREFIX = "decision:"
# ISO dates in the body (``2026-04-20``). Used by the summary-staleness
# rule to detect "Recent changes acknowledges newness; Summary doesn't
# mention current state."
_ISO_DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
# Heuristic: events newer than this relative to last_compiled count as
# "recent" enough to expect the Summary to describe the current truth.
# 90 days mirrors the V12-U4 teaching ("current state" means current-
# quarter horizon).
_SUMMARY_STALENESS_WINDOW_DAYS = 90
# Current-state markers in the Summary paragraph. If the Summary mentions
# any of these tokens (or contains any ISO date itself), we assume it's
# been rewritten to current truth and the rule stays quiet. Case-
# insensitive match; trailing spaces avoid substring collisions, mirroring
# ``src.wiki.scoring.GOOD_TOKENS`` discipline.
_SUMMARY_CURRENT_STATE_TOKENS: tuple[str, ...] = (
    "live ",
    "currently ",
    "currently,",
    "rolled out to ",
    "as of ",
    "today ",
)


def _check_anti_pattern_h2(page: Path, repo_root: Path, body: str) -> list[Issue]:
    """Per ``<concept_vs_thread>`` teaching: narrative/thread-subject H2s
    describe one email's flow, not a concept. Warn the agent so it can
    self-correct mid-batch.

    Shares ``ANTI_PATTERN_H2`` with ``src.wiki.scoring`` — scorer and
    critique must never drift. ``decision:`` prefix rule is handled here
    (not in the frozenset) to avoid enumerating every suffix like
    ``## Decision: Scale to 100%``.
    """
    h2_titles = [m.group(1).strip() for m in _H2_RE.finditer(body)]
    bad = [
        h
        for h in h2_titles
        if h.lower() in ANTI_PATTERN_H2_LOWER or h.lower().startswith(_DECISION_PREFIX)
    ]
    if not bad:
        return []
    relp = _relpath(page, repo_root)
    msg = (
        f"anti-pattern H2(s) {bad}: these describe one email's flow, "
        "not a durable concept. See <concept_vs_thread>."
    )
    return [
        Issue(
            _issue_id(relp, "anti-pattern-h2", msg),
            "warning",
            "anti-pattern-h2",
            relp,
            msg,
        )
    ]


def _check_recent_changes_h2(
    page: Path, repo_root: Path, body: str, page_type: str | None
) -> list[Issue]:
    """Topic pages MUST have a ``## Recent changes`` H2 — it's the place
    where ongoing batch history lives. Missing it means new emails get
    appended as fresh H2s (``## Launch Announcement``, etc.), which is
    exactly the anti-pattern V12 is trying to kill (#158)."""
    if page_type != "topic":
        return []
    h2_titles_lower = {m.group(1).strip().lower() for m in _H2_RE.finditer(body)}
    if "recent changes" in h2_titles_lower:
        return []
    relp = _relpath(page, repo_root)
    msg = (
        "missing `## Recent changes` H2. Topic pages use this section to "
        "append dated batch updates — without it, new emails get jammed "
        "into fresh thread-subject H2s. Add the section even if empty."
    )
    return [
        Issue(
            _issue_id(relp, "missing-recent-changes-h2", msg),
            "blocker",
            "missing-recent-changes-h2",
            relp,
            msg,
        )
    ]


def _parse_last_compiled(value: Any) -> datetime | None:
    """Best-effort: return a timezone-aware UTC datetime from a
    ``last_compiled`` frontmatter value. Returns ``None`` for stub
    markers (``"stub"``, ``"stub-backfilled"``) or unparseable input —
    caller treats that as "no signal, skip the check"."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s in ("stub", "stub-backfilled"):
        return None
    # ``datetime.fromisoformat`` handles ``2026-04-15T07:00:00Z`` in 3.11+
    # via ``Z`` → ``+00:00`` normalization.
    normalized = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _parse_iso_dates(text: str) -> list[datetime]:
    """Extract all ISO-formatted dates from ``text`` as UTC datetimes.
    Malformed matches (``2026-13-40``) are skipped silently — ``_ISO_DATE_RE``
    is permissive by design to catch prose variants like ``on 2026-04-20``
    or ``(2026-04-20)``, so callers expect best-effort parsing."""
    parsed: list[datetime] = []
    for match in _ISO_DATE_RE.findall(text):
        try:
            parsed.append(datetime.fromisoformat(match).replace(tzinfo=UTC))
        except ValueError:
            continue
    return parsed


def _recent_changes_block(body: str) -> str | None:
    """Extract the ``## Recent changes`` / ``## Recent Changes`` section.

    Returns body text from the Recent-changes heading up to the next H2
    or EOF. Returns ``None`` if the section isn't present."""
    lines = body.splitlines()
    in_block = False
    start = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_block:
            if stripped.lower().startswith("## recent changes"):
                in_block = True
                start = i + 1
                continue
        else:
            if line.startswith("## "):
                return "\n".join(lines[start:i])
    if in_block and start != -1:
        return "\n".join(lines[start:])
    return None


def _first_paragraph(body: str) -> str:
    """Return the first non-empty, non-heading paragraph after any H1.

    Mirrors ``src.wiki.scoring._first_paragraph`` — duplicated (not
    imported) to keep the critique module's dependency graph narrow;
    both copies are one-screen functions, and the scoring module treats
    its copy as private (leading underscore)."""
    lines = body.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# ") and not lines[i].startswith("## "):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
    buf: list[str] = []
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            break
        if line.startswith("#"):
            break
        buf.append(line)
        i += 1
    return " ".join(line.strip() for line in buf).strip()


def _check_summary_staleness(
    page: Path,
    repo_root: Path,
    body: str,
    frontmatter: dict[str, Any],
) -> list[Issue]:
    """Warn when the Summary paragraph looks stale — ``Recent changes``
    contains a date newer than ``last_compiled``, suggesting the agent
    updated the body but forgot to rewrite the Summary to match
    current truth (V12-U4 teaching).

    v1 heuristic (iterate later): warn if ``## Recent changes`` contains
    any ISO date within the last ``_SUMMARY_STALENESS_WINDOW_DAYS`` days
    and the Summary paragraph contains neither an ISO date nor a
    current-state token (``live``, ``currently``, ``rolled out to``,
    etc.). Key caveat: we can't detect "Summary was unchanged" without
    a pre-compile snapshot — stage 2 will diff against one. Until then
    this is a proxy for "Recent changes acknowledges newness; Summary
    doesn't mention current state".
    """
    recent_block = _recent_changes_block(body)
    if recent_block is None:
        return []
    parsed_dates = _parse_iso_dates(recent_block)
    if not parsed_dates:
        return []

    # Anchor the staleness window to ``last_compiled`` when present,
    # otherwise ``datetime.now`` — legacy pages missing the field still
    # get flagged by absolute-date recency.
    last_compiled = _parse_last_compiled(frontmatter.get("last_compiled"))
    anchor = last_compiled if last_compiled is not None else datetime.now(tz=UTC)
    window_start = anchor - timedelta(days=_SUMMARY_STALENESS_WINDOW_DAYS)

    recent_events = [d for d in parsed_dates if d >= window_start]
    if not recent_events:
        return []

    summary = _first_paragraph(body)
    summary_lower = summary.lower()
    has_iso_date = bool(_ISO_DATE_RE.search(summary))
    has_current_marker = any(tok in summary_lower for tok in _SUMMARY_CURRENT_STATE_TOKENS)
    if has_iso_date or has_current_marker:
        return []

    relp = _relpath(page, repo_root)
    newest = max(recent_events).date().isoformat()
    msg = (
        f"summary may be stale: `## Recent changes` cites {newest} but the "
        "Summary paragraph has no ISO date or current-state marker "
        "(`live`, `currently`, `rolled out to ...`). V12-U4: rewrite the "
        "Summary to current truth, don't just append to Recent changes."
    )
    return [
        Issue(
            _issue_id(relp, "summary-staleness", msg),
            "warning",
            "summary-staleness",
            relp,
            msg,
        )
    ]


def _check_summary_stale_date(
    page: Path,
    repo_root: Path,
    body: str,
) -> list[Issue]:
    """Blocker-level date comparison: if ``## Recent changes`` has a
    bullet with a date strictly newer than any date mentioned in the
    Summary paragraph, the Summary is demonstrably out of sync with the
    page's own history (V12-U4 teaching, #182).

    Stricter than ``_check_summary_staleness`` (which warns on absence of
    any current-state marker): this rule only fires when we have
    evidence on both sides — Recent-changes date AND Summary date — and
    they disagree on direction. False-positive rate stays low because
    the signal is explicit.
    """
    recent_block = _recent_changes_block(body)
    if recent_block is None:
        return []
    recent_dates = _parse_iso_dates(recent_block)
    if not recent_dates:
        return []

    summary_dates = _parse_iso_dates(_first_paragraph(body))
    if not summary_dates:
        # No date in Summary — covered by the warning-level
        # `_check_summary_staleness` rule; this blocker needs explicit
        # evidence on both sides.
        return []

    newest_recent = max(recent_dates)
    newest_summary = max(summary_dates)
    if newest_recent <= newest_summary:
        return []

    relp = _relpath(page, repo_root)
    msg = (
        f"summary stale: `## Recent changes` has an entry on "
        f"{newest_recent.date().isoformat()} but the Summary's newest "
        f"date is {newest_summary.date().isoformat()}. Rewrite the "
        "Summary to reflect the latest change before returning."
    )
    return [
        Issue(
            _issue_id(relp, "summary-stale-date", msg),
            "blocker",
            "summary-stale-date",
            relp,
            msg,
        )
    ]


# Threshold per page type for "too few suggested H2s present". Set to
# half-or-less of the canonical list — enough latitude for genuinely
# alternative structures, tight enough to flag thread-subject-templated
# pages with zero canonical H2s. Reviewer takes the final call.
#
# PR2 (2026-04-28 prompt-review Q7.1): the universal H2 floor shrunk
# (Summary, Key decisions, References dropped); thresholds rescaled
# proportionally.
_SUGGESTED_H2_FLOOR: dict[str, int] = {
    "topic": 3,  # 3/5
    "system": 3,  # 3/5
    "policy": 3,  # 3/5
}


def _check_suggested_h2_sections(
    page: Path, repo_root: Path, body: str, page_type: str | None
) -> list[Issue]:
    """Warning-only: count present canonical H2s vs. SUGGESTED_SECTIONS.

    Skips decision/people/glossary — only topic/system/policy carry the
    canonical shape. The rule emits one warning per page when the count
    falls below `_SUGGESTED_H2_FLOOR[page_type]`. Severity is always
    `warning`; final judgment ("does the chosen structure fit?") lives
    in the reviewer (`filing_cabinet` / `structure_mismatch`).
    """
    if page_type not in SUGGESTED_SECTIONS:
        return []
    suggested = SUGGESTED_SECTIONS[page_type]
    floor = _SUGGESTED_H2_FLOOR[page_type]

    headings_lower = [h.strip().lower() for h in _H2_RE.findall(body)]
    present = [s for s in suggested if any(s.lower() in h for h in headings_lower)]
    missing = [s for s in suggested if s not in present]
    if len(present) >= floor:
        return []

    relp = _relpath(page, repo_root)
    msg = (
        f"present {len(present)}/{len(suggested)} suggested H2s: {present}; "
        f"missing: {missing}. If these don't apply to this page, explain in "
        "the body or add placeholder sections with 'None documented yet.' "
        "Reviewer will evaluate if the chosen structure fits."
    )
    return [
        Issue(
            _issue_id(relp, "missing_suggested_h2s", msg),
            "warning",
            "missing_suggested_h2s",
            relp,
            msg,
        )
    ]


def critique_pages(paths: list[Path], wiki_dir: Path, repo_root: Path) -> CritiqueResult:
    """Run all checks against the given wiki pages."""
    known_slugs: set[str] = set()
    for cat in _WIKI_CATEGORIES:
        d = wiki_dir / cat
        if d.exists():
            known_slugs.update(p.stem for p in d.glob("*.md"))

    issues: list[Issue] = []
    pages_rel: list[str] = []
    for page in paths:
        pages_rel.append(_relpath(page, repo_root))
        try:
            content = page.read_text(encoding="utf-8")
        except OSError as exc:
            relp = _relpath(page, repo_root)
            msg = f"unreadable: {exc}"
            issues.append(
                Issue(_issue_id(relp, "unreadable", msg), "blocker", "unreadable", relp, msg)
            )
            continue

        fm_issues = _check_frontmatter(page, repo_root, content)
        issues.extend(fm_issues)
        # Skip body-level checks if the frontmatter itself is unparseable —
        # body may not even be correctly split.
        fm_blocked = any(
            i.check in ("fence-count", "yaml-parse", "yaml-integrity") for i in fm_issues
        )
        if fm_blocked:
            continue

        _, body = split_frontmatter(content)
        # `_check_frontmatter` above already parsed the FM and would have
        # bailed via fm_blocked if it were unparseable — so the second
        # parse here via `extract_frontmatter` is safe and never errors.
        # Worth the second parse for the simpler call site.
        fm = extract_frontmatter(content)
        pt = fm.get("page_type")
        page_type = pt if isinstance(pt, str) else None

        issues.extend(_check_duplicate_h2(page, repo_root, body))
        issues.extend(_check_broken_wikilinks(page, repo_root, body, known_slugs))
        issues.extend(_check_h1_in_body(page, repo_root, body))
        issues.extend(_check_stray_bracket(page, repo_root, body))
        # Footnote-def integrity is guaranteed by the post-batch
        # coordinator hook in `compile_all.py`. The check below remains
        # as a `warning`-level safety net for non-coordinator
        # entrypoints (`watch_and_compile.py`, `compile_parallel.py`).
        issues.extend(_check_footnote_defs(page, repo_root, body))
        issues.extend(_check_recent_changes_h2(page, repo_root, body, page_type))
        issues.extend(_check_suggested_h2_sections(page, repo_root, body, page_type))
        issues.extend(_check_anti_pattern_h2(page, repo_root, body))
        issues.extend(_check_summary_staleness(page, repo_root, body, fm))
        issues.extend(_check_summary_stale_date(page, repo_root, body))

    return CritiqueResult(issues=issues, pages_critiqued=pages_rel)


def write_audit(
    result: CritiqueResult,
    raw_path: str,
    action: str,
    audit_dir: Path,
    acknowledged_ids: set[str] | None = None,
) -> Path:
    """Dump critique + action to docs/audits/critique-<ISO>-<msgid>.md.

    The timestamp is microsecond-precise and, if a collision is still
    possible (fix-loop retries inside a single microsecond), we walk a
    counter suffix until we find an unclaimed path. Second-granularity
    names silently overwrote prior rounds and lost the blocked→clean
    history.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=UTC)
    ts = now.strftime("%Y%m%dT%H%M%S%fZ")
    stem = Path(raw_path).stem
    msgid = stem.rsplit("_", 1)[-1] if "_" in stem else stem
    out = audit_dir / f"critique-{ts}-{msgid}.md"
    # Defensive: even with microsecond precision, a clock that doesn't
    # tick monotonically in between calls (unlikely but possible on some
    # filesystems) could collide. Walk a counter suffix.
    counter = 1
    while out.exists():
        out = audit_dir / f"critique-{ts}-{msgid}-{counter}.md"
        counter += 1

    ack = acknowledged_ids or set()
    lines = [
        "---",
        f"timestamp: {now.isoformat()}",
        f"raw_email: {raw_path}",
        f"action: {action}",
        f"pages_critiqued: {len(result.pages_critiqued)}",
        f"blockers: {len(result.blockers)}",
        f"warnings: {len(result.warnings)}",
        f"acknowledged: {len(ack)}",
        "---",
        "",
        f"# Critique for `{raw_path}`",
        "",
        f"**Action**: `{action}`",
        "",
    ]

    if result.pages_critiqued:
        lines.append("## Pages touched")
        lines.extend(f"- `{p}`" for p in result.pages_critiqued)
        lines.append("")
    else:
        lines.extend(
            [
                "## Pages touched",
                "_No pages in `wiki/` cite this raw email as a source._",
                "",
            ]
        )

    if result.blockers:
        lines.append("## Blockers")
        for i in result.blockers:
            ack_note = " — _acknowledged_" if i.id in ack else ""
            lines.append(f"- `[{i.id}]` **{i.check}** · `{i.page}` — {i.message}{ack_note}")
        lines.append("")

    if result.warnings:
        lines.append("## Warnings")
        for i in result.warnings:
            lines.append(f"- `[{i.id}]` **{i.check}** · `{i.page}` — {i.message}")
        lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
