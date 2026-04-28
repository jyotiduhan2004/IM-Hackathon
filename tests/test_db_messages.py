"""Tests for the messages catalog repo functions (src/db/messages.py).

Isolation: see tests/conftest.py. Each test runs against the dedicated
`email_kb_test` database and starts with an empty messages table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

import psycopg

from src.db import messages as repo


def _insert(
    conn: psycopg.Connection,
    *,
    message_id: str,
    raw_path: str | None = None,
    thread_id: str | None = "t-1",
    subject: str | None = "s",
    from_address: str | None = "a@b.c",
    date: datetime | None = None,
    compile_state: str = "pending",
) -> bool:
    """Shortcut: insert via the repo using the test connection."""
    return repo.insert_message(
        conn,
        message_id=message_id,
        raw_path=raw_path or f"raw/{message_id}.md",
        thread_id=thread_id,
        subject=subject,
        from_address=from_address,
        date=date,
        compile_state=compile_state,
    )


def _fetch_one(conn: psycopg.Connection, message_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM messages WHERE message_id = %s", (message_id,)).fetchone()
    assert row is not None, f"no row for {message_id}"
    return row


# ---------------------------------------------------------------------------
# insert_message
# ---------------------------------------------------------------------------


def test_insert_message_basic(db_conn: psycopg.Connection) -> None:
    first = _insert(db_conn, message_id="m1")
    db_conn.commit()
    assert first is True

    # Re-insert: ON CONFLICT DO NOTHING → returns False, row unchanged.
    second = _insert(db_conn, message_id="m1", subject="NEW SUBJECT")
    db_conn.commit()
    assert second is False

    row = _fetch_one(db_conn, "m1")
    assert row["subject"] == "s"  # original value preserved


# ---------------------------------------------------------------------------
# list_uncompiled
# ---------------------------------------------------------------------------


def test_list_uncompiled_orders_by_date_asc_nulls_last(
    db_conn: psycopg.Connection,
) -> None:
    d_old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    d_new = datetime(2026, 6, 1, tzinfo=timezone.utc)

    _insert(db_conn, message_id="m_new", date=d_new)
    _insert(db_conn, message_id="m_null", date=None)
    _insert(db_conn, message_id="m_old", date=d_old)
    db_conn.commit()

    rows = repo.list_uncompiled()
    ids = [r["message_id"] for r in rows]
    # Oldest first, then newer, NULL dates last.
    assert ids == ["m_old", "m_new", "m_null"]


def test_list_uncompiled_by_thread_returns_all_emails_from_oldest_n_threads(
    db_conn: psycopg.Connection,
) -> None:
    """`limit_threads=2` pulls all pending emails from the 2 oldest threads."""
    d1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    d2 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    d3 = datetime(2026, 3, 1, tzinfo=timezone.utc)

    # Thread A: 3 emails (oldest)
    _insert(db_conn, message_id="a1", thread_id="tA", date=d1)
    _insert(db_conn, message_id="a2", thread_id="tA", date=d1)
    _insert(db_conn, message_id="a3", thread_id="tA", date=d1)
    # Thread B: 2 emails (middle)
    _insert(db_conn, message_id="b1", thread_id="tB", date=d2)
    _insert(db_conn, message_id="b2", thread_id="tB", date=d2)
    # Thread C: 1 email (newest — excluded at limit=2)
    _insert(db_conn, message_id="c1", thread_id="tC", date=d3)
    db_conn.commit()

    rows = repo.list_uncompiled_by_thread(limit_threads=2)
    ids = {r["message_id"] for r in rows}
    # All 3 from A + all 2 from B; C excluded.
    assert ids == {"a1", "a2", "a3", "b1", "b2"}


def test_list_uncompiled_by_thread_treats_null_thread_as_singleton(
    db_conn: psycopg.Connection,
) -> None:
    """Emails with NULL thread_id each count as their own thread."""
    d1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _insert(db_conn, message_id="s1", thread_id=None, date=d1)
    _insert(db_conn, message_id="s2", thread_id=None, date=d1)
    db_conn.commit()

    rows = repo.list_uncompiled_by_thread(limit_threads=1)
    # Only 1 of the 2 singleton "threads" pulled.
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# claim_next_message + finish_message_compile (happy path)
# ---------------------------------------------------------------------------


def test_claim_finish_happy_path(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    db_conn.commit()

    run_id = uuid.uuid4()
    claimed = repo.claim_next_message(run_id)
    assert claimed is not None
    assert claimed["message_id"] == "m1"
    assert claimed["compile_attempts"] == 1

    row = _fetch_one(db_conn, "m1")
    assert row["compile_state"] == "claimed"
    assert row["compile_run_id"] == run_id
    assert row["claimed_at"] is not None
    assert row["is_compiled"] is False  # generated column

    repo.finish_message_compile("m1")

    row = _fetch_one(db_conn, "m1")
    assert row["compile_state"] == "compiled"
    assert row["compiled_at"] is not None
    assert row["is_compiled"] is True  # generated column reflects state
    assert row["last_error"] is None


# ---------------------------------------------------------------------------
# claim_next_message concurrency (no double-claim)
# ---------------------------------------------------------------------------


def test_claim_skip_locked_no_double_claim(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(db_conn, message_id="m2", date=datetime(2026, 2, 1, tzinfo=timezone.utc))
    db_conn.commit()

    run_a = uuid.uuid4()
    run_b = uuid.uuid4()

    first = repo.claim_next_message(run_a)
    second = repo.claim_next_message(run_b)

    assert first is not None
    assert second is not None
    assert first["message_id"] != second["message_id"], "two workers must not claim the same row"
    assert {first["message_id"], second["message_id"]} == {"m1", "m2"}

    # Third claim: queue empty → None.
    third = repo.claim_next_message(uuid.uuid4())
    assert third is None


# ---------------------------------------------------------------------------
# fail_message_compile → re-claim increments attempts, clears last_error
# ---------------------------------------------------------------------------


def test_fail_then_reclaim_increments_attempts(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    db_conn.commit()

    run_id = uuid.uuid4()
    first = repo.claim_next_message(run_id)
    assert first is not None
    assert first["compile_attempts"] == 1

    repo.fail_message_compile("m1", error="boom")
    row = _fetch_one(db_conn, "m1")
    assert row["compile_state"] == "failed"
    assert row["last_error"] == "boom"

    # Re-claim should pick the failed row up again.
    second = repo.claim_next_message(uuid.uuid4())
    assert second is not None
    assert second["message_id"] == "m1"
    assert second["compile_attempts"] == 2

    row = _fetch_one(db_conn, "m1")
    assert row["last_error"] is None  # cleared on re-claim
    assert row["compile_state"] == "claimed"


# ---------------------------------------------------------------------------
# Stale-claim recovery
# ---------------------------------------------------------------------------


def test_stale_claim_recovery(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    db_conn.commit()

    first = repo.claim_next_message(uuid.uuid4())
    assert first is not None
    assert first["compile_attempts"] == 1

    # A second claim right away finds nothing — it's still claimed and fresh.
    fresh = repo.claim_next_message(uuid.uuid4(), stale_after_minutes=30)
    assert fresh is None

    # Simulate a crashed worker: backdate claimed_at by 1 hour.
    stale_ts = datetime.now(timezone.utc) - timedelta(hours=1)
    db_conn.execute(
        "UPDATE messages SET claimed_at = %s WHERE message_id = %s",
        (stale_ts, "m1"),
    )
    db_conn.commit()

    # Now with the default 30-minute staleness threshold, it should be reclaimable.
    stolen = repo.claim_next_message(uuid.uuid4(), stale_after_minutes=30)
    assert stolen is not None
    assert stolen["message_id"] == "m1"
    assert stolen["compile_attempts"] == 2


# ---------------------------------------------------------------------------
# recover_stale_claims (the coordinator hook used by compile_all.py /
# compile_parallel.py — ensures orphan `claimed` rows are visible to the
# dispatcher's pending/failed-only list helpers)
# ---------------------------------------------------------------------------


def test_recover_stale_claims_resets_old_claims(db_conn: psycopg.Connection) -> None:
    """A `claimed` row older than the threshold flips back to pending."""
    _insert(db_conn, message_id="m_stale", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    db_conn.commit()

    claimed = repo.claim_next_message(uuid.uuid4())
    assert claimed is not None and claimed["message_id"] == "m_stale"

    # Backdate claimed_at to 13 hours ago — past the 12h default threshold.
    db_conn.execute(
        "UPDATE messages SET claimed_at = %s WHERE message_id = %s",
        (datetime.now(timezone.utc) - timedelta(hours=13), "m_stale"),
    )
    db_conn.commit()

    recovered = repo.recover_stale_claims()
    assert recovered == 1

    row = _fetch_one(db_conn, "m_stale")
    assert row["compile_state"] == "pending"
    assert row["claimed_at"] is None
    assert row["compile_run_id"] is None
    # Attempts are preserved — retry history isn't lost on recovery.
    assert row["compile_attempts"] == 1


def test_recover_stale_claims_leaves_fresh_claims_alone(db_conn: psycopg.Connection) -> None:
    """A `claimed` row younger than the threshold is left in `claimed`."""
    _insert(db_conn, message_id="m_fresh", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    db_conn.commit()

    claimed = repo.claim_next_message(uuid.uuid4())
    assert claimed is not None

    # Default threshold is 12 hours; a just-claimed row is well under it.
    recovered = repo.recover_stale_claims()
    assert recovered == 0

    row = _fetch_one(db_conn, "m_fresh")
    assert row["compile_state"] == "claimed"


# ---------------------------------------------------------------------------
# count_by_state
# ---------------------------------------------------------------------------


def test_count_by_state(db_conn: psycopg.Connection) -> None:
    # Distribution: 3 pending, 2 compiled, 1 failed.
    _insert(db_conn, message_id="p1")
    _insert(db_conn, message_id="p2")
    _insert(db_conn, message_id="p3")
    _insert(
        db_conn,
        message_id="c1",
        compile_state="compiled",
    )
    _insert(
        db_conn,
        message_id="c2",
        compile_state="compiled",
    )
    _insert(db_conn, message_id="f1", compile_state="failed")
    db_conn.commit()

    counts = repo.count_by_state()
    assert counts == {"pending": 3, "compiled": 2, "failed": 1}


# ---------------------------------------------------------------------------
# remaining_uncompiled_count
# ---------------------------------------------------------------------------


def test_remaining_uncompiled_count(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="p1")
    _insert(db_conn, message_id="p2")
    _insert(db_conn, message_id="f1", compile_state="failed")
    _insert(db_conn, message_id="c1", compile_state="compiled")
    db_conn.commit()

    remaining = repo.remaining_uncompiled_count()
    uncompiled_rows = repo.list_uncompiled()

    assert remaining == len(uncompiled_rows)
    assert remaining == 3  # two pending + one failed


# ---------------------------------------------------------------------------
# find_by_raw_path
# ---------------------------------------------------------------------------


def test_find_by_raw_path(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", raw_path="raw/2026-04-10-xyz.md")
    db_conn.commit()

    row = repo.find_by_raw_path("raw/2026-04-10-xyz.md")
    assert row is not None
    assert row["message_id"] == "m1"
    assert row["compile_state"] == "pending"

    assert repo.find_by_raw_path("raw/does-not-exist.md") is None


# ---------------------------------------------------------------------------
# find_by_raw_paths (batch) — used by backfill scripts
# ---------------------------------------------------------------------------


def test_find_by_raw_paths_batch(db_conn: psycopg.Connection) -> None:
    """Resolve many paths in one round-trip; missing paths absent from output."""
    _insert(db_conn, message_id="m1", raw_path="raw/a.md", thread_id="t-A")
    _insert(db_conn, message_id="m2", raw_path="raw/b.md", thread_id="t-A")
    _insert(db_conn, message_id="m3", raw_path="raw/c.md", thread_id="t-B")
    db_conn.commit()

    # Mix of hits + one miss + one duplicate (dedupe on the way in).
    got = repo.find_by_raw_paths(
        ["raw/a.md", "raw/b.md", "raw/missing.md", "raw/c.md", "raw/a.md"],
        conn=db_conn,
    )
    assert set(got.keys()) == {"raw/a.md", "raw/b.md", "raw/c.md"}
    assert got["raw/a.md"] == {"message_id": "m1", "thread_id": "t-A"}
    assert got["raw/b.md"] == {"message_id": "m2", "thread_id": "t-A"}
    assert got["raw/c.md"] == {"message_id": "m3", "thread_id": "t-B"}


def test_find_by_raw_paths_empty_short_circuits() -> None:
    """Empty input avoids any DB call + returns an empty dict."""
    assert repo.find_by_raw_paths([]) == {}


def test_find_by_raw_paths_chunks_at_500(db_conn: psycopg.Connection) -> None:
    """Input larger than a single chunk still resolves every hit.

    The chunk boundary is a 500-path ``ANY(%s)`` query. Seed 600 rows,
    query all 600 + 10 missing, expect 600 results back.
    """
    for i in range(600):
        _insert(db_conn, message_id=f"m{i}", raw_path=f"raw/{i:04d}.md", thread_id="t-X")
    db_conn.commit()

    paths = [f"raw/{i:04d}.md" for i in range(600)] + [f"raw/missing-{i}.md" for i in range(10)]
    got = repo.find_by_raw_paths(paths, conn=db_conn)
    assert len(got) == 600
    assert got["raw/0000.md"]["message_id"] == "m0"
    assert got["raw/0599.md"]["message_id"] == "m599"
    assert "raw/missing-0.md" not in got


# ---------------------------------------------------------------------------
# reset_to_pending (bulk)
# ---------------------------------------------------------------------------


def test_reset_to_pending_bulk(db_conn: psycopg.Connection) -> None:
    """Flip every compiled row back to pending; leave already-pending rows alone."""
    # 3 compiled with non-null compiled_at, 2 already pending.
    compiled_ts = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    for i in range(1, 4):
        _insert(db_conn, message_id=f"c{i}", compile_state="compiled")
        db_conn.execute(
            "UPDATE messages SET compiled_at = %s, last_error = %s WHERE message_id = %s",
            (compiled_ts, "stale-error", f"c{i}"),
        )
    _insert(db_conn, message_id="p1")
    _insert(db_conn, message_id="p2")
    db_conn.commit()

    # Sanity: starting state.
    assert repo.count_by_state() == {"compiled": 3, "pending": 2}

    rowcount = repo.reset_to_pending()
    assert rowcount == 3

    # All 5 are now pending.
    assert repo.count_by_state() == {"pending": 5}

    # The flipped rows have compiled_at and last_error cleared.
    for i in range(1, 4):
        row = _fetch_one(db_conn, f"c{i}")
        assert row["compile_state"] == "pending"
        assert row["compiled_at"] is None
        assert row["last_error"] is None


# ---------------------------------------------------------------------------
# reset_to_pending_by_path (targeted)
# ---------------------------------------------------------------------------


def test_mark_skipped_flips_state_and_is_excluded_from_claim(
    db_conn: psycopg.Connection,
) -> None:
    """Skipped rows carry the reason in last_error and are invisible to the
    claim loop (which only scans pending/failed/stale-claimed)."""
    _insert(db_conn, message_id="m_skip", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(db_conn, message_id="m_keep", date=datetime(2026, 2, 1, tzinfo=timezone.utc))
    db_conn.commit()

    rows_flipped = repo.mark_skipped("m_skip", "auto_sender")
    assert rows_flipped == 1

    row = _fetch_one(db_conn, "m_skip")
    assert row["compile_state"] == "skipped"
    assert row["last_error"] == "auto_sender"
    assert row["is_compiled"] is False

    # The claim loop must not pick up skipped rows — only m_keep is eligible.
    claimed = repo.claim_next_message(uuid.uuid4())
    assert claimed is not None
    assert claimed["message_id"] == "m_keep"

    # Nothing else claimable.
    assert repo.claim_next_message(uuid.uuid4()) is None

    # list_uncompiled and remaining_uncompiled_count also exclude skipped.
    remaining_ids = {r["message_id"] for r in repo.list_uncompiled()}
    assert "m_skip" not in remaining_ids


def test_reset_to_pending_by_path(db_conn: psycopg.Connection) -> None:
    """Only flip rows whose raw_path is in the supplied list."""
    raw_paths = [
        "raw/2026-04-01-aaa.md",
        "raw/2026-04-02-bbb.md",
        "raw/2026-04-03-ccc.md",
    ]
    compiled_ts = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    for idx, path in enumerate(raw_paths, start=1):
        _insert(
            db_conn,
            message_id=f"c{idx}",
            raw_path=path,
            compile_state="compiled",
        )
        db_conn.execute(
            "UPDATE messages SET compiled_at = %s WHERE message_id = %s",
            (compiled_ts, f"c{idx}"),
        )
    db_conn.commit()

    # Reset only the first and third paths.
    rowcount = repo.reset_to_pending_by_path([raw_paths[0], raw_paths[2]])
    assert rowcount == 2

    row1 = _fetch_one(db_conn, "c1")
    row2 = _fetch_one(db_conn, "c2")
    row3 = _fetch_one(db_conn, "c3")

    assert row1["compile_state"] == "pending"
    assert row1["compiled_at"] is None
    assert row3["compile_state"] == "pending"
    assert row3["compiled_at"] is None

    # Untouched.
    assert row2["compile_state"] == "compiled"
    assert row2["compiled_at"] == compiled_ts

    # Empty list short-circuits to 0 with no DB call needed.
    assert repo.reset_to_pending_by_path([]) == 0


# ---------------------------------------------------------------------------
# shared_thread_id_for_paths
# ---------------------------------------------------------------------------


def test_shared_thread_id_empty_returns_none() -> None:
    assert repo.shared_thread_id_for_paths([]) is None


def test_shared_thread_id_single_path(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", raw_path="raw/one.md", thread_id="T-A")
    db_conn.commit()
    assert repo.shared_thread_id_for_paths(["raw/one.md"]) == "T-A"


def test_shared_thread_id_all_same_thread(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", raw_path="raw/one.md", thread_id="T-A")
    _insert(db_conn, message_id="m2", raw_path="raw/two.md", thread_id="T-A")
    db_conn.commit()
    assert repo.shared_thread_id_for_paths(["raw/one.md", "raw/two.md"]) == "T-A"


def test_shared_thread_id_multi_thread_returns_none(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", raw_path="raw/one.md", thread_id="T-A")
    _insert(db_conn, message_id="m2", raw_path="raw/two.md", thread_id="T-B")
    db_conn.commit()
    assert repo.shared_thread_id_for_paths(["raw/one.md", "raw/two.md"]) is None


def test_shared_thread_id_missing_path_returns_none(db_conn: psycopg.Connection) -> None:
    """If any raw_path has no row, the batch is ambiguous — fall back to None."""
    _insert(db_conn, message_id="m1", raw_path="raw/one.md", thread_id="T-A")
    db_conn.commit()
    assert repo.shared_thread_id_for_paths(["raw/one.md", "raw/missing.md"]) is None


def test_shared_thread_id_null_thread_returns_none(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", raw_path="raw/one.md", thread_id=None)
    db_conn.commit()
    assert repo.shared_thread_id_for_paths(["raw/one.md"]) is None


def test_shared_thread_id_mixed_null_returns_none(db_conn: psycopg.Connection) -> None:
    """Codex P2 on PR #171: a batch where one row has a real thread
    and another has NULL thread_id must return None, not the non-NULL
    thread. Filtering NULLs silently violated the contract that every
    raw_path maps to one thread."""
    _insert(db_conn, message_id="m1", raw_path="raw/one.md", thread_id="T-A")
    _insert(db_conn, message_id="m2", raw_path="raw/two.md", thread_id=None)
    db_conn.commit()
    assert repo.shared_thread_id_for_paths(["raw/one.md", "raw/two.md"]) is None
