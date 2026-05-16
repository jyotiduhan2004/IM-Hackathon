"""Index wiki/ pages into Chroma for semantic search.

Usage:
    uv run python scripts/index_wiki.py
    uv run python scripts/index_wiki.py --wiki-dir wiki
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import click

from src.query.indexer import build_chroma_index, build_wiki_tree, iter_wiki_pages


@click.command()
@click.option("--wiki-dir", default="wiki", help="Path to wiki directory")
@click.option("--persist-dir", default=".chroma_index", help="Path to Chroma persistence dir")
def main(wiki_dir: str, persist_dir: str) -> None:
    """Build Chroma index from wiki pages (split by H2 sections)."""
    wiki_path = Path(wiki_dir)
    persist_path = Path(persist_dir)

    if not wiki_path.exists():
        click.echo(f"Error: wiki directory '{wiki_path}' does not exist", err=True)
        sys.exit(1)

    pages = iter_wiki_pages(wiki_path)
    click.echo(f"Found {len(pages)} active wiki pages")

    for p in pages:
        click.echo(f"  [{p['category']}] {p['title']} — {len(p['sections'])} sections")

    collection = build_chroma_index(wiki_path, persist_path)
    click.echo(f"\nChroma index built at {persist_path}/")
    click.echo(f"Collection: {collection.name}, {collection.count()} documents")

    click.echo("\nBuilding wiki_tree.json...")
    build_wiki_tree(wiki_path)
    click.echo("Done. Ready for queries.")


if __name__ == "__main__":
    main()
