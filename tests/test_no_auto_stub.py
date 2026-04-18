"""Regression tests for v10-U8 — auto-stub for unresolved wikilinks is dropped.

Before v10-U8 the `scripts/lint_wiki.py::create_missing_stubs` function walked
every page, pulled out unresolved `[[target]]` wikilinks, and wrote stub pages
at `wiki/entities/<target>.md` or `wiki/systems/<target>.md` based on a
slug-shape heuristic. That path is how garbage slugs (``vishakha-indiamart``,
``akash-singh6``, ``arjun-gaur-clean``) entered the catalog — the invented
slug replaced the canonical email-derived slug that `create_entities()`
produces.

These tests pin three invariants:

1. The `create_missing_stubs` function is gone (no accidental re-import).
2. The `--create-stubs` CLI flag is gone.
3. Running the post-batch coordinator hooks on a wiki with a broken
   `[[people/...]]` wikilink leaves no file on disk at that target —
   the broken link stays broken so the reviewer can surface it.

`_rebuild_person_backlinks` is left alone: it only refreshes the
"Appears in" section of person pages that ALREADY exist. These tests
assert that property too, so a future refactor that turns it into a
creator trips the regression.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml
from src.compile.compiler import _rebuild_person_backlinks
from src.compile.compiler import rebuild_landing_pages


def _write_page(path: Path, frontmatter: dict[str, object], body: str) -> None:
    """Minimal helper — write a wiki page with YAML frontmatter + body."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False).rstrip()
    path.write_text(f"---\n{fm_text}\n---\n\n{body}", encoding="utf-8")


def test_create_missing_stubs_is_gone() -> None:
    """Importing `scripts.lint_wiki` must not expose `create_missing_stubs`.

    v10-U8 removed the function entirely — keeping only a comment pointer so
    anyone searching the codebase understands why the auto-stub path is gone.
    """
    module = importlib.import_module("scripts.lint_wiki")
    assert not hasattr(module, "create_missing_stubs"), (
        "create_missing_stubs must stay removed (v10-U8, GH #12). "
        "Use create_entities() for person pages."
    )


def test_create_stubs_cli_flag_is_gone() -> None:
    """`lint_wiki.py` CLI must reject `--create-stubs` with Click's standard
    usage-error exit code (2)."""
    from click.testing import CliRunner

    module = importlib.import_module("scripts.lint_wiki")
    result = CliRunner().invoke(module.main, ["--create-stubs", "--fix"])
    assert result.exit_code == 2
    assert "No such option: --create-stubs" in result.output


def test_post_batch_hooks_do_not_stub_broken_people_wikilink(
    mini_wiki: Path,
) -> None:
    """Post-batch rebuild leaves a broken `[[people/...]]` wikilink unresolved.

    Before v10-U8 `make lint-wiki-fix` (via `create_missing_stubs`) would
    materialize `wiki/entities/vishakha-indiamart.md` when a topic referenced
    `[[people/vishakha-indiamart]]`. The function is gone now; no other
    coordinator hook is allowed to write to `wiki/people/` on behalf of an
    unresolved link.
    """
    _write_page(
        mini_wiki / "topics" / "notif-frequency.md",
        {
            "title": "Notification Frequency",
            "page_type": "topic",
            "status": "active",
        },
        "Discussion led by [[people/vishakha-indiamart]] on cap rollout.\n",
    )

    rebuild_landing_pages(str(mini_wiki))

    assert not (mini_wiki / "people" / "vishakha-indiamart.md").exists()
    assert not (mini_wiki / "entities" / "vishakha-indiamart.md").exists()


def test_rebuild_person_backlinks_does_not_create_missing_person(
    mini_wiki: Path,
) -> None:
    """`_rebuild_person_backlinks` must only touch EXISTING person pages.

    Guard against a future refactor quietly turning this into an on-miss
    creator — which would reintroduce exactly the bug v10-U8 fixes.
    """
    _write_page(
        mini_wiki / "topics" / "buylead-quality.md",
        {"title": "BuyLead Quality", "page_type": "topic", "status": "active"},
        "Owned by [[people/akash-singh6]].\n",
    )

    written = _rebuild_person_backlinks(mini_wiki)

    assert written == 0
    assert not (mini_wiki / "people" / "akash-singh6.md").exists()


def test_rebuild_person_backlinks_updates_existing_person(mini_wiki: Path) -> None:
    """Sanity: the generator still refreshes backlinks on EXISTING pages.

    v10-U8 must NOT regress the legitimate path — a topic that wikilinks
    to a person page already on disk should still get its "Appears in"
    section updated.
    """
    _write_page(
        mini_wiki / "people" / "amit-indiamart-com.md",
        {
            "title": "Amit",
            "page_type": "person",
            "status": "active",
            "email": "amit@indiamart.com",
        },
        "Owner of the compile pipeline.\n",
    )
    _write_page(
        mini_wiki / "topics" / "compile-pipeline.md",
        {"title": "Compile Pipeline", "page_type": "topic", "status": "active"},
        "Maintained by [[amit-indiamart-com]].\n",
    )

    written = _rebuild_person_backlinks(mini_wiki)

    assert written == 1
    refreshed = (mini_wiki / "people" / "amit-indiamart-com.md").read_text(encoding="utf-8")
    assert "## Appears in" in refreshed
    assert "[[compile-pipeline]]" in refreshed


def test_broken_wikilink_still_flagged_by_validator(mini_wiki: Path) -> None:
    """Reviewer / validator UX — broken wikilink surfaces even with no auto-stub.

    `scripts/validate_wiki.py::check_broken_wikilinks` is the fail-hard
    validator that catches broken links. With the auto-stub path gone, it
    must still flag `[[people/...]]` references that don't resolve so the
    agent knows to call `create_entities()`.
    """
    validate_wiki = importlib.import_module("scripts.validate_wiki")

    _write_page(
        mini_wiki / "topics" / "buylead-quality.md",
        {"title": "BuyLead Quality", "page_type": "topic", "status": "active"},
        "Owned by [[people/akash-singh6]].\n",
    )

    errors = validate_wiki.check_broken_wikilinks(mini_wiki)

    assert errors, "check_broken_wikilinks must flag unresolved wikilinks"
    assert any("akash-singh6" in err.reason for err in errors)


# Parametrised guard so a future `_regenerate_<X>_stubs` helper tripping the
# "create on broken [[people/...]] link" anti-pattern fails this too.
@pytest.mark.parametrize(
    "directory",
    ["people", "entities"],
)
def test_broken_people_link_never_materializes(mini_wiki: Path, directory: str) -> None:
    _write_page(
        mini_wiki / "topics" / "random.md",
        {"title": "Random", "page_type": "topic", "status": "active"},
        "CC: [[people/arjun-gaur-clean]].\n",
    )
    rebuild_landing_pages(str(mini_wiki))
    assert not (mini_wiki / directory / "arjun-gaur-clean.md").exists()
