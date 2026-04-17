"""Unit tests for scripts/consolidate_duplicate_slug.py pure helpers.

End-to-end (DB + FS) is covered by the --dry-run smoke in the PR.
These tests pin the pure-function behaviour that the smoke can't
assert cheaply."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "consolidate_duplicate_slug",
        REPO_ROOT / "scripts" / "consolidate_duplicate_slug.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["consolidate_duplicate_slug"] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


class TestMergeBodies:
    def test_keeps_winner_frontmatter_drops_loser_frontmatter(self) -> None:
        winner = "---\ntitle: Winner\nslug: winner\n---\n\nWinner body.\n"
        loser = "---\ntitle: Loser\nslug: loser\n---\n\nLoser body.\n"
        merged = script._merge_bodies(winner, loser)
        assert merged.startswith("---\ntitle: Winner\nslug: winner\n---\n")
        # Loser frontmatter dropped
        assert "title: Loser" not in merged
        assert "slug: loser" not in merged
        # Both bodies preserved
        assert "Winner body." in merged
        assert "Loser body." in merged

    def test_handles_missing_frontmatter_on_either_side(self) -> None:
        merged = script._merge_bodies("raw winner\n", "raw loser\n")
        assert "raw winner" in merged
        assert "raw loser" in merged

    def test_no_double_blank_line_between_bodies(self) -> None:
        winner = "---\ntitle: W\n---\n\nLine one.\n\n"
        loser = "---\ntitle: L\n---\n\n\n\nLine two.\n"
        merged = script._merge_bodies(winner, loser)
        # Exactly two newlines between the merged bodies (one blank line).
        assert "Line one.\n\nLine two." in merged
        assert "Line one.\n\n\nLine two." not in merged


class TestRewriteWikilinks:
    def test_rewrites_plain_and_piped_forms(self, tmp_path: Path) -> None:
        path = tmp_path / "page.md"
        path.write_text(
            "See [[OldSlug]] and [[OldSlug|alias text]] plus [[NotAffected]].\n",
            encoding="utf-8",
        )
        n = script._rewrite_wikilinks(path, "OldSlug", "new-slug")
        assert n == 2
        written = path.read_text(encoding="utf-8")
        assert "[[new-slug]]" in written
        assert "[[new-slug|alias text]]" in written
        assert "[[NotAffected]]" in written
        # Old slug is entirely gone.
        assert "[[OldSlug" not in written

    def test_preserves_pre_existing_winner_links(self, tmp_path: Path) -> None:
        path = tmp_path / "page.md"
        path.write_text("Links: [[new-slug]] and [[OldSlug]].\n", encoding="utf-8")
        n = script._rewrite_wikilinks(path, "OldSlug", "new-slug")
        assert n == 1
        # Count should show both now point at the winner.
        assert path.read_text(encoding="utf-8").count("[[new-slug]]") == 2

    def test_case_sensitive_match(self, tmp_path: Path) -> None:
        # The loser's slug may have casing the wiki doesn't elsewhere
        # use (Lens.IndiaMART). Lowercase variant must not be touched.
        path = tmp_path / "page.md"
        path.write_text("[[Lens.IndiaMART]] and [[lens.indiamart]].\n", encoding="utf-8")
        n = script._rewrite_wikilinks(path, "Lens.IndiaMART", "lens-indiamart-com")
        assert n == 1
        written = path.read_text(encoding="utf-8")
        assert "[[lens-indiamart-com]]" in written
        assert "[[lens.indiamart]]" in written  # untouched


class TestFindWikilinkRefs:
    def test_finds_only_files_that_contain_the_slug(self, tmp_path: Path) -> None:
        (tmp_path / "systems").mkdir()
        (tmp_path / "topics").mkdir()
        (tmp_path / "systems" / "match.md").write_text("see [[TargetSlug]]", encoding="utf-8")
        (tmp_path / "topics" / "piped.md").write_text("see [[TargetSlug|named]]", encoding="utf-8")
        (tmp_path / "topics" / "no-match.md").write_text("see [[other]]", encoding="utf-8")
        (tmp_path / "topics" / "suffix-collision.md").write_text(
            "see [[TargetSlugExtra]]", encoding="utf-8"
        )

        refs = script._find_wikilink_refs(tmp_path, "TargetSlug")
        names = {p.name for p in refs}
        assert names == {"match.md", "piped.md"}, names
