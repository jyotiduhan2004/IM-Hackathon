"""Tests for the post-batch normalization + validation hooks in compile_all.py.

Context: the compile agent still writes pages that fail validators (duplicate
``## Related`` headings, broken wikilinks, malformed frontmatter) despite a
prompt rule against it. ``_iter_touched_pages`` collects every wiki page whose
mtime advanced during a batch; ``_normalize_touched_pages`` runs the
idempotent formatter over them to catch the most common format drift before
the next batch sees it; ``_validate_touched_pages`` surfaces anything the
formatter couldn't auto-fix so operators notice immediately. The validator
runs on the full touched-page set (not just the formatter-normalized subset)
so corruption on pages the formatter skips or leaves alone still gets
reported.

These tests cover the ``mtime >= batch_start`` filter, the formatter firing
on touched pages, the "validator sees every touched page" contract, and the
"validator failure doesn't crash the batch" guarantee.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


@pytest.fixture
def wiki_dir(tmp_path: Path) -> Path:
    """Build an empty wiki tree with the standard category subdirs."""
    wiki = tmp_path / "wiki"
    for cat in ("topics", "entities", "systems", "policies", "timelines", "conflicts"):
        (wiki / cat).mkdir(parents=True)
    return wiki


def _write_page(path: Path, title: str, page_type: str, body: str) -> None:
    """Write a minimal valid wiki page with frontmatter + body."""
    path.write_text(
        f"---\ntitle: {title}\npage_type: {page_type}\nstatus: current\n---\n\n{body}",
        encoding="utf-8",
    )


def _backdate(path: Path, seconds: int) -> None:
    """Push a file's mtime backwards by ``seconds``."""
    target = time.time() - seconds
    os.utime(path, (target, target))


def _set_mtime(path: Path, mtime: float) -> None:
    """Pin a file's mtime (and atime) to a specific epoch value.

    Replaces the sleep-then-touch pattern: explicit mtime is both faster
    and robust against loaded-CI clock jitter where a 50ms sleep can
    fail to advance the mtime past ``batch_start``.
    """
    os.utime(path, (mtime, mtime))


def test_iter_touched_pages_skips_untouched(compile_all_module, wiki_dir: Path) -> None:
    """Pages whose mtime predates ``batch_start`` must be filtered out.

    Guards the contract that upstream helpers (formatter, validator) see
    only pages touched DURING the batch, not every page in the wiki.
    """
    mod = compile_all_module
    stale = wiki_dir / "topics" / "stale.md"
    _write_page(
        stale,
        title="Stale",
        page_type="topic",
        body=("Stale is an old topic. Two sentences required.\n\n## Related\n\n- [[nowhere]]\n"),
    )
    _backdate(stale, 60)

    batch_start = time.time() - 1
    touched = mod._iter_touched_pages(batch_start, wiki_dir)

    assert touched == []


def test_iter_touched_pages_includes_new(compile_all_module, wiki_dir: Path) -> None:
    """Pages whose mtime advanced at/after ``batch_start`` are returned."""
    mod = compile_all_module
    page = wiki_dir / "topics" / "fresh.md"
    _write_page(page, title="Fresh", page_type="topic", body="Body\n")

    batch_start = time.time()
    _set_mtime(page, batch_start + 1)

    touched = mod._iter_touched_pages(batch_start, wiki_dir)
    assert page in touched


def test_iter_touched_pages_ignores_policies(compile_all_module, wiki_dir: Path) -> None:
    """Iter scope is topics/entities/systems only; policies/timelines/conflicts
    keep their own templates and must not be scanned."""
    mod = compile_all_module
    policy = wiki_dir / "policies" / "p.md"
    _write_page(
        policy,
        title="P",
        page_type="policy",
        body="Policy P covers a rule. Two sentences.\n",
    )

    batch_start = time.time() - 60  # policy is newer than batch_start
    touched = mod._iter_touched_pages(batch_start, wiki_dir)
    assert policy not in touched


