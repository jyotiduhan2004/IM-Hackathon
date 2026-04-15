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
from pathlib import Path

import yaml

from src.utils import split_frontmatter

# Broken pages (unparseable frontmatter) count as touched when their mtime
# is within this window — catches pages the agent just corrupted without
# nagging about pre-existing breakage from other batches.
_BROKEN_PAGE_STALENESS_SECONDS = 600

_WIKI_CATEGORIES = ("topics", "entities", "systems", "policies", "timelines", "conflicts")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+[^#].+$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


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


def find_touched_pages(raw_path: str, wiki_dir: Path) -> list[Path]:
    """Return wiki pages whose frontmatter `sources:` cites this raw email.

    Matches by basename so the caller doesn't have to normalize the
    "raw/" prefix — `raw/foo.md`, `./raw/foo.md`, and `foo.md` all hit.
    """
    raw_basename = Path(raw_path).name
    pages: list[Path] = []
    broken: list[Path] = []
    if not wiki_dir.exists():
        return pages
    for md in wiki_dir.rglob("*.md"):
        try:
            content = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fm_text, _ = split_frontmatter(content)
        if not fm_text:
            # Broken frontmatter — can't confirm citation, but a page with
            # no parseable frontmatter is itself a blocker the agent should
            # know about. We only surface pages that were touched recently
            # (mtime within the batch window) so we don't nag about pre-
            # existing broken pages from other batches.
            try:
                age = time.time() - md.stat().st_mtime
            except OSError:
                age = _BROKEN_PAGE_STALENESS_SECONDS + 1
            if age <= _BROKEN_PAGE_STALENESS_SECONDS:
                broken.append(md)
            continue
        try:
            fm = yaml.safe_load(fm_text) or {}
        except yaml.YAMLError:
            try:
                age = time.time() - md.stat().st_mtime
            except OSError:
                age = _BROKEN_PAGE_STALENESS_SECONDS + 1
            if age <= _BROKEN_PAGE_STALENESS_SECONDS:
                broken.append(md)
            continue
        if not isinstance(fm, dict):
            continue
        sources = fm.get("sources") or []
        if not isinstance(sources, list):
            continue
        for s in sources:
            if isinstance(s, str) and Path(s).name == raw_basename:
                pages.append(md)
                break
    # Recently-broken pages go to the front so their blockers are obvious
    # in the audit output even when they don't cite this raw email.
    return broken + pages


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
    broken: list[str] = []
    for link in _WIKILINK_RE.findall(body):
        target = link.split("|")[0].strip()
        if target and target not in known_slugs:
            broken.append(target)
    if not broken:
        return []
    relp = _relpath(page, repo_root)
    preview = ", ".join(broken[:5])
    if len(broken) > 5:
        preview += f" (+{len(broken) - 5} more)"
    msg = f"{len(broken)} broken wikilink(s): {preview}"
    return [Issue(_issue_id(relp, "broken-wikilink", msg), "blocker", "broken-wikilink", relp, msg)]


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
        issues.extend(_check_duplicate_h2(page, repo_root, body))
        issues.extend(_check_broken_wikilinks(page, repo_root, body, known_slugs))
        issues.extend(_check_h1_in_body(page, repo_root, body))
        issues.extend(_check_stray_bracket(page, repo_root, body))

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
