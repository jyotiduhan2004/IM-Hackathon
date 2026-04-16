"""Unit tests for scripts/migrate_entities_to_people.py.

Covers the three cases the migration has to get right:

1. **Planning** against a synthetic wiki — the dry-run plan enumerates
   the correct moves, counts the correct wikilink + markdown-link
   rewrites, and doesn't touch disk.
2. **Commit** — files move, references are rewritten, DB rows flip
   ``entity`` → ``person``, paths update.
3. **Idempotency** — running ``--commit`` twice is a no-op on the
   second run.
4. **Collision** — if ``people/slug.md`` already exists, the move is
   skipped and reported.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import psycopg
import pytest
from click.testing import CliRunner

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_migration_module():  # type: ignore[no-untyped-def]
    """Import scripts/migrate_entities_to_people.py directly.

    Matches the pattern used by test_fix_broken_wikilinks — the
    scripts/ directory isn't on PYTHONPATH so we load the file
    explicitly. Running this at import time (like test_audit_systems)
    keeps per-test setup cheap.
    """
    spec = importlib.util.spec_from_file_location(
        "migrate_entities_to_people",
        REPO_ROOT / "scripts" / "migrate_entities_to_people.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_entities_to_people"] = module
    spec.loader.exec_module(module)
    return module


migrate = _load_migration_module()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write(path: Path, body: str) -> None:
    """Write ``body`` to ``path``, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _entity_page(slug: str, email: str) -> str:
    """Minimal entity-page body with frontmatter — enough for the migration."""
    return (
        f"---\n"
        f"title: {slug.replace('-', ' ').title()}\n"
        f"page_type: entity\n"
        f"status: current\n"
        f"email: {email}\n"
        f"---\n\n"
        f"# {slug}\n\nStub body.\n"
    )


@pytest.fixture
def synthetic_wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a tiny wiki with 3 entity pages + 1 topic page that links them.

    Returns the wiki/ root path. Also patches the migration module's
    REPO_ROOT so the dry-run plan file lands in tmp_path (not the real
    repo) — otherwise every test run would leave artifacts behind.
    """
    wiki = tmp_path / "wiki"
    _write(wiki / "entities" / "alice.md", _entity_page("alice", "alice@example.com"))
    _write(wiki / "entities" / "bob.md", _entity_page("bob", "bob@example.com"))
    _write(wiki / "entities" / "carol.md", _entity_page("carol", "carol@example.com"))
    # index.md + .gitkeep are placeholders the migration must skip.
    _write(wiki / "entities" / "index.md", "---\ntitle: Entities\n---\n\nindex\n")
    _write(wiki / "entities" / ".gitkeep", "")
    # Topic page with one of each reference form.
    _write(
        wiki / "topics" / "project-x.md",
        (
            "---\ntitle: Project X\npage_type: topic\nstatus: current\n---\n\n"
            "# Project X\n\n"
            "Owned by [[entities/alice]] with [[entities/bob|Bob]] as backup.\n\n"
            "See also [Carol's page](entities/carol.md) and [[carol]] directly.\n"
        ),
    )
    # Top-level home.md — should also be scanned + rewritten.
    _write(
        wiki / "home.md",
        ("---\ntitle: Home\npage_type: home\n---\n\nPing [[entities/alice]] for context.\n"),
    )
    # Pin REPO_ROOT for the plan-file path so we don't pollute the real repo.
    monkeypatch.setattr(migrate, "REPO_ROOT", tmp_path)
    return wiki


def _seed_catalog(db_conn: psycopg.Connection, wiki: Path) -> None:
    """Insert matching ``wiki_pages`` rows for the three synthetic entities."""
    from src.db import users as users_repo
    from src.db import wiki_pages as repo

    for slug in ("alice", "bob", "carol"):
        email = f"{slug}@example.com"
        users_repo.upsert_user(db_conn, email=email, display_name=slug.title())
        repo.upsert_wiki_page(
            db_conn,
            slug=slug,
            path=str(wiki / "entities" / f"{slug}.md"),
            title=slug.title(),
            page_type="entity",
            status="current",
            canonical_user_email=email,
        )
    db_conn.commit()


# ---------------------------------------------------------------------------
# Plan construction (dry-run semantics)
# ---------------------------------------------------------------------------


def test_plan_enumerates_real_entity_pages_only(
    synthetic_wiki: Path, db_conn: psycopg.Connection
) -> None:
    """index.md and .gitkeep must not be planned for move."""
    _seed_catalog(db_conn, synthetic_wiki)
    plan = migrate._build_plan(synthetic_wiki)
    moved_names = sorted(m.src.name for m in plan.moves)
    assert moved_names == ["alice.md", "bob.md", "carol.md"]
    assert all("index" not in m.src.name for m in plan.moves)


def test_plan_counts_wikilink_rewrites(synthetic_wiki: Path, db_conn: psycopg.Connection) -> None:
    """The topic page has 2 `[[entities/X]]` wikilinks; home.md has 1. Total 3."""
    _seed_catalog(db_conn, synthetic_wiki)
    plan = migrate._build_plan(synthetic_wiki)
    assert plan.wikilink_total == 3


def test_plan_counts_markdown_rewrites(synthetic_wiki: Path, db_conn: psycopg.Connection) -> None:
    """The topic page has one `[...](entities/carol.md)` — that's the markdown total."""
    _seed_catalog(db_conn, synthetic_wiki)
    plan = migrate._build_plan(synthetic_wiki)
    assert plan.markdown_total == 1