def test_iter_touched_pages_returns_empty_when_wiki_missing(
    compile_all_module, tmp_path: Path
) -> None:
    """Missing wiki dir → empty list, no exception (matches stamp helper)."""
    mod = compile_all_module
    result = mod._iter_touched_pages(time.time(), tmp_path / "does-not-exist")
    assert result == []


def test_normalize_touched_pages_processes_new(compile_all_module, wiki_dir: Path) -> None:
    """The formatter rewrites an agent-written page with nav-section drift."""
    mod = compile_all_module
    page = wiki_dir / "topics" / "fresh.md"
    _write_page(
        page,
        title="Fresh",
        page_type="topic",
        body=(
            "Fresh is a topic the agent just wrote. Two sentences.\n\n"
            "## Related\n\n- [[other]]\n\n"
            "## People\n\n- [[jane-doe]]\n"
        ),
    )

    normalized = mod._normalize_touched_pages([page], wiki_dir)

    # The formatter stripped the agent-written nav sections.
    assert page in normalized
    content = page.read_text(encoding="utf-8")
    assert "## People" not in content


def test_normalize_touched_pages_noop_on_clean_page(compile_all_module, wiki_dir: Path) -> None:
    """A page already in canonical form must NOT be returned as changed —
    the validator will still see it via the broader touched-page set."""
    mod = compile_all_module
    clean = wiki_dir / "topics" / "clean.md"
    # Minimal body: the formatter should find nothing to rewrite.
    _write_page(clean, title="Clean", page_type="topic", body="Just a sentence.\n")

    normalized = mod._normalize_touched_pages([clean], wiki_dir)
    # Formatter is idempotent on clean pages → not reported as changed.
    assert clean not in normalized


def test_validate_hook_returns_error_map(compile_all_module, wiki_dir: Path) -> None:
    """``_validate_touched_pages`` returns a page→errors dict for bad pages,
    and omits clean pages entirely."""
    mod = compile_all_module
    clean = wiki_dir / "topics" / "clean.md"
    _write_page(
        clean,
        title="Clean",
        page_type="topic",
        body="Clean is a fine page. It has enough frontmatter.\n",
    )
    bad = wiki_dir / "topics" / "bad.md"
    # Malformed frontmatter: three fences instead of two — the "extra ---"
    # pattern that's one of the top-three agent errors we're trying to catch.
    bad.write_text(
        "---\ntitle: Bad\npage_type: topic\nstatus: current\n---\n"
        "---\nstray fence above body\n---\n\nBody.\n",
        encoding="utf-8",
    )

    errors_by_page = mod._validate_touched_pages([clean, bad], wiki_dir)

    assert clean not in errors_by_page
    assert bad in errors_by_page
    assert any("fence" in e.reason.lower() for e in errors_by_page[bad])


