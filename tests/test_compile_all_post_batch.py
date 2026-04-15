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

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_compile_all():
    """Load scripts/compile_all.py as a module so we can test its helpers."""
    path = REPO_ROOT / "scripts" / "compile_all.py"
    spec = importlib.util.spec_from_file_location("_compile_all_for_post_batch_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_compile_all_for_post_batch_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def compile_all_module():
    return _load_compile_all()


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


def test_validate_runs_on_pages_formatter_skipped(
    compile_all_module, wiki_dir: Path
) -> None:
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
