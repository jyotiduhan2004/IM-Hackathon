"""Tests for the deterministic batch marking helpers in compile_all.py.

Covers the catalog-truth v7 Phase A U1 rework: the coordinator now reads
`message_touched_pages` (not wiki frontmatter) to decide which batch
emails to flip to `compiled` / `skipped` / kept pending. Entity/person
stubs no longer count as "compiled" evidence (Bug C is dead).
"""

from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime

import pytest
from src.db.wiki_pages import upsert_wiki_page


def _insert_message(conn, *, message_id: str, raw_path: str, state: str = "pending") -> None:
    conn.execute(
        """
        INSERT INTO messages (
          message_id, raw_path, thread_id, subject, from_address, date, compile_state
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (message_id, raw_path, "t1", "subj", "a@b.c", datetime.now(UTC), state),
    )


def _insert_page(conn, *, slug: str, page_type: str) -> int:
    """Upsert a wiki_pages row and return its page_id.

    Goes through the repo's upsert so the test exercises the same
    code path the compile loop uses for catalog sync.
    """
    return upsert_wiki_page(
        conn,
        slug=slug,
        path=f"wiki/{page_type}s/{slug}.md",
        title=slug.replace("-", " ").title(),
        page_type=page_type,
        status="active",
    )


def _insert_touch(conn, *, message_id: str, page_id: int) -> None:
    conn.execute(
        """
        INSERT INTO message_touched_pages (message_id, page_id)
        VALUES (%s, %s)
        """,
        (message_id, page_id),
    )


def _insert_run(conn) -> uuid.UUID:
    """Minimal ``compile_runs`` row — insights FK `run_id` needs it."""
    row = conn.execute(
        "INSERT INTO compile_runs (model, notes) VALUES (%s, %s) RETURNING run_id",
        ("test-model", "test"),
    ).fetchone()
    return row["run_id"]


def _insert_insight(conn, *, run_id: uuid.UUID, category: str, email_path: str) -> None:
    conn.execute(
        """
        INSERT INTO compile_insights (run_id, category, message, email_path)
        VALUES (%s, %s, %s, %s)
        """,
        (run_id, category, "test insight", email_path),
    )


def _state(conn, message_id: str) -> str:
    row = conn.execute(
        "SELECT compile_state FROM messages WHERE message_id = %s", (message_id,)
    ).fetchone()
    assert row is not None
    return row["compile_state"]


def _trivial_skip_category_accepted(conn) -> bool:
    """Probe the test-schema ``compile_insights`` CHECK for `trivial_skip`.

    The source-schema CHECK is widened by migration
    202604160500_compile_insights_trivial_skip.sql but the test-schema
    mirror in tests/conftest.py may lag for a commit. When the CHECK
    rejects ``trivial_skip`` we skip the skipped-insight tests instead
    of failing — mirrors the CLAUDE.md guardrail gating rule.
    """
    try:
        conn.execute(
            """
            INSERT INTO compile_insights (category, message, email_path)
            VALUES ('trivial_skip', 'probe', 'raw/probe.md')
            """
        )
    except Exception:  # noqa: BLE001 — this is a feature probe
        conn.rollback()
        return False
    conn.rollback()
    return True


def test_batch_paths_handles_dicts_and_strings(compile_all_module):
    mod = compile_all_module
    assert mod._batch_paths(["a", "b"]) == ["a", "b"]
    assert mod._batch_paths([{"path": "a"}, {"path": "b"}]) == ["a", "b"]
    assert mod._batch_paths(["a", {"path": "b"}]) == ["a", "b"]


def test_mark_batch_compiled_only_flips_content_touched(compile_all_module, db_conn, tmp_path):
    """Messages with a touch on a content-type page flip to compiled;
    messages touched only on a person/entity stub stay pending (Bug C dead)."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    _insert_message(db_conn, message_id="m3", raw_path="raw/c.md")
    # m1 → topic page (content)  → compiled
    # m2 → no touches             → not cited (pending)
    # m3 → person stub only       → Bug C: stays pending, NOT compiled
    topic_id = _insert_page(db_conn, slug="topic-a", page_type="topic")
    person_id = _insert_page(db_conn, slug="person-c", page_type="person")
    _insert_touch(db_conn, message_id="m1", page_id=topic_id)
    _insert_touch(db_conn, message_id="m3", page_id=person_id)
    db_conn.commit()

    batch = [{"path": "raw/a.md"}, {"path": "raw/b.md"}, {"path": "raw/c.md"}]
    compiled, skipped, not_cited_paths, missing = mod._mark_batch_compiled(batch, tmp_path)
    assert set(compiled) == {"m1"}
    assert skipped == []
    # m2 (no touches) + m3 (person stub only) → pending; returned as paths
    # so the coordinator can selectively flip the terminal-guard-exhausted
    # subset to ``skipped``.
    assert set(not_cited_paths) == {"raw/b.md", "raw/c.md"}
    assert missing == 0
    assert _state(db_conn, "m1") == "compiled"
    assert _state(db_conn, "m2") == "pending"
    assert _state(db_conn, "m3") == "pending"


def test_mark_batch_compiled_reports_missing(compile_all_module, db_conn, tmp_path):
    """Paths with no `messages` row count as missing (backfill drift)."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    topic_id = _insert_page(db_conn, slug="topic-a", page_type="topic")
    _insert_touch(db_conn, message_id="m1", page_id=topic_id)
    db_conn.commit()

    batch = [{"path": "raw/a.md"}, {"path": "raw/not-in-db.md"}]
    compiled, skipped, not_cited_paths, missing = mod._mark_batch_compiled(batch, tmp_path)
    assert compiled == ["m1"]
    assert skipped == []
    assert not_cited_paths == []
    assert missing == 1
    assert _state(db_conn, "m1") == "compiled"


def test_mark_batch_compiled_all_uncited_keeps_all_pending(compile_all_module, db_conn, tmp_path):
    """No touches in the catalog → every batch email stays pending."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    db_conn.commit()

    batch = [{"path": "raw/a.md"}, {"path": "raw/b.md"}]
    compiled, skipped, not_cited_paths, _missing = mod._mark_batch_compiled(batch, tmp_path)
    assert compiled == []
    assert skipped == []
    assert set(not_cited_paths) == {"raw/a.md", "raw/b.md"}
    assert _state(db_conn, "m1") == "pending"
    assert _state(db_conn, "m2") == "pending"


def test_mark_batch_compiled_skips_on_trivial_insight(compile_all_module, db_conn, tmp_path):
    """A message with a `trivial_skip` insight logged in the current run
    flips to ``skipped`` instead of staying pending."""
    mod = compile_all_module
    if not _trivial_skip_category_accepted(db_conn):
        pytest.skip("test-schema CHECK lags production — trivial_skip not yet accepted")
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    run_id = _insert_run(db_conn)
    _insert_insight(db_conn, run_id=run_id, category="trivial_skip", email_path="raw/a.md")
    db_conn.commit()

    batch = [{"path": "raw/a.md"}]
    compiled, skipped, not_cited_paths, missing = mod._mark_batch_compiled(
        batch, tmp_path, run_id=run_id
    )
    assert compiled == []
    assert skipped == ["m1"]
    assert not_cited_paths == []
    assert missing == 0
    assert _state(db_conn, "m1") == "skipped"


def test_mark_batch_compiled_skip_insight_from_other_run_ignored(
    compile_all_module, db_conn, tmp_path
):
    """A trivial_skip insight from a prior run must not flip this run's
    messages — skip-insight lookup is scoped by run_id."""
    mod = compile_all_module
    if not _trivial_skip_category_accepted(db_conn):
        pytest.skip("test-schema CHECK lags production — trivial_skip not yet accepted")
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    old_run = _insert_run(db_conn)
    _insert_insight(db_conn, run_id=old_run, category="trivial_skip", email_path="raw/a.md")
    current_run = _insert_run(db_conn)
    db_conn.commit()

    batch = [{"path": "raw/a.md"}]
    compiled, skipped, not_cited_paths, _missing = mod._mark_batch_compiled(
        batch, tmp_path, run_id=current_run
    )
    assert compiled == []
    assert skipped == []  # the prior-run insight does not reach across runs
    assert not_cited_paths == ["raw/a.md"]
    assert _state(db_conn, "m1") == "pending"


def test_mark_batch_failed_flips_to_failed(compile_all_module, db_conn):
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    db_conn.commit()

    batch = [{"path": "raw/a.md"}, {"path": "raw/b.md"}]
    marked = mod._mark_batch_failed(batch, "recursion limit hit")
    assert marked == 2
    assert _state(db_conn, "m1") == "failed"
    assert _state(db_conn, "m2") == "failed"
    row = db_conn.execute("SELECT last_error FROM messages WHERE message_id = 'm1'").fetchone()
    assert row["last_error"] == "recursion limit hit"


def test_mark_batch_failed_truncates_long_error(compile_all_module, db_conn):
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    db_conn.commit()

    long_err = "x" * 10_000
    mod._mark_batch_failed([{"path": "raw/a.md"}], long_err)
    row = db_conn.execute("SELECT last_error FROM messages WHERE message_id = 'm1'").fetchone()
    assert len(row["last_error"]) == 500


def test_write_touch_catalog_filters_to_content_pages(compile_all_module, db_conn, tmp_path):
    """``_write_touch_catalog`` writes a row for each (batch message,
    touched content-type page) pair and silently ignores entity/person
    stubs — core of the Bug C fix."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    topic_id = _insert_page(db_conn, slug="my-topic", page_type="topic")
    person_id = _insert_page(db_conn, slug="alice", page_type="person")
    db_conn.commit()

    # Simulate the on-disk "touched pages" list: a topic page + a person
    # stub. Only the topic should land in the catalog.
    topic_path = tmp_path / "topics" / "my-topic.md"
    person_path = tmp_path / "people" / "alice.md"
    topic_path.parent.mkdir(parents=True, exist_ok=True)
    person_path.parent.mkdir(parents=True, exist_ok=True)
    topic_path.write_text("stub", encoding="utf-8")
    person_path.write_text("stub", encoding="utf-8")

    inserted = mod._write_touch_catalog([topic_path, person_path], ["m1"])
    assert inserted == 1

    touches = db_conn.execute(
        "SELECT message_id, page_id FROM message_touched_pages ORDER BY page_id"
    ).fetchall()
    assert len(touches) == 1
    assert touches[0]["message_id"] == "m1"
    assert touches[0]["page_id"] == topic_id
    # Person stub row is absent — Bug C stays dead.
    assert person_id not in {t["page_id"] for t in touches}


def test_write_touch_catalog_idempotent(compile_all_module, db_conn, tmp_path):
    """Re-running the hook over the same (message, page) pair is a no-op
    thanks to the ON CONFLICT guard in ``insert_touch``."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_page(db_conn, slug="my-topic", page_type="topic")
    db_conn.commit()

    topic_path = tmp_path / "topics" / "my-topic.md"
    topic_path.parent.mkdir(parents=True, exist_ok=True)
    topic_path.write_text("stub", encoding="utf-8")

    first = mod._write_touch_catalog([topic_path], ["m1"])
    second = mod._write_touch_catalog([topic_path], ["m1"])
    assert first == 1
    assert second == 0  # second call is a pure ON CONFLICT DO NOTHING

    count = db_conn.execute("SELECT COUNT(*)::int AS c FROM message_touched_pages").fetchone()
    assert count["c"] == 1


def test_collect_content_cited_message_ids_filters_by_page_type(compile_all_module, db_conn):
    """The catalog query joins ``wiki_pages`` and filters to
    ``CONTENT_PAGE_TYPES`` — entity/person touches are excluded."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    topic_id = _insert_page(db_conn, slug="t", page_type="topic")
    person_id = _insert_page(db_conn, slug="alice", page_type="person")
    _insert_touch(db_conn, message_id="m1", page_id=topic_id)
    _insert_touch(db_conn, message_id="m2", page_id=person_id)
    db_conn.commit()

    cited = mod._collect_content_cited_message_ids(["m1", "m2"])
    assert cited == {"m1"}  # m2's person-stub touch is filtered out


# ---------------------------------------------------------------------------
# Terminal-decision guard fallback helpers (V12 audit fix-C).
# ---------------------------------------------------------------------------


def test_terminal_guard_sentinel_is_present_in_nudge_message(compile_all_module) -> None:
    """The sentinel substring must appear in the middleware's canonical nudge.

    The coordinator scans batch_result messages for the sentinel to
    decide whether to flip not-cited paths to ``skipped``. If the
    middleware's wording drifts such that the sentinel is gone, this
    test fires — without it a wording tweak silently breaks the
    coordinator fallback.
    """
    from src.agent.middleware.terminal_decision_guard import TERMINAL_NUDGE_MESSAGE

    assert compile_all_module._TERMINAL_NUDGE_SENTINEL in TERMINAL_NUDGE_MESSAGE


def test_terminal_guard_exhausted_detects_injected_nudge(compile_all_module) -> None:
    """``_terminal_guard_exhausted`` returns True when the nudge is in messages."""
    from langchain_core.messages import HumanMessage
    from src.agent.middleware.terminal_decision_guard import TERMINAL_NUDGE_MESSAGE

    mod = compile_all_module
    result = {"messages": [HumanMessage(content=TERMINAL_NUDGE_MESSAGE)]}
    assert mod._terminal_guard_exhausted(result) is True


def test_terminal_guard_exhausted_false_on_clean_result(compile_all_module) -> None:
    """Without the sentinel, the guard-exhausted check returns False."""
    from langchain_core.messages import AIMessage

    mod = compile_all_module
    assert mod._terminal_guard_exhausted({"messages": [AIMessage(content="done")]}) is False
    assert mod._terminal_guard_exhausted({"messages": []}) is False
    assert mod._terminal_guard_exhausted(None) is False
    assert mod._terminal_guard_exhausted({}) is False


def test_mark_terminal_guard_exhausted_paths_flips_to_skipped(compile_all_module, db_conn) -> None:
    """Not-cited paths flip to ``skipped`` with the guard-exhausted reason."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    db_conn.commit()

    flipped = mod._mark_terminal_guard_exhausted_paths(["raw/a.md", "raw/b.md"])

    assert set(flipped) == {"m1", "m2"}
    assert _state(db_conn, "m1") == "skipped"
    assert _state(db_conn, "m2") == "skipped"
    row = db_conn.execute("SELECT last_error FROM messages WHERE message_id = 'm1'").fetchone()
    assert row["last_error"] == mod.TERMINAL_GUARD_EXHAUSTED_REASON


def test_mark_terminal_guard_exhausted_paths_skips_missing_rows(
    compile_all_module, db_conn
) -> None:
    """Paths without a ``messages`` row are silently dropped (no crash)."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    db_conn.commit()

    # raw/missing.md has no messages row — helper must not crash.
    flipped = mod._mark_terminal_guard_exhausted_paths(["raw/missing.md", "raw/a.md"])

    assert flipped == ["m1"]
    assert _state(db_conn, "m1") == "skipped"


def test_mark_terminal_guard_exhausted_paths_preserves_compiled(
    compile_all_module, db_conn
) -> None:
    """``mark_skipped`` is a no-op on already-compiled rows (state guard)."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md", state="compiled")
    db_conn.commit()

    flipped = mod._mark_terminal_guard_exhausted_paths(["raw/a.md"])

    assert flipped == []  # compiled rows don't flip
    assert _state(db_conn, "m1") == "compiled"


