"""Single-shot audit of the wiki + catalog.

Combines what was previously spread across stats.py, dipstick.py, and
validate_wiki into one report with a stable section layout, so reports
diff usefully across compile batches.

Usage:
    uv run python scripts/audit.py                 # print to stdout
    uv run python scripts/audit.py --save          # also save to docs/audits/
    uv run python scripts/audit.py --quiet         # only print the path saved
"""

from __future__ import annotations

import random
import re
import sys
from datetime import UTC
from datetime import datetime
from io import StringIO
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.db.messages import count_by_state  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402

CATEGORIES = ("topics", "entities", "systems", "policies", "timelines", "conflicts")
STUB_BYTES = 500
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
SOURCE_RAWPATH_RE = re.compile(r"raw/[\w./-]+\.md")


def _rel_to_repo(p: Path) -> str:
    """Stable repo-relative path. Handles paths that are already relative
    (e.g. settings.wiki_dir defaults to Path('wiki'))."""
    try:
        return str(p.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def _page_paths() -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for cat in CATEGORIES:
        d = settings.wiki_dir / cat
        out[cat] = sorted(d.glob("*.md")) if d.exists() else []
    return out


def _section_state(buf: StringIO) -> None:
    counts = count_by_state()
    total = sum(counts.values())
    buf.write("## Catalog state\n\n")
    buf.write(f"Total messages: **{total}**\n\n")
    for state in ("pending", "claimed", "compiled", "failed"):
        n = counts.get(state, 0)
        pct = (100 * n / total) if total else 0.0
        buf.write(f"- {state}: **{n}** ({pct:.1f}%)\n")
    buf.write("\n")


def _section_pages(buf: StringIO, pages: dict[str, list[Path]]) -> dict[str, dict]:
    """Per-category counts + body-size stats. Returns a metrics dict for reuse."""
    buf.write("## Pages by category\n\n")
    buf.write("| Category | Count | Stubs (<500B) | Avg body (B) | Median (B) |\n")
    buf.write("|---|---:|---:|---:|---:|\n")
    metrics: dict[str, dict] = {}
    for cat, paths in pages.items():
        sizes: list[int] = []
        stubs = 0
        for p in paths:
            try:
                body = extract_body(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            sizes.append(len(body.encode("utf-8")))
            if len(body.encode("utf-8")) < STUB_BYTES:
                stubs += 1
        avg = (sum(sizes) // len(sizes)) if sizes else 0
        median = (sorted(sizes)[len(sizes) // 2]) if sizes else 0
        metrics[cat] = {"count": len(paths), "stubs": stubs, "avg": avg, "median": median}
        buf.write(f"| {cat} | {len(paths)} | {stubs} | {avg:,} | {median:,} |\n")
    total_pages = sum(m["count"] for m in metrics.values())
    total_stubs = sum(m["stubs"] for m in metrics.values())
    pct = 100 * total_stubs / total_pages if total_pages else 0.0
    buf.write(f"\n**Stub rate**: {total_stubs}/{total_pages} ({pct:.1f}%)\n\n")
    return metrics


def _section_orphans(buf: StringIO, pages: dict[str, list[Path]]) -> None:
    """Count pages with no incoming wikilinks anywhere in the wiki."""
    all_pages = [p for paths in pages.values() for p in paths]
    incoming: dict[str, int] = {p.stem: 0 for p in all_pages}
    for p in all_pages:
        try:
            content = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for target in WIKILINK_RE.findall(content):
            target_stem = target.strip().split("|")[0].strip()
            if target_stem in incoming and target_stem != p.stem:
                incoming[target_stem] += 1
    orphans_by_cat: dict[str, int] = dict.fromkeys(CATEGORIES, 0)
    for p in all_pages:
        if incoming[p.stem] == 0:
            orphans_by_cat[p.parent.name] = orphans_by_cat.get(p.parent.name, 0) + 1
    total_orphans = sum(orphans_by_cat.values())
    total_pages = len(all_pages)
    pct = 100 * total_orphans / total_pages if total_pages else 0.0
    buf.write(f"## Orphans (no incoming wikilinks): **{total_orphans}** ({pct:.1f}%)\n\n")
    for cat, n in orphans_by_cat.items():
        if n:
            buf.write(f"- {cat}: {n}\n")
    buf.write("\n")


def _section_validator(buf: StringIO) -> None:
    """Reuse validate_wiki's checks; report counts + first 10 errors."""
    from scripts import validate_wiki  # noqa: PLC0415

    errors = validate_wiki.run(settings.wiki_dir)
    buf.write(f"## Validator: **{len(errors)}** error(s)\n\n")
    if not errors:
        buf.write("Wiki is clean.\n\n")
        return
    by_kind: dict[str, int] = {}
    for e in errors:
        kind = e.reason.split(":", 1)[0].split("(")[0].strip()
        by_kind[kind] = by_kind.get(kind, 0) + 1
    buf.write("| Kind | Count |\n|---|---:|\n")
    for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        buf.write(f"| {kind} | {n} |\n")
    buf.write("\nFirst 10:\n\n")
    for e in errors[:10]:
        rel = _rel_to_repo(e.page)
        buf.write(f"- `{rel}`: {e.reason}\n")
    buf.write("\n")


def _section_spot_check(buf: StringIO, pages: dict[str, list[Path]], n: int = 5) -> None:
    """Sample a few random pages and surface their key facts for human review."""
    all_pages = [p for paths in pages.values() for p in paths]
    if not all_pages:
        buf.write("## Spot check\n\nNo pages.\n\n")
        return
    sample = random.sample(all_pages, min(n, len(all_pages)))
    buf.write(f"## Spot check ({len(sample)} random pages)\n\n")
    for p in sample:
        try:
            content = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = extract_frontmatter(content)
        body = extract_body(content)
        sources = fm.get("sources") or []
        wikilinks = WIKILINK_RE.findall(content)
        buf.write(
            f"### `{_rel_to_repo(p)}`\n"
            f"- title: {fm.get('title', '(missing)')!r}\n"
            f"- status: {fm.get('status', '(missing)')}\n"
            f"- body: {len(body.encode('utf-8'))} B\n"
            f"- sources: {len(sources)}\n"
            f"- outgoing wikilinks: {len(wikilinks)}\n"
            f"- last_compiled: {fm.get('last_compiled', '(missing)')}\n"
            f"- update_count: {fm.get('update_count', 0)}\n\n"
        )


@click.command()
@click.option("--save", is_flag=True, help="Also save to docs/audits/")
@click.option("--quiet", is_flag=True, help="Only print the saved path (implies --save)")
@click.option("--note", default="", help="Free-form note attached to the report header")
def main(save: bool, quiet: bool, note: str) -> None:
    if quiet:
        save = True

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    buf = StringIO()
    buf.write(f"# Audit — {ts}\n\n")
    if note:
        buf.write(f"_Note: {note}_\n\n")

    pages = _page_paths()
    _section_state(buf)
    _section_pages(buf, pages)
    _section_orphans(buf, pages)
    _section_validator(buf)
    _section_spot_check(buf, pages, n=5)

    report = buf.getvalue()
    out_path: Path | None = None
    if save:
        audits_dir = REPO_ROOT / "docs" / "audits"
        audits_dir.mkdir(parents=True, exist_ok=True)
        out_path = audits_dir / f"audit-{ts}.md"
        out_path.write_text(report, encoding="utf-8")

    if quiet:
        click.echo(str(out_path))
    else:
        click.echo(report)
        if out_path:
            click.echo(f"\n→ saved: {out_path.relative_to(REPO_ROOT)}", err=True)


if __name__ == "__main__":
    main()