def test_validate_hook_skips_crashes_without_raising(
    compile_all_module, wiki_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A buggy validator must not tear down the batch — exceptions are
    swallowed and logged, and the affected page is simply omitted from the
    result map."""
    mod = compile_all_module

    def _boom(path: Path) -> list:
        del path  # unused — test stub raises regardless
        raise RuntimeError("validator exploded")

    monkeypatch.setattr(mod, "validate_page", _boom)
    page = wiki_dir / "topics" / "any.md"
    _write_page(page, title="Any", page_type="topic", body="body\n")

    # Must not raise.
    errors_by_page = mod._validate_touched_pages([page], wiki_dir)
    assert errors_by_page == {}


def test_validate_runs_on_pages_formatter_skipped(compile_all_module, wiki_dir: Path) -> None:
    """The validator must see every touched page, not just the subset the
    formatter rewrote.

    This is the Codex P2 regression: pages the formatter leaves alone
    (already-clean pages OR malformed pages the formatter skips) are still
    part of the batch's footprint and can carry agent-introduced corruption.
    Feeding only ``normalized`` pages into the validator would let that
    corruption slip through silently.

    Exercise: create a page with NO frontmatter — ``format_file`` returns a
    ``skipped_reason`` so ``format_page`` returns False and it never lands
    in ``normalized``. Then prove the validator still flags it when the
    call site passes the broader touched set.
    """
    mod = compile_all_module

    # Page with no frontmatter — format_file returns skipped_reason
    # ("unparseable frontmatter"), so format_page returns False and this
    # page is NOT in `normalized`. The validator should still see it.
    skipped = wiki_dir / "topics" / "no-frontmatter.md"
    skipped.write_text("Just body text, no frontmatter at all.\n", encoding="utf-8")

    # Page the formatter leaves alone because it's already canonical.
    clean = wiki_dir / "topics" / "clean.md"
    _write_page(clean, title="Clean", page_type="topic", body="Sentence one.\n")

    batch_start = time.time()
    _set_mtime(skipped, batch_start + 1)
    _set_mtime(clean, batch_start + 1)

    touched = mod._iter_touched_pages(batch_start, wiki_dir)
    normalized = mod._normalize_touched_pages(touched, wiki_dir)
    # Sanity: the formatter didn't rewrite either page — proves the
    # validator would MISS them if we only fed it `normalized`.
    assert skipped not in normalized
    assert clean not in normalized

    # The production call site feeds `touched` (not `normalized`) into the
    # validator so corruption on formatter-skipped pages is surfaced.
    errors_by_page = mod._validate_touched_pages(touched, wiki_dir)
    assert skipped in errors_by_page, (
        "validator must surface errors on pages the formatter skipped; "
        "otherwise Codex P2 regression returns"
    )


def test_validate_hook_logs_errors_but_does_not_fail_batch(
    compile_all_module,
    wiki_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end post-batch flow on a broken page: the formatter runs, the
    validator flags the page, a structured warning is logged, and the call
    returns cleanly so the batch can continue.

    Simulates the user's ship scenario: the compile agent wrote a
    three-fence page (top-three error class); the hook must catch and
    surface it without rolling back the commit. structlog uses
    ``ConsoleRenderer`` which writes to stdout/stderr directly, so we
    assert on ``capsys`` rather than the stdlib ``caplog`` bridge.
    """
    mod = compile_all_module
    page = wiki_dir / "topics" / "three-fence.md"
    page.write_text(
        "---\ntitle: Three\npage_type: topic\nstatus: current\n---\n"
        "---\nstray fence\n---\n\nBody text.\n",
        encoding="utf-8",
    )
    batch_start = time.time()
    _set_mtime(page, batch_start + 1)  # pin mtime past batch_start

    touched = mod._iter_touched_pages(batch_start, wiki_dir)
    normalized = mod._normalize_touched_pages(touched, wiki_dir)
    del normalized  # not used — validator runs on the broader touched set
    errors_by_page = mod._validate_touched_pages(touched, wiki_dir)

    assert page in errors_by_page
    # The batch would mark "compiled" at the call site; validator errors
    # don't flip the outcome, they only surface in logs + batch notes.
    # Mimic the production call to confirm the structlog renderer emits.
    mod.logger.warning(
        "batch touched pages have validator errors",
        batch_index=1,
        errors=[
            {"page": str(p), "reasons": [e.reason for e in errs]}
            for p, errs in errors_by_page.items()
        ],
    )
    captured = capsys.readouterr()
    # The rendered line carries the message verbatim + the error reasons.
    combined = captured.out + captured.err
    assert "validator errors" in combined
    assert "fence" in combined  # reason-level detail surfaced for the operator


# --- coordinator-side ## References backfill ----------------------------


@pytest.fixture
def staged_raw_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Repoint ``settings.raw_dir`` at a stand-alone tmp ``raw/`` dir and
    clear the shared raw-index cache so each test stages its own
    fixtures."""
    from src.config import settings as _settings
    from src.wiki import references as refs_mod

    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(_settings, "raw_dir", raw)
    refs_mod.clear_raw_index_cache()
    return tmp_path


def test_backfill_references_appends_h2_when_missing(
    compile_all_module, wiki_dir: Path, staged_raw_repo: Path
) -> None:
    """Touched page has body refs and no defs — coordinator hook adds them."""
    (staged_raw_repo / "raw" / "2026-04-01_subj_aaaa.md").write_text("x", encoding="utf-8")

    page = wiki_dir / "topics" / "foo.md"
    _write_page(page, "Foo", "topic", "Body cites [^msg-aaaa] here.\n")
    changed = compile_all_module._backfill_references_on_touched_pages([page], wiki_dir)
    assert changed == 1
    txt = page.read_text(encoding="utf-8")
    assert "## References" in txt
    assert "[^msg-aaaa]: `raw/2026-04-01_subj_aaaa.md`" in txt


def test_backfill_references_noop_on_clean_page(
    compile_all_module, wiki_dir: Path, staged_raw_repo: Path
) -> None:
    """Page with no body refs is left alone (returns 0 changed)."""
    page = wiki_dir / "topics" / "clean.md"
    _write_page(page, "Clean", "topic", "Just prose, no refs.\n")
    before = page.read_text(encoding="utf-8")
    assert compile_all_module._backfill_references_on_touched_pages([page], wiki_dir) == 0
    assert page.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# _refresh_qmd_index — Codex P2 / P3 (PR #289)
# ---------------------------------------------------------------------------


def test_refresh_qmd_index_short_circuits_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-zero ``qmd update`` must skip ``qmd embed`` (embedding
    against a half-known corpus would re-vectorise stale state) and
    log a warning instead of swallowing the failure. Codex P2 on PR #289.

    The behaviour contract is: exactly ONE subprocess call when the
    first one returns non-zero. The warning emission is exercised at
    the same code path; pinning argv-count is what protects the
    silent-failure regression Codex flagged.
    """
    import subprocess
    from unittest.mock import patch

    from src.config import settings as _s
    from src.coordinator import post_batch

    monkeypatch.setattr(_s, "use_semantic_resolve", True)

    fake_proc = subprocess.CompletedProcess(
        args=["qmd", "update"], returncode=2, stdout="", stderr="index corrupt"
    )
    with (
        patch("shutil.which", return_value="/usr/local/bin/qmd"),
        patch("subprocess.run", return_value=fake_proc) as run_mock,
        patch.object(post_batch.logger, "warning") as warn_mock,
    ):
        post_batch._refresh_qmd_index()

    assert run_mock.call_count == 1, "qmd embed must not run after qmd update fails"
    assert warn_mock.called, "non-zero exit must surface as a warning"
    args, kwargs = warn_mock.call_args
    assert args[0] == "qmd_reindex_nonzero_exit"
    assert kwargs["returncode"] == 2


def test_refresh_qmd_index_runs_both_steps_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: both ``qmd update`` and ``qmd embed`` run when
    ``use_semantic_resolve`` is on and update succeeds."""
    import subprocess
    from unittest.mock import patch

    from src.config import settings as _s
    from src.coordinator import post_batch

    monkeypatch.setattr(_s, "use_semantic_resolve", True)
    fake_ok = subprocess.CompletedProcess(args=["qmd"], returncode=0, stdout="", stderr="")
    with (
        patch("shutil.which", return_value="/usr/local/bin/qmd"),
        patch("subprocess.run", return_value=fake_ok) as run_mock,
    ):
        post_batch._refresh_qmd_index()
    assert [c.args[0] for c in run_mock.call_args_list] == [["qmd", "update"], ["qmd", "embed"]]


def test_refresh_qmd_index_skips_when_semantic_resolve_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No subprocess calls when ``use_semantic_resolve=False`` — keeps
    the hook free for users who haven't opted into qmd."""
    from unittest.mock import patch

    from src.config import settings as _s
    from src.coordinator import post_batch

    monkeypatch.setattr(_s, "use_semantic_resolve", False)
    with patch("subprocess.run") as run_mock:
        post_batch._refresh_qmd_index()
    assert run_mock.call_count == 0


def test_refresh_qmd_index_skips_when_qmd_not_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No subprocess calls when ``qmd`` binary is missing — production
    images without the dev extra shouldn't crash on the hook."""
    from unittest.mock import patch

    from src.config import settings as _s
    from src.coordinator import post_batch

    monkeypatch.setattr(_s, "use_semantic_resolve", True)
    with (
        patch("shutil.which", return_value=None),
        patch("subprocess.run") as run_mock,
    ):
        post_batch._refresh_qmd_index()
    assert run_mock.call_count == 0
