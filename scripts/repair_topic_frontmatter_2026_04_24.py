"""One-shot repair for a handful of topic pages surfaced in the
2026-04-24 deep audit:

1. `centralized-gst-kyc-blocker-logic.md` — truncated `sources:` URL
   (split across two lines) + premature `---` frontmatter-close. The
   downstream `domains/trust-safety.md` summary extractor grabs raw YAML
   because of this.

2. `mcat-buyer-spec-fill-analysis.md` — legit markdown horizontal-rule
   (`---`) in the body confused the 3-divider detector. Replace with a
   blank separator so the page renders cleanly and isn't mistaken for a
   broken-frontmatter page.

3. Two zero-content topic pages:
   - `export-search-rating-and-price-ux.md` — literally one blank line.
   - `removing-download-brochure-company-pages.md` — empty `{}`
     frontmatter plus an orphan `References` block.

   Neither carries signal. Delete filesystem + DB catalog row so they
   don't show up in listings.

Lifecycle: one-shot — retire after 2026-05-24 (files are fixed or gone;
no re-entry expected).
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import connect  # noqa: E402


def fix_centralized_gst(path: Path) -> None:
    """Merge the truncated source URL, drop the premature frontmatter close,
    and fold `related:` into the frontmatter where it belongs.
    """
    # The line-by-line walker above was dead code (left from an earlier
    # draft). The actual fix is the deterministic string-replace below.
    text = path.read_text(encoding="utf-8")
    fixed = text.replace(
        "- raw/2026-04-06_launchim-mplaunchim-indiamart-android-app-1371\nlast_compiled:",
        "- raw/2026-04-06_launchim-mplaunchim-indiamart-android-app-1371"
        "c_6ed8f857.md\n"
        "last_compiled:",
    )
    # Remove the premature `---\n\nc_6ed8f857.md\n` sequence if it remained
    fixed = fixed.replace(
        "domain: trust-safety\n---\n\nc_6ed8f857.md\nrelated:\n",
        "domain: trust-safety\nrelated:\n",
    )
    path.write_text(fixed, encoding="utf-8")


def fix_mcat_buyer_spec(path: Path) -> None:
    """Replace every body horizontal rule `---` with a blank line so the
    3-divider summary-extractor heuristic stops misfiring.

    Post-review fix (Codex 2026-04-24): the earlier implementation was a
    silent no-op — splitting on `"\\n---\\n"` put both delimiters back
    into `head` during rejoin. Switched to a line-by-line pass that keeps
    the first two standalone-`---` lines (frontmatter open + close) and
    rewrites every subsequent one to a blank line. Tolerates `\\r\\n`
    and trailing whitespace on the delimiter line.
    """
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines: list[str] = []
    divider_count = 0
    for line in lines:
        if line.rstrip("\r\n").strip() == "---":
            divider_count += 1
            if divider_count <= 2:
                # Frontmatter open (1) + close (2) — preserve.
                new_lines.append(line)
            else:
                # Body horizontal rule — drop to blank.
                new_lines.append("\n")
        else:
            new_lines.append(line)
    path.write_text("".join(new_lines), encoding="utf-8")


@click.command()
@click.option("--dry-run", is_flag=True, help="Preview without writing.")
def main(dry_run: bool) -> None:
    wiki = REPO_ROOT / "wiki"
    fixes = []
    deletes = []

    p = wiki / "topics" / "centralized-gst-kyc-blocker-logic.md"
    if p.exists() and p.read_text(encoding="utf-8").count("\n---\n") == 2:
        # Still broken (2 `\n---\n` splits => 3 dividers with BOM-adjustment)
        fixes.append(("centralized-gst", p, fix_centralized_gst))

    p = wiki / "topics" / "mcat-buyer-spec-fill-analysis.md"
    if p.exists():
        # Count standalone-`---` lines; run only if there are >2 (i.e. a body
        # HR beyond the frontmatter open + close).
        divider_count = sum(
            1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip() == "---"
        )
        if divider_count > 2:
            fixes.append(("mcat-buyer-spec", p, fix_mcat_buyer_spec))

    for name in (
        "export-search-rating-and-price-ux",
        "removing-download-brochure-company-pages",
    ):
        p = wiki / "topics" / f"{name}.md"
        if p.exists():
            deletes.append((name, p))

    click.echo(f"fixes: {[n for n, _, _ in fixes]}")
    click.echo(f"deletes: {[n for n, _ in deletes]}")

    if dry_run:
        return

    for _, path, fn in fixes:
        fn(path)
        click.echo(f"✓ fixed {path.relative_to(REPO_ROOT)}")

    with connect() as conn:
        for slug, path in deletes:
            path.unlink()
            conn.execute("DELETE FROM wiki_pages WHERE slug = %s", (slug,))
            click.echo(f"✓ deleted {path.relative_to(REPO_ROOT)} + catalog row")
        conn.commit()


if __name__ == "__main__":
    main()
