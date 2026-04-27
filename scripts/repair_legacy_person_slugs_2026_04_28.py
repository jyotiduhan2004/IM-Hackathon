"""One-shot migration of remaining legacy display-name person slugs to
the email-canonical form (`name-indiamart-com`). Surfaced by the
audit-status tracker (`docs/audits/STATUS.md`, finding F-013) — the
broader "wiki hygiene" cleanup behind the 3-pair fix in PR #241.

Inventory (re-derived dynamically; counts are illustrative):

- ~588 person pages in `wiki/people/`. ~479 already canonical.
- ~107 legacy-form pages (`alok-kumar`, `vikram-varshney`, etc.).
  Of those:
  * ~63 have a canonical twin already on disk (both pages live, same
    email): MERGE via `apply_merge_candidate.py`.
  * ~44 have no twin: RENAME to canonical form.

What this script does, per legacy page:

1. Pre-flight collision check on both `wiki_pages.slug` UNIQUE and
   `wiki_pages.path` UNIQUE (`src/db/schema.sql`:174-208). If a stub
   row already squats on the canonical path, treat as MERGE not RENAME.
2. **MERGE path** (twin exists): delegate to `scripts/apply_merge_candidate.py`
   which marks the loser `status=superseded` + `superseded_by=canonical`
   in markdown + DB. Wikilink rewriting is left to the final healing
   pass (apply_merge_candidate is intentionally conservative).
3. **RENAME path** (no twin): order is FS-first, then DB, then frontmatter
   — this is recoverable on a mid-row crash because re-running detects
   the post-state (canonical file exists) and skips. Reverse order would
   leave DB pointing at a non-existent path with no on-disk signal.
   1. `os.rename(legacy.md, canonical.md)`
   2. `UPDATE wiki_pages SET slug=canonical, path=canonical_path WHERE slug=legacy`
   3. Normalize frontmatter (`email:` + `is_external:`) via
      `migrate_entity_slugs.py::_normalize_frontmatter`.
4. **Final wikilink-rewrite pass** (idempotency heal): match every
   `[[legacy_slug]]` and `[[legacy_slug|alias]]` across the entire
   `wiki/` tree (incl. `home.md`, `changes.md`, `glossary.md`,
   domain hubs, decisions, the `people/` directory itself) and rewrite
   to `[[canonical]]`. Catches survivors from any partial crash
   between steps. The two superseded tombstones from PR #241 are
   skipped so their `superseded_by:` values don't get clobbered.

What this script DOES NOT do:

- It does not "fix" mailing-list pages misfiled under `wiki/people/`
  (e.g. `mobile-team`, `chatbot-accuracy-audit`). Those are
  miscategorization (STATUS.md F-015), not slug hygiene. Renaming
  follows the convention; the deeper fix is a separate PR.
- It does not touch `raw/*.md` (immutable evidence; subject lines
  legitimately mention legacy display names).
- It does not regenerate `wiki/log.md` / `wiki/compile-status.md`'s
  body content — those refresh on next compile via
  `_rebuild_person_backlinks` and the coordinator's landing-page hook.

Recovery:

- Pre-`--commit` step tarballs `wiki/people/` + `pg_dump wiki_pages`
  into `/tmp/repair-people-slugs-<run_id>/` so a botched run can be
  rolled back. (`wiki/` is gitignored — `git checkout` won't help.)

One-shot lifecycle:

- Last production run: 2026-04-28
- Safe to delete after: 2026-05-28
- Deletion gate: ``scripts/audit.py`` reports zero ``wiki/people/*.md``
  with slugs that don't end in ``-indiamart-com`` (excluding superseded
  tombstones) for 7 consecutive days.

Usage::

    uv run python scripts/repair_legacy_person_slugs_2026_04_28.py --dry-run
    uv run python scripts/repair_legacy_person_slugs_2026_04_28.py --commit
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import UTC
from datetime import datetime
from pathlib import Path

import click

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compile.entities import email_to_slug  # noqa: E402
from src.compile.entities import is_external_email  # noqa: E402
from src.compile.entities import is_valid_email  # noqa: E402
from src.db import connect  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

WIKI = REPO_ROOT / "wiki"
PEOPLE = WIKI / "people"

# Wikilink rewrite walks the entire wiki/ subtree (rglob('*.md')),
# not a hardcoded category list — caught during smoke test that the
# append-only wiki/log.md carries body [[person-slug]] wikilinks too.


def _email_for(page: Path) -> str | None:
    """Return the canonical (lowercased) email from frontmatter, or None.

    Mirrors `migrate_entity_slugs.py::_email_for` but only checks
    frontmatter — body fallback was for legacy entity pages, and current
    people pages always have it in frontmatter.
    """
    try:
        text = page.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm = extract_frontmatter(text)
    val = fm.get("email")
    if isinstance(val, str) and is_valid_email(val):
        return val.strip().lower()
    return None


def _normalize_frontmatter(page: Path, email: str) -> bool:
    """Ensure email + is_external are present and correct. Returns True
    if changed."""
    try:
        text = page.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    fm = extract_frontmatter(text)
    body = extract_body(text)
    expected_external = is_external_email(email)
    if fm.get("email") == email and fm.get("is_external") == expected_external:
        return False
    fm["email"] = email
    fm["is_external"] = expected_external
    page.write_text(render_with_frontmatter(fm, body), encoding="utf-8")
    return True


def _rewrite_wikilinks_for_slugs(slug_map: dict[str, str]) -> tuple[int, int]:
    """Final healing pass: rewrite [[old]] / [[old|alias]] → [[new]] for
    every (old, new) in slug_map across the entire wiki/ subtree
    (rglob('*.md') — includes log.md / compile-status.md / domain hubs
    / decisions / people cross-refs). Skips superseded tombstones so
    their `superseded_by:` values stay intact.

    Returns (files_touched, total_rewrites).
    """
    if not slug_map:
        return 0, 0
    # Build one regex with named alternation. Each alternative captures
    # the alias (or empty). The replacement function looks up which old
    # slug matched and substitutes.
    pattern = re.compile(
        r"\[\[(?P<old>" + "|".join(re.escape(k) for k in slug_map) + r")(?P<rest>(?:\|[^\]]*)?)\]\]"
    )

    def _repl(m: re.Match[str]) -> str:
        return f"[[{slug_map[m.group('old')]}{m.group('rest')}]]"

    # Walk the entire wiki/ subtree. The hardcoded category list missed
    # wiki/log.md (append-only batch summaries that legitimately contain
    # [[person-slug]] body wikilinks) — caught during the --limit 5
    # smoke test. rglob is cheap once per script run.
    files_touched = 0
    total = 0
    candidates = sorted(WIKI.rglob("*.md"))
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Skip superseded tombstones — their superseded_by: value is a
        # YAML scalar with a slug, but our regex only matches [[...]]
        # so this is belt-and-suspenders.
        fm = extract_frontmatter(text)
        if fm.get("status") == "superseded":
            continue
        new_text, n = pattern.subn(_repl, text)
        if n:
            files_touched += 1
            total += n
            path.write_text(new_text, encoding="utf-8")
    return files_touched, total


def _backup(run_id: str) -> Path:
    """Tarball wiki/people/ + pg_dump wiki_pages to /tmp/. Returns the
    backup directory."""
    out = Path("/tmp") / f"repair-people-slugs-{run_id}"
    out.mkdir(parents=True, exist_ok=True)
    shutil.make_archive(str(out / "wiki-people"), "gztar", root_dir=WIKI, base_dir="people")
    # pg_dump via subprocess; failure is non-fatal but warned.
    try:
        with (out / "wiki_pages.sql").open("w") as f:
            subprocess.run(
                [
                    "pg_dump",
                    "--data-only",
                    "--table=wiki_pages",
                    os.environ.get("DATABASE_URL", ""),
                ],
                stdout=f,
                check=False,
                timeout=60,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        click.echo(f"  warning: pg_dump failed ({exc}); proceeding without DB backup", err=True)
    return out


def _classify() -> tuple[
    list[tuple[Path, str, str]],  # to_rename: (legacy_path, email, canonical_slug)
    list[tuple[Path, str, str]],  # to_merge: (legacy_path, email, canonical_slug)
    list[Path],  # already_canonical
    list[Path],  # superseded (skip)
    list[Path],  # no_email (skip)
]:
    """Walk wiki/people/ once; classify each page."""
    to_rename: list[tuple[Path, str, str]] = []
    to_merge: list[tuple[Path, str, str]] = []
    already_canonical: list[Path] = []
    superseded: list[Path] = []
    no_email: list[Path] = []
    for page in sorted(PEOPLE.glob("*.md")):
        text = page.read_text(encoding="utf-8")
        fm = extract_frontmatter(text)
        if fm.get("status") == "superseded":
            superseded.append(page)
            continue
        email = _email_for(page)
        if not email:
            no_email.append(page)
            continue
        try:
            canonical = email_to_slug(email)
        except (TypeError, ValueError):
            no_email.append(page)
            continue
        if page.stem == canonical:
            already_canonical.append(page)
            continue
        target = PEOPLE / f"{canonical}.md"
        if target.exists():
            to_merge.append((page, email, canonical))
        else:
            to_rename.append((page, email, canonical))
    return to_rename, to_merge, already_canonical, superseded, no_email


def _check_db_path_collision(canonical_slug: str, canonical_path_str: str) -> str | None:
    """Returns reason string if either UNIQUE constraint would block the
    rename, else None.

    Catches the "stub squatting on canonical path" case Codex flagged.
    """
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT slug FROM wiki_pages WHERE slug = %s", (canonical_slug,))
        if cur.fetchone():
            return f"slug={canonical_slug} already exists in DB"
        cur.execute("SELECT slug FROM wiki_pages WHERE path = %s", (canonical_path_str,))
        row = cur.fetchone()
        if row:
            return f"path={canonical_path_str} already taken by slug={row['slug']}"
    return None


def _do_rename(legacy: Path, canonical_slug: str, email: str) -> tuple[bool, str]:
    """FS-first rename + DB UPDATE + frontmatter normalize. Returns
    (success, reason)."""
    canonical = PEOPLE / f"{canonical_slug}.md"
    canonical_path_rel = f"wiki/people/{canonical_slug}.md"
    legacy_path_rel = f"wiki/people/{legacy.stem}.md"

    coll = _check_db_path_collision(canonical_slug, canonical_path_rel)
    if coll:
        return False, f"db collision: {coll}"

    # 1. FS rename. If this fails, nothing in DB has changed.
    try:
        legacy.rename(canonical)
    except OSError as exc:
        return False, f"fs rename failed: {exc}"

    # 2. DB UPDATE. If this fails, file is at canonical name — re-run
    # of script will detect (legacy file gone, canonical exists) and
    # the UPDATE-only path will heal.
    try:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE wiki_pages SET slug = %s, path = %s, updated_at = now() WHERE slug = %s",
                (canonical_slug, canonical_path_rel, legacy.stem),
            )
            # Some legacy slug rows might also have stored absolute paths
            # (per wiki-state-baseline observation that some paths are
            # absolute). Match by suffix to catch them.
            cur.execute(
                """UPDATE wiki_pages SET slug = %s, path = %s, updated_at = now()
                   WHERE slug = %s OR path LIKE %s""",
                (
                    canonical_slug,
                    canonical_path_rel,
                    legacy.stem,
                    f"%/{legacy_path_rel}",
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — DB error must surface, FS already changed
        return False, f"db update failed (FS already renamed): {exc}"

    # 3. Frontmatter normalize.
    _normalize_frontmatter(canonical, email)
    return True, "renamed"


def _do_merge(legacy: Path, canonical_slug: str) -> tuple[bool, str]:
    """Delegate to apply_merge_candidate.py. Returns (success, reason)."""
    args = [
        "uv",
        "run",
        "python",
        "scripts/apply_merge_candidate.py",
        "--pair",
        f"{legacy.stem},{canonical_slug}",
        "--keep",
        canonical_slug,
        "--commit",
    ]
    proc = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return False, f"merge failed: {proc.stderr.strip()[:200]}"
    return True, "merged"


@click.command()
@click.option("--commit", is_flag=True, help="Apply changes for real.")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show what would change.")
@click.option("--limit", type=int, default=0, help="Max operations (0=all)")
def main(commit: bool, dry_run: bool, limit: int) -> None:
    if commit == dry_run:
        click.echo("Pass exactly one of --commit / --dry-run.", err=True)
        sys.exit(2)

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    click.echo(f"=== run_id: {run_id} ===")
    click.echo("")

    click.echo("=== 1. classify ===")
    to_rename, to_merge, canonical, superseded, no_email = _classify()
    click.echo(f"  total people/ pages:    {len(list(PEOPLE.glob('*.md')))}")
    click.echo(f"  already canonical:      {len(canonical)}")
    click.echo(f"  to rename (no twin):    {len(to_rename)}")
    click.echo(f"  to merge (twin exists): {len(to_merge)}")
    click.echo(f"  superseded (skip):      {len(superseded)}")
    click.echo(f"  no/invalid email (skip):{len(no_email)}")
    if no_email:
        click.echo("    samples: " + ", ".join(p.stem for p in no_email[:5]))

    if commit:
        click.echo("")
        click.echo("=== 2. backup ===")
        backup_dir = _backup(run_id)
        click.echo(f"  backup at {backup_dir}/")

    if not commit:
        click.echo("")
        click.echo("=== 2. (dry-run, skip backup) ===")

    legacy_to_canonical_for_rewrite: dict[str, str] = {}

    click.echo("")
    click.echo(f"=== 3. {'execute' if commit else 'plan'} renames ({len(to_rename)}) ===")
    rename_done = rename_skip = 0
    rename_batch = to_rename if not limit else to_rename[:limit]
    for legacy, email, canonical_slug in rename_batch:
        if not commit:
            click.echo(f"  RENAME {legacy.stem:<35} -> {canonical_slug}")
            legacy_to_canonical_for_rewrite[legacy.stem] = canonical_slug
            continue
        ok, reason = _do_rename(legacy, canonical_slug, email)
        if ok:
            rename_done += 1
            legacy_to_canonical_for_rewrite[legacy.stem] = canonical_slug
            click.echo(f"  OK   {legacy.stem:<35} -> {canonical_slug}")
        else:
            rename_skip += 1
            click.echo(f"  SKIP {legacy.stem:<35} -> {canonical_slug}: {reason}", err=True)
    if commit:
        click.echo(f"  rename: {rename_done} done, {rename_skip} skipped")

    click.echo("")
    click.echo(f"=== 4. {'execute' if commit else 'plan'} merges ({len(to_merge)}) ===")
    merge_done = merge_skip = 0
    merge_batch = to_merge if not limit else to_merge[: max(0, limit - len(rename_batch))]
    for legacy, _email, canonical_slug in merge_batch:
        if not commit:
            click.echo(f"  MERGE  {legacy.stem:<35} -> {canonical_slug}")
            legacy_to_canonical_for_rewrite[legacy.stem] = canonical_slug
            continue
        ok, reason = _do_merge(legacy, canonical_slug)
        if ok:
            merge_done += 1
            legacy_to_canonical_for_rewrite[legacy.stem] = canonical_slug
            click.echo(f"  OK     {legacy.stem:<35} -> {canonical_slug}")
        else:
            merge_skip += 1
            click.echo(f"  SKIP   {legacy.stem:<35} -> {canonical_slug}: {reason}", err=True)
    if commit:
        click.echo(f"  merge: {merge_done} done, {merge_skip} skipped")

    click.echo("")
    click.echo("=== 5. final wikilink rewrite (idempotency heal) ===")
    if commit:
        files_touched, total_rewrites = _rewrite_wikilinks_for_slugs(
            legacy_to_canonical_for_rewrite
        )
        click.echo(f"  {files_touched} files touched, {total_rewrites} wikilinks rewritten")
    else:
        # Estimate by counting matches without writing.
        if legacy_to_canonical_for_rewrite:
            pat = re.compile(
                r"\[\[(?:"
                + "|".join(re.escape(k) for k in legacy_to_canonical_for_rewrite)
                + r")(?:\|[^\]]*)?\]\]"
            )
            estimate = 0
            for p in WIKI.rglob("*.md"):
                try:
                    estimate += len(pat.findall(p.read_text(encoding="utf-8")))
                except (OSError, UnicodeDecodeError):
                    continue
            click.echo(f"  ~{estimate} wikilinks to rewrite (estimate)")
        else:
            click.echo("  nothing to rewrite")

    click.echo("")
    click.echo("done." if commit else "(dry-run; pass --commit to apply)")


if __name__ == "__main__":
    main()