def test_plan_counts_catalog_rows(synthetic_wiki: Path, db_conn: psycopg.Connection) -> None:
    _seed_catalog(db_conn, synthetic_wiki)
    plan = migrate._build_plan(synthetic_wiki)
    assert plan.catalog_rows == 3


def test_noop_when_no_entities(
    tmp_path: Path, db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty wiki + empty catalog → is_noop is True."""
    monkeypatch.setattr(migrate, "REPO_ROOT", tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    plan = migrate._build_plan(wiki)
    assert plan.is_noop


def test_not_noop_when_only_skips_present(
    tmp_path: Path, db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All-skips plan (every entity blocked by a collision) is NOT noop —
    the operator needs to see the skip list rather than a misleading
    'nothing to migrate' message.
    """
    monkeypatch.setattr(migrate, "REPO_ROOT", tmp_path)
    wiki = tmp_path / "wiki"
    # One entity source + a pre-existing collision target for it.
    _write(wiki / "entities" / "alice.md", _entity_page("alice", "alice@example.com"))
    _write(
        wiki / "people" / "alice.md",
        "---\ntitle: Alice\npage_type: person\n---\n\nexisting\n",
    )
    plan = migrate._build_plan(wiki)
    # No moves (collision), no rewrites (no content links), but skips is
    # populated — the plan must not report noop.
    assert plan.moves == []
    assert plan.rewrites == []
    assert plan.skips
    assert not plan.is_noop


# ---------------------------------------------------------------------------
# Execute phase: moves
# ---------------------------------------------------------------------------


def test_execute_moves_files(synthetic_wiki: Path, db_conn: psycopg.Connection) -> None:
    """After _execute_moves, the 3 entity pages land under people/."""
    plan = migrate._build_plan(synthetic_wiki)
    moved, skipped = migrate._execute_moves(plan.moves)
    assert moved == 3
    assert skipped == []
    for slug in ("alice", "bob", "carol"):
        assert (synthetic_wiki / "people" / f"{slug}.md").exists()
        assert not (synthetic_wiki / "entities" / f"{slug}.md").exists()


def test_execute_moves_skips_collision(synthetic_wiki: Path, db_conn: psycopg.Connection) -> None:
    """If people/alice.md already exists, the move is skipped + reported."""
    # Pre-create the collision target.
    _write(
        synthetic_wiki / "people" / "alice.md",
        "---\ntitle: Alice\npage_type: person\n---\n\nexisting\n",
    )
    plan = migrate._build_plan(synthetic_wiki)
    moved_names = sorted(m.src.name for m in plan.moves)
    # alice is NOT in moves — she's in skips.
    assert moved_names == ["bob.md", "carol.md"]
    skip_names = sorted(s.path.name for s in plan.skips)
    assert "alice.md" in skip_names
    assert any("destination exists" in s.reason for s in plan.skips)


def test_execute_rewrites_apply_changes(synthetic_wiki: Path, db_conn: psycopg.Connection) -> None:
    """After rewrites, the topic page points at people/, not entities/."""
    plan = migrate._build_plan(synthetic_wiki)
    # No moves executed in this test → empty moved_dsts; every rewrite
    # uses the precomputed plan-phase buffer.
    wl, md = migrate._execute_rewrites(plan.rewrites, set())
    assert wl == 3  # 3 wikilinks across topic + home
    assert md == 1  # 1 markdown link in topic
    topic = (synthetic_wiki / "topics" / "project-x.md").read_text(encoding="utf-8")
    assert "[[people/alice]]" in topic
    assert "[[people/bob|Bob]]" in topic
    assert "(people/carol.md)" in topic
    # Bare [[carol]] stays untouched — already valid.
    assert "[[carol]]" in topic
    assert "entities/" not in topic
    home = (synthetic_wiki / "home.md").read_text(encoding="utf-8")
    assert "[[people/alice]]" in home
    assert "entities/" not in home


def test_moved_entity_keeps_frontmatter_and_rewrites_links(
    synthetic_wiki: Path, db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migrated entity page that references another entity: after a full
    ``--commit`` run, the moved file must end up with BOTH the rewritten
    frontmatter (``page_type: person``) AND the rewritten wikilink
    (``[[people/b]]``).

    Regression guard for Codex P1 on ``_execute_rewrites``: pre-fix, the
    plan-phase buffer was computed against the pre-move body (still
    ``page_type: entity`` in frontmatter), ``_execute_moves`` rewrote
    the frontmatter on disk, then ``_execute_rewrites`` wrote the stale
    plan-phase buffer on top of the moved file — clobbering the
    frontmatter fix. The fix re-reads from disk for moved files and
    applies the link regex fresh.
    """
    monkeypatch.setattr(migrate.settings, "wiki_dir", synthetic_wiki)
    # a (= alice) has both: (i) page_type: entity in frontmatter, (ii)
    # body link [[entities/b]] (= bob). The migration must flip both.
    alice_path = synthetic_wiki / "entities" / "alice.md"
    alice_path.write_text(
        _entity_page("alice", "alice@example.com") + "\nWorks closely with [[entities/bob]].\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(migrate.main, ["--commit"])
    assert result.exit_code == 0, result.output
    moved = (synthetic_wiki / "people" / "alice.md").read_text(encoding="utf-8")
    # (i) frontmatter flipped — NOT overwritten by the stale pre-move buffer.
    assert "page_type: person" in moved
    assert "page_type: entity" not in moved
    # (ii) wikilink in body rewritten — the fresh regex pass on disk
    # content catches the [[entities/bob]] reference.
    assert "[[people/bob]]" in moved
    assert "entities/" not in moved


def test_entity_to_entity_refs_survive_move(
    synthetic_wiki: Path, db_conn: psycopg.Connection
) -> None:
    """Entity page that references another entity: the rewrite must land
    at the POST-move people/ path, not the pre-move entities/ path.

    Regression guard: pre-fix, the rewrite phase read rw.path which
    pointed at entities/alice.md, but that file had already been
    renamed to people/alice.md — so the intra-entity link was silently
    left unrewritten.
    """
    # Add an entity-to-entity reference to alice's page.
    alice_path = synthetic_wiki / "entities" / "alice.md"
    alice_path.write_text(
        _entity_page("alice", "alice@example.com") + "\nWorks closely with [[entities/bob]].\n",
        encoding="utf-8",
    )
    # Full commit path: build plan → execute in order.
    plan = migrate._build_plan(synthetic_wiki)
    migrate._execute_moves(plan.moves)
    moved_dsts = {m.dst for m in plan.moves}
    migrate._execute_rewrites(plan.rewrites, moved_dsts)
    # Alice lives at people/alice.md now and her link to bob flipped.
    alice_new = (synthetic_wiki / "people" / "alice.md").read_text(encoding="utf-8")
    assert "[[people/bob]]" in alice_new
    assert "entities/" not in alice_new


# ---------------------------------------------------------------------------
# Execute phase: DB
# ---------------------------------------------------------------------------


def test_execute_catalog_updates_rows(synthetic_wiki: Path, db_conn: psycopg.Connection) -> None:
    """UPDATE flips page_type to 'person' and fixes path."""
    _seed_catalog(db_conn, synthetic_wiki)
    # Sanity-check pre-state.
    pre = db_conn.execute(
        "SELECT COUNT(*) AS n FROM wiki_pages WHERE page_type = 'entity'"
    ).fetchone()
    assert pre is not None
    assert pre["n"] == 3

    updated, failed = migrate._execute_catalog_update()
    assert updated == 3
    assert failed == 0

    post = db_conn.execute("SELECT slug, page_type, path FROM wiki_pages ORDER BY slug").fetchall()
    assert all(r["page_type"] == "person" for r in post)
    assert all("wiki/people/" in r["path"] for r in post)
    assert not any("wiki/entities/" in r["path"] for r in post)


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------


def test_cli_dry_run_writes_plan_file(
    synthetic_wiki: Path, db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run saves docs/audits/migration-plan-<ISO>.md + touches nothing."""
    _seed_catalog(db_conn, synthetic_wiki)
    monkeypatch.setattr(migrate.settings, "wiki_dir", synthetic_wiki)

    runner = CliRunner()
    result = runner.invoke(migrate.main, ["--dry-run"])
    assert result.exit_code == 0, result.output
    # Files still in entities/, not in people/.
    assert (synthetic_wiki / "entities" / "alice.md").exists()
    assert not (synthetic_wiki / "people" / "alice.md").exists()
    # Plan file landed under the patched REPO_ROOT (= tmp_path).
    plan_files = list((migrate.REPO_ROOT / "docs" / "audits").glob("migration-plan-*.md"))
    assert len(plan_files) == 1
    body = plan_files[0].read_text(encoding="utf-8")
    assert "Files to move: **3**" in body
    assert "wikilinks: **3**" in body


def test_cli_commit_performs_migration(
    synthetic_wiki: Path, db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--commit moves files, rewrites links, updates DB, exits 0."""
    _seed_catalog(db_conn, synthetic_wiki)
    monkeypatch.setattr(migrate.settings, "wiki_dir", synthetic_wiki)

    runner = CliRunner()
    result = runner.invoke(migrate.main, ["--commit"])
    assert result.exit_code == 0, result.output
    # Files moved.
    for slug in ("alice", "bob", "carol"):
        assert (synthetic_wiki / "people" / f"{slug}.md").exists()
        assert not (synthetic_wiki / "entities" / f"{slug}.md").exists()
    # Frontmatter page_type flipped on every moved file (P1 fix —
    # otherwise validators flag every migrated page as a directory/
    # type mismatch).
    for slug in ("alice", "bob", "carol"):
        body = (synthetic_wiki / "people" / f"{slug}.md").read_text(encoding="utf-8")
        assert "page_type: person" in body
        assert "page_type: entity" not in body
    # Links rewritten.
    topic = (synthetic_wiki / "topics" / "project-x.md").read_text(encoding="utf-8")
    assert "entities/" not in topic
    assert "[[people/alice]]" in topic
    # DB updated.
    row = db_conn.execute(
        "SELECT COUNT(*) AS n FROM wiki_pages WHERE page_type = 'entity'"
    ).fetchone()
    assert row is not None
    assert row["n"] == 0
    # Output mentions the move count + both rewrite counts (markdown
    # links surfaced separately so operators don't mistake the
    # wikilinks figure for the total).
    assert "entity_pages_moved:" in result.output
    assert "wikilinks_rewritten:" in result.output
    assert "markdown_links_rewritten:" in result.output
    assert "catalog_rows_updated:" in result.output


def test_rewrite_frontmatter_type_is_scoped_to_frontmatter(tmp_path: Path) -> None:
    """The page_type rewrite must NOT touch body mentions of `entity`."""
    page = tmp_path / "victim.md"
    page.write_text(
        "---\n"
        "title: Victim\n"
        "page_type: entity\n"
        "status: current\n"
        "---\n\n"
        "# Body\n\n"
        "This page describes the entity model and uses `page_type: entity` "
        "in code samples — those should stay untouched.\n",
        encoding="utf-8",
    )
    changed = migrate._rewrite_frontmatter_type(page)
    assert changed is True
    body = page.read_text(encoding="utf-8")
    # Frontmatter flipped.
    assert "page_type: person" in body
    # Body mentions of the literal phrase are preserved — the regex is
    # restricted to the leading frontmatter block.
    assert "page_type: entity`" in body
    assert "the entity model" in body


def test_rewrite_frontmatter_type_is_idempotent(tmp_path: Path) -> None:
    """Running the rewrite twice on a person page is a no-op."""
    page = tmp_path / "alice.md"
    page.write_text(
        "---\ntitle: Alice\npage_type: person\nstatus: active\n---\n\nbody\n",
        encoding="utf-8",
    )
    changed = migrate._rewrite_frontmatter_type(page)
    assert changed is False


def test_cli_commit_is_idempotent(
    synthetic_wiki: Path, db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running --commit twice is a no-op the second time."""
    _seed_catalog(db_conn, synthetic_wiki)
    monkeypatch.setattr(migrate.settings, "wiki_dir", synthetic_wiki)

    runner = CliRunner()
    first = runner.invoke(migrate.main, ["--commit"])
    assert first.exit_code == 0, first.output

    # Second run: nothing to migrate.
    second = runner.invoke(migrate.main, ["--commit"])
    assert second.exit_code == 0, second.output
    assert "nothing to migrate" in second.output
    # State unchanged: people/ still has the files, DB still clean.
    for slug in ("alice", "bob", "carol"):
        assert (synthetic_wiki / "people" / f"{slug}.md").exists()
    row = db_conn.execute(
        "SELECT COUNT(*) AS n FROM wiki_pages WHERE page_type = 'entity'"
    ).fetchone()
    assert row is not None
    assert row["n"] == 0


def test_cli_default_is_dry_run(
    synthetic_wiki: Path, db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invoking with no flags must be the safe dry-run path."""
    _seed_catalog(db_conn, synthetic_wiki)
    monkeypatch.setattr(migrate.settings, "wiki_dir", synthetic_wiki)

    runner = CliRunner()
    result = runner.invoke(migrate.main, [])
    assert result.exit_code == 0, result.output
    # Nothing moved.
    assert (synthetic_wiki / "entities" / "alice.md").exists()
    assert not (synthetic_wiki / "people" / "alice.md").exists()
    assert "dry-run" in result.output


# ---------------------------------------------------------------------------
# Regex correctness
# ---------------------------------------------------------------------------


def test_rewrite_preserves_bare_wikilinks() -> None:
    """`[[alice]]` without the `entities/` prefix stays untouched."""
    content = "See [[alice]] and [[entities/bob]] for details."
    new, wl, md = migrate._rewrite_content(content)
    assert new == "See [[alice]] and [[people/bob]] for details."
    assert wl == 1
    assert md == 0


def test_rewrite_preserves_aliased_wikilinks() -> None:
    """`[[entities/alice|Alice]]` keeps the alias after rewrite."""
    content = "Ping [[entities/alice|Alice]] for context."
    new, wl, _md = migrate._rewrite_content(content)
    assert new == "Ping [[people/alice|Alice]] for context."
    assert wl == 1


def test_rewrite_handles_relative_markdown_links() -> None:
    """`../entities/slug.md` variants all flip to people/."""
    content = "See [A](entities/a.md), [B](./entities/b.md), [C](../entities/c.md)."
    new, wl, md = migrate._rewrite_content(content)
    assert "people/a.md" in new
    assert "people/b.md" in new
    assert "people/c.md" in new
    assert "entities/" not in new
    assert md == 3
    assert wl == 0


def test_rewrite_is_idempotent_on_already_migrated_content() -> None:
    """Re-running the rewrite on post-migration content changes nothing."""
    content = "See [[people/alice]] and [Bob](people/bob.md)."
    new, wl, md = migrate._rewrite_content(content)
    assert new == content
    assert wl == 0
    assert md == 0


# ---------------------------------------------------------------------------
# Mid-flight collision regression (P1 #2)
# ---------------------------------------------------------------------------


def test_mid_flight_collision_does_not_clobber_protected_destination(
    synthetic_wiki: Path, db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Race scenario: someone creates ``people/alice.md`` after the plan
    is built but before ``_execute_moves`` runs. The mid-flight guard
    skips the move — we must NOT then run the planned rewrite of
    ``people/alice.md`` (originally aimed at the post-move location of
    ``entities/alice.md``), which would clobber the protected file.

    The fix in ``main`` filters such rewrites; this test asserts the
    protected file's content survives a full --commit invocation.
    """
    _seed_catalog(db_conn, synthetic_wiki)
    monkeypatch.setattr(migrate.settings, "wiki_dir", synthetic_wiki)

    # Add an entity-to-entity reference so alice's page is in the
    # rewrite plan (post-move target = wiki/people/alice.md).
    alice_src = synthetic_wiki / "entities" / "alice.md"
    alice_src.write_text(
        _entity_page("alice", "alice@example.com") + "\nWorks closely with [[entities/bob]].\n",
        encoding="utf-8",
    )

    # Build the plan FIRST — this records LinkRewrite(path=people/alice.md, ...).
    # Then create the collision target before the moves execute by patching
    # _execute_moves to drop a protected file at the destination right
    # before its loop starts.
    protected_marker = "PROTECTED_DESTINATION_DO_NOT_CLOBBER"
    real_execute_moves = migrate._execute_moves

    def execute_with_collision(moves: list[migrate.MovePlan]) -> tuple[int, list[migrate.Skip]]:
        # Race injection: the operator (or another script) wrote this
        # file between plan-build and execute-moves.
        (synthetic_wiki / "people").mkdir(parents=True, exist_ok=True)
        (synthetic_wiki / "people" / "alice.md").write_text(
            f"---\ntitle: Alice\npage_type: person\n---\n\n{protected_marker}\n",
            encoding="utf-8",
        )
        return real_execute_moves(moves)

    monkeypatch.setattr(migrate, "_execute_moves", execute_with_collision)

    runner = CliRunner()
    result = runner.invoke(migrate.main, ["--commit"])
    # The mid-flight skip exits 1 (partial migration). That's expected
    # behaviour; what we care about is that the protected file is intact.
    assert result.exit_code == 1, result.output
    surviving = (synthetic_wiki / "people" / "alice.md").read_text(encoding="utf-8")
    assert protected_marker in surviving, (
        "rewrite phase clobbered the mid-flight-protected destination"
    )