# ---------------------------------------------------------------------------
# Secondary compile signal (#174): attempts-based fallback.
# ---------------------------------------------------------------------------


def _insert_attempt(conn, *, message_id: str, run_id: uuid.UUID, outcome: str | None = None) -> int:
    """Insert a ``compile_attempts`` row. When ``outcome`` is set, also
    stamp ``finished_at``. Returns the row id."""
    if outcome is None:
        row = conn.execute(
            """
            INSERT INTO compile_attempts (
              message_id, run_id, compile_model
            )
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (message_id, run_id, "test-model"),
        ).fetchone()
    else:
        row = conn.execute(
            """
            INSERT INTO compile_attempts (
              message_id, run_id, compile_model, outcome, finished_at
            )
            VALUES (%s, %s, %s, %s, now())
            RETURNING id
            """,
            (message_id, run_id, "test-model", outcome),
        ).fetchone()
    return int(row["id"])


def test_mark_batch_compiled_attempts_signal_flips_people_only_touch(
    compile_all_module, db_conn, tmp_path
) -> None:
    """#174 secondary signal: person-only touch PLUS attempts outcome='compiled'
    in the current run flips the message to compiled (the ~5% waste case)."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    run_id = _insert_run(db_conn)
    # Agent's only citation was a people stub — content-page filter
    # normally excludes this. But the attempt row says it compiled.
    person_id = _insert_page(db_conn, slug="alice", page_type="person")
    _insert_touch(db_conn, message_id="m1", page_id=person_id)
    _insert_attempt(db_conn, message_id="m1", run_id=run_id, outcome="compiled")
    db_conn.commit()

    batch = [{"path": "raw/a.md"}]
    compiled, skipped, not_cited_paths, _missing = mod._mark_batch_compiled(
        batch, tmp_path, run_id=run_id
    )
    assert compiled == ["m1"]
    assert skipped == []
    assert not_cited_paths == []
    assert _state(db_conn, "m1") == "compiled"


def test_mark_batch_compiled_attempts_signal_scoped_by_run(
    compile_all_module, db_conn, tmp_path
) -> None:
    """A prior run's outcome='compiled' attempt must not flip this run's message."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    old_run = _insert_run(db_conn)
    current_run = _insert_run(db_conn)
    # outcome='compiled' attached to the OLD run id — should be ignored.
    _insert_attempt(db_conn, message_id="m1", run_id=old_run, outcome="compiled")
    db_conn.commit()

    batch = [{"path": "raw/a.md"}]
    compiled, skipped, not_cited_paths, _missing = mod._mark_batch_compiled(
        batch, tmp_path, run_id=current_run
    )
    assert compiled == []
    assert skipped == []
    assert not_cited_paths == ["raw/a.md"]
    assert _state(db_conn, "m1") == "pending"


def test_mark_batch_compiled_in_flight_attempts_do_not_flip(
    compile_all_module, db_conn, tmp_path
) -> None:
    """In-flight attempts (outcome IS NULL) are not evidence of success —
    only stamped ``outcome='compiled'`` counts as the secondary signal."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    run_id = _insert_run(db_conn)
    # In-flight attempt (NULL outcome) — the normal within-batch state
    # before ``_record_attempts_outcome`` fires. Must not auto-flip.
    _insert_attempt(db_conn, message_id="m1", run_id=run_id, outcome=None)
    db_conn.commit()

    batch = [{"path": "raw/a.md"}]
    compiled, _skipped, not_cited_paths, _missing = mod._mark_batch_compiled(
        batch, tmp_path, run_id=run_id
    )
    assert compiled == []
    assert not_cited_paths == ["raw/a.md"]
    assert _state(db_conn, "m1") == "pending"


def test_collect_attempts_compiled_message_ids(compile_all_module, db_conn) -> None:
    """Helper returns only message_ids with a stamped 'compiled' outcome
    attached to the given run_id."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    _insert_message(db_conn, message_id="m3", raw_path="raw/c.md")
    run_id = _insert_run(db_conn)
    other_run = _insert_run(db_conn)
    _insert_attempt(db_conn, message_id="m1", run_id=run_id, outcome="compiled")
    _insert_attempt(db_conn, message_id="m2", run_id=run_id, outcome="failed")
    _insert_attempt(db_conn, message_id="m3", run_id=other_run, outcome="compiled")
    db_conn.commit()

    cited = mod._collect_attempts_compiled_message_ids(run_id, ["m1", "m2", "m3"])
    assert cited == {"m1"}


# ---------------------------------------------------------------------------
# #179 — merge_candidates.md frontmatter.
# ---------------------------------------------------------------------------


def test_append_merge_candidates_writes_frontmatter_on_first_append(
    compile_all_module, tmp_path
) -> None:
    """First append creates ``merge_candidates.md`` with YAML frontmatter
    that validates as ``page_type: coordinator_notes`` — otherwise critique's
    touched-pages scan flags it as 'broken' and loops the gate (#179)."""
    mod = compile_all_module
    pairs = [{"slug_a": "a", "slug_b": "b", "note": "duplicate"}]
    written = mod._append_merge_candidates(pairs, str(tmp_path), trace_id="run-1:batch-1")
    assert written == 1

    content = (tmp_path / "merge_candidates.md").read_text(encoding="utf-8")
    assert content.startswith("---\n")
    # Canonical frontmatter block — page_type must be coordinator_notes so
    # the wiki_pages CHECK + validator accept the catalog row.
    head = content.split("---", 2)[1]
    assert 'title: "Merge candidates"' in head
    assert "page_type: coordinator_notes" in head
    assert "status: active" in head


def test_append_merge_candidates_backfills_legacy_file(compile_all_module, tmp_path) -> None:
    """An existing ``merge_candidates.md`` without frontmatter gets the
    header prepended on the next append — legacy files migrate in place."""
    mod = compile_all_module
    queue = tmp_path / "merge_candidates.md"
    queue.write_text("# Merge candidates\n\nSome legacy content\n", encoding="utf-8")

    pairs = [{"slug_a": "x", "slug_b": "y", "note": "merge me"}]
    written = mod._append_merge_candidates(pairs, str(tmp_path), trace_id="run-1:batch-1")
    assert written == 1

    content = queue.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    head = content.split("---", 2)[1]
    assert "page_type: coordinator_notes" in head
    # Legacy body is preserved.
    assert "Some legacy content" in content
    # New append landed too.
    assert "[x] vs [y]" in content


def test_append_merge_candidates_idempotent_frontmatter(compile_all_module, tmp_path) -> None:
    """Re-append over an already-correct file leaves the header untouched
    (exactly one frontmatter block, not a nested one)."""
    mod = compile_all_module
    pairs = [{"slug_a": "a", "slug_b": "b", "note": "n"}]
    mod._append_merge_candidates(pairs, str(tmp_path), trace_id="run-1:batch-1")
    mod._append_merge_candidates(pairs, str(tmp_path), trace_id="run-1:batch-2")

    content = (tmp_path / "merge_candidates.md").read_text(encoding="utf-8")
    # Exactly two `---` markers (opening + closing of the single header).
    assert content.count("---\n") == 2


# ---------------------------------------------------------------------------
# #180 — empty-frontmatter guard on catalog sync.
# ---------------------------------------------------------------------------


def test_sync_wiki_catalog_skips_empty_frontmatter(compile_all_module, db_conn, tmp_path) -> None:
    """A page with empty YAML frontmatter (``---\\n{}\\n---``) is NOT
    upserted into ``wiki_pages`` — the catalog must not pretend a mangled
    page exists (#180)."""
    mod = compile_all_module
    topics = tmp_path / "topics"
    topics.mkdir(parents=True)
    # Empty-frontmatter case — exactly the shape #180 flagged.
    bad_page = topics / "google-ads-account-guardrails.md"
    bad_page.write_text("---\n{}\n---\n\nbody\n", encoding="utf-8")
    # A well-formed sibling proves the loop still processes other rows.
    good_page = topics / "good-one.md"
    good_page.write_text(
        "---\ntitle: Good\npage_type: topic\nstatus: active\n---\n\nbody\n",
        encoding="utf-8",
    )
    db_conn.commit()

    synced = mod._sync_wiki_catalog([bad_page, good_page], tmp_path)
    assert synced == 1  # only the good page

    row = db_conn.execute("SELECT slug FROM wiki_pages ORDER BY slug").fetchall()
    slugs = [r["slug"] for r in row]
    assert slugs == ["good-one"]


def test_sync_wiki_catalog_keeps_upserting_when_title_missing(
    compile_all_module, db_conn, tmp_path
) -> None:
    """A page with frontmatter that has ``page_type`` but no ``title`` still
    syncs (the fallback stem-derived title kicks in) — the guard targets
    EMPTY frontmatter, not merely incomplete."""
    mod = compile_all_module
    topics = tmp_path / "topics"
    topics.mkdir(parents=True)
    page = topics / "topic-no-title.md"
    page.write_text("---\npage_type: topic\nstatus: active\n---\n\nbody\n", encoding="utf-8")
    db_conn.commit()

    synced = mod._sync_wiki_catalog([page], tmp_path)
    assert synced == 1
    row = db_conn.execute(
        "SELECT slug, title FROM wiki_pages WHERE slug = %s", ("topic-no-title",)
    ).fetchone()
    assert row is not None
    assert row["title"] == "Topic No Title"  # stem fallback


# ---------------------------------------------------------------------------
# #165 — last_compiled_at DB stamp.
# ---------------------------------------------------------------------------


def test_stamp_recently_modified_pages_updates_wiki_pages_last_compiled_at(
    compile_all_module, db_conn, tmp_path
) -> None:
    """Stamping a page bumps BOTH the frontmatter `last_compiled` AND the
    catalog mirror `wiki_pages.last_compiled_at` (#165)."""
    import time

    mod = compile_all_module
    # Pre-seed the catalog row with NULL last_compiled_at — mimics the
    # production state that #165 called out.
    _insert_page(db_conn, slug="topic-a", page_type="topic")
    db_conn.commit()

    # Freshly-written topic page on disk.
    topics = tmp_path / "topics"
    topics.mkdir(parents=True)
    page = topics / "topic-a.md"
    page.write_text(
        "---\ntitle: Topic A\npage_type: topic\nstatus: active\n---\n\nbody\n",
        encoding="utf-8",
    )
    # `since_timestamp` just before the write so the page is "recent".
    since = time.time() - 60

    stamped, skipped = mod._stamp_recently_modified_pages(str(tmp_path), since, "test-model")
    assert stamped == 1
    assert skipped == 0

    # Frontmatter stamp landed on disk.
    content = page.read_text(encoding="utf-8")
    assert "last_compiled:" in content

    # DB mirror stamped — was NULL before the run (#165 symptom).
    row = db_conn.execute(
        "SELECT last_compiled_at FROM wiki_pages WHERE slug = %s",
        ("topic-a",),
    ).fetchone()
    assert row is not None
    assert row["last_compiled_at"] is not None


def test_stamp_catalog_last_compiled_batch_updates_multiple_slugs(
    compile_all_module, db_conn
) -> None:
    """Helper batches UPDATEs across every passed slug."""
    mod = compile_all_module
    _insert_page(db_conn, slug="a", page_type="topic")
    _insert_page(db_conn, slug="b", page_type="system")
    db_conn.commit()

    mod._stamp_catalog_last_compiled(["a", "b"])

    rows = db_conn.execute("SELECT slug, last_compiled_at FROM wiki_pages ORDER BY slug").fetchall()
    assert all(r["last_compiled_at"] is not None for r in rows)


def test_stamp_catalog_last_compiled_empty_noop(compile_all_module) -> None:
    """Empty slug list is a no-op (no DB round-trip, no crash)."""
    mod = compile_all_module
    # Should not raise even without any DB connection wiring.
    mod._stamp_catalog_last_compiled([])
