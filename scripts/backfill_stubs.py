"""Second-pass stub-filler.

Walks wiki/entities/ and wiki/systems/ for "stub" pages — pages with empty
sources:[] or `last_compiled: "stub"` — and rebuilds their `sources:` list
by grep-searching raw/ for their slug, name variants, or email.

This runs AFTER a compile batch as a cheap post-processing step. It does NOT
re-compile the page body via LLM — that's the job of the main compiler
when the raw sources get marked uncompiled again.

Two modes:
- --refresh-sources (default): only updates the sources list
- --recompile: marks raw sources as compiled=false so the next compile run
  will rewrite the page body with the full thread context

Usage:
    uv run python scripts/backfill_stubs.py
    uv run python scripts/backfill_stubs.py --recompile
    uv run python scripts/backfill_stubs.py --dry-run
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click
import yaml

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402


def _grep_raw_for_entity(
    slug: str, email: str | None, raw_dir: Path, name_only_hit_cap: int = 30
) -> list[str]:
    """Find raw files where this person is From/To/CC'd or signed the body.

    Much stricter than matching slug substrings — that gave us 1500 matches
    for common tokens like "whatsapp" or "buylead". Real signal for an
    entity is the person's email address appearing in headers or signature.

    Strategy (in order):
    1. If `email` is supplied or derivable, match any raw where that email
       appears (From/To/CC/body).
    2. If slug is 2-3 kebab parts (likely a name), try candidate emails:
       `first.last@indiamart.com`, `last.first@`, `firstlast@`, plus
       occurrences of the full `First Last` as a name match in From/CC lines.
    3. Skip pages where match count exceeds `max_hits` — that indicates the
       slug is a common term, not a person.

    Returns sorted list of repo-relative paths.
    """
    import re as _re

    parts = slug.split("-")
    email_candidates: set[str] = set()
    name_candidates: set[str] = set()

    if email:
        email_candidates.add(email.lower())
    elif (
        2 <= len(parts) <= 4
        and all(p.isalpha() for p in parts)
        and all(len(p) >= 3 for p in parts)  # rules out "m-site", "m-cat", etc.
    ):
        first, last = parts[0], parts[-1]
        # Typical IndiaMART email patterns from observed data
        for domain in ("@indiamart.com", "@intermesh.net"):
            email_candidates.update(
                {
                    f"{first}.{last}{domain}".lower(),
                    f"{last}.{first}{domain}".lower(),
                    f"{first}{last}{domain}".lower(),
                    f"{first}1{domain}".lower(),  # common disambiguator
                }
            )
        name_candidates.add(f"{first} {last}".lower())
        if len(parts) >= 3:
            middle = " ".join(parts[1:-1])
            name_candidates.add(f"{first} {middle} {last}".lower())

    if not email_candidates and not name_candidates:
        # Very short or non-person-looking slug: don't backfill with name
        # search (too noisy). Caller will record "no matches".
        return []

    email_hits: set[str] = set()
    name_only_hits: set[str] = set()
    repo_root_resolved = REPO_ROOT.resolve()
    from_cc_re = _re.compile(
        r"^(from|to|cc|delivered-to):\s*(.+)$", _re.IGNORECASE | _re.MULTILINE
    )

    for md in raw_dir.glob("*.md"):
        try:
            content = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lc = content.lower()

        rel: str
        try:
            rel = str(md.resolve().relative_to(repo_root_resolved))
        except ValueError:
            rel = f"raw/{md.name}"

        # Email match is authoritative — no cap
        for e in email_candidates:
            if e in lc:
                email_hits.add(rel)
                break

        # Name-only match: only count if the full "First Last" appears in a
        # From/To/CC line (not body). Capped to avoid common-word collisions.
        if rel not in email_hits and name_candidates:
            header_text = "\n".join(m.group(0) for m in from_cc_re.finditer(content))
            header_lc = header_text.lower()
            for n in name_candidates:
                if n in header_lc:
                    name_only_hits.add(rel)
                    break

    # If we have ANY email-authoritative hits, that's the signal — return all
    # of them (plus any name-only hits below the cap for completeness).
    if email_hits:
        combined = email_hits | name_only_hits
        return sorted(combined)

    # No email hits: fall back to name-only hits, but abort if suspiciously
    # common (implies the slug is a shared name like "amit agarwal" that
    # appears in many unrelated threads).
    if len(name_only_hits) > name_only_hit_cap:
        return []
    return sorted(name_only_hits)


# Kept for callers that don't know the category
_grep_raw_for_slug = _grep_raw_for_entity


def _is_stub(fm: dict) -> bool:
    """A page is a stub if sources is empty OR last_compiled is literal 'stub'."""
    if fm.get("last_compiled") == "stub":
        return True
    sources = fm.get("sources") or []
    return len(sources) == 0


@click.command()
@click.option(
    "--dry-run", is_flag=True, help="Show what would be backfilled without writing"
)
@click.option(
    "--recompile",
    is_flag=True,
    help="After rewriting sources, mark those raw files as compiled=false so "
    "the next compile run regenerates the page body with full context",
)
@click.option(
    "--category",
    type=click.Choice(["entities", "systems", "all"]),
    default="all",
    help="Which wiki category to backfill stubs in",
)
def main(dry_run: bool, recompile: bool, category: str) -> None:
    """Find stub wiki pages and backfill their sources list from raw/."""
    wiki_dir = settings.wiki_dir
    raw_dir = settings.raw_dir

    cats = ["entities", "systems"] if category == "all" else [category]

    found = 0
    backfilled = 0
    no_matches = 0
    raw_to_reset: set[Path] = set()

    for cat in cats:
        cat_dir = wiki_dir / cat
        if not cat_dir.exists():
            continue
        for page in sorted(cat_dir.glob("*.md")):
            content = page.read_text(encoding="utf-8")
            fm = extract_frontmatter(content)
            if not _is_stub(fm):
                continue

            found += 1
            slug = page.stem
            # Only backfill entities (people). Systems need different heuristic
            # (product/URL references, not name-style email addresses).
            if cat != "entities":
                click.echo(f"  skip {cat}/{slug} (only entities backfilled by this pass)")
                continue
            # Try to extract an email from the existing body (our compiler
            # often writes "Email: first.last@domain" as first body line).
            body_email_match = re.search(
                r"(?:^|\s)(?:email[:\s]+)?([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]+)",
                extract_body(content),
                re.IGNORECASE,
            )
            body_email = body_email_match.group(1) if body_email_match else None
            hits = _grep_raw_for_entity(slug, body_email, raw_dir)

            if not hits:
                no_matches += 1
                click.echo(f"  no matches for {cat}/{slug}")
                continue

            click.echo(f"  {cat}/{slug}: {len(hits)} raw source(s) found")

            if dry_run:
                continue

            # Rewrite sources; leave body alone
            fm["sources"] = hits
            if fm.get("last_compiled") == "stub":
                fm["last_compiled"] = "stub-backfilled"
            body = extract_body(content)
            page.write_text(render_with_frontmatter(fm, body), encoding="utf-8")
            backfilled += 1

            if recompile:
                # Mark raw files as uncompiled so the next compile run picks them up
                # and can rewrite this page's body with full thread context
                for hit in hits:
                    raw_path = REPO_ROOT / hit
                    if not raw_path.exists():
                        continue
                    rc = raw_path.read_text(encoding="utf-8")
                    rfm = extract_frontmatter(rc)
                    if rfm.get("compiled") is True:
                        rfm["compiled"] = False
                        rfm.pop("compiled_at", None)
                        new = render_with_frontmatter(rfm, extract_body(rc))
                        raw_path.write_text(new, encoding="utf-8")
                        raw_to_reset.add(raw_path)

    click.echo()
    click.echo(f"Stubs found: {found}")
    click.echo(f"Backfilled: {backfilled}")
    click.echo(f"No matches (kept empty): {no_matches}")
    if recompile:
        click.echo(f"Raw files reset for recompile: {len(raw_to_reset)}")


if __name__ == "__main__":
    main()
