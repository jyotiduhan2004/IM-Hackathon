"""Phase A U5 — source_threads: as page-level citation field.

Tests cover three axes:

- Shape validation: well-formed `source_threads:` (16-char hex entries) passes
  validation cleanly; malformed entries surface as ERRORs regardless of flags.
- Legacy co-existence: pages with `sources:` but no `source_threads:` fire the
  `legacy-sources-only` warning by default (tolerant reads during the U6
  backfill window).
- Strict-mode enforcement: the `--strict-no-sources` CLI flag promotes the
  warning to an ERROR — intended for post-U6 hardening when every page
  should have been migrated.

The new field pattern mirrors PR #124 (`--strict-new-ontology`) so the
validator's flag surface stays additive and predictable.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_validator():  # type: ignore[no-untyped-def]
    """Import scripts/validate_wiki.py as a module.

    Same trick as the legacy-shape tests: register in sys.modules before exec
    so dataclasses resolve forward references when the script isn't on
    PYTHONPATH.
    """
    spec = importlib.util.spec_from_file_location(
        "validate_wiki", REPO_ROOT / "scripts" / "validate_wiki.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_wiki"] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _write_page(
    cat_dir: Path,
    slug: str,
    *,
    page_type: str = "topic",
    status: str = "active",
    extra_fm_lines: list[str] | None = None,
    body: str | None = None,
) -> Path:
    """Write a minimal wiki page with the requested frontmatter."""
    lines = [
        "---",
        f"title: {slug.replace('-', ' ').title()}",
        f"page_type: {page_type}",
        f"status: {status}",
    ]
    if extra_fm_lines:
        lines.extend(extra_fm_lines)
    lines += ["---", "", body or f"Body for {slug}.", ""]
    path = cat_dir / f"{slug}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Task 1 — well-formed source_threads validates cleanly
# ---------------------------------------------------------------------------


def test_source_threads_valid_shape_passes(mini_wiki: Path) -> None:
    """Page with only `source_threads:` (no legacy sources) validates cleanly."""
    page = _write_page(
        mini_wiki / "topics",
        "u5-smoke",
        extra_fm_lines=[
            "source_threads:",
            "- '19b59cdc863ac109'",
        ],
    )
    errors = validator.validate_page(page)
    assert errors == [], f"unexpected errors: {errors!r}"


def test_source_threads_multiple_valid_thread_ids(mini_wiki: Path) -> None:
    """Multiple well-formed thread_ids: all accepted."""
    page = _write_page(
        mini_wiki / "topics",
        "multi-thread",
        extra_fm_lines=[
            "source_threads:",
            "- '19b59cdc863ac109'",
            "- '19aee0c7cc330376'",
            "- 'abcdef0123456789'",
        ],
    )
    errors = validator.validate_page(page)
    assert errors == []


# ---------------------------------------------------------------------------
# Task 2 — malformed source_threads produces ERRORs
# ---------------------------------------------------------------------------


def test_source_threads_non_hex_entry_errors(mini_wiki: Path) -> None:
    """A non-hex entry (uppercase / wrong length) is a hard error."""
    page = _write_page(
        mini_wiki / "topics",
        "bad-thread",
        extra_fm_lines=[
            "source_threads:",
            "- 'NOT-A-HEX-THREAD'",
        ],
    )
    errors = validator.validate_page(page)
    assert any("source_threads" in e.reason and "invalid" in e.reason for e in errors), (
        f"expected source_threads error, got {errors!r}"
    )


def test_source_threads_wrong_length_errors(mini_wiki: Path) -> None:
    """Thread ids must be 16 chars exactly — 15 or 17 chars fails."""
    page = _write_page(
        mini_wiki / "topics",
        "short-thread",
        extra_fm_lines=[
            "source_threads:",
            "- '19b59cdc863ac10'",  # 15 chars
        ],
    )
    errors = validator.validate_page(page)
    assert any("source_threads" in e.reason for e in errors)


def test_source_threads_not_a_list_errors(mini_wiki: Path) -> None:
    """If `source_threads:` parses as a scalar, we flag the shape."""
    page = _write_page(
        mini_wiki / "topics",
        "scalar-threads",
        extra_fm_lines=[
            "source_threads: '19b59cdc863ac109'",  # scalar, not list
        ],
    )
    errors = validator.validate_page(page)
    assert any("source_threads" in e.reason and "list" in e.reason for e in errors)


# ---------------------------------------------------------------------------
# Task 3 — legacy-sources-only warning (tolerant reads)
# ---------------------------------------------------------------------------


def test_legacy_sources_only_warns_by_default(mini_wiki: Path) -> None:
    """Page with `sources:` but no `source_threads:` → warning, zero errors."""
    _write_page(
        mini_wiki / "topics",
        "legacy-page",
        extra_fm_lines=[
            "sources:",
            "- raw/2026-01-01_hello_abc123.md",
        ],
    )
    errors, warnings = validator.check_legacy_sources_only(mini_wiki, strict=False)
    assert errors == []
    assert len(warnings) == 1
    w = warnings[0]
    assert w.check == "legacy-sources-only"
    assert w.page.name == "legacy-page.md"
    assert "source_threads backfill" in w.reason


def test_page_with_both_fields_stays_clean(mini_wiki: Path) -> None:
    """Dual-writing (sources: AND source_threads:) during migration is fine."""
    _write_page(
        mini_wiki / "topics",
        "dual-write",
        extra_fm_lines=[
            "sources:",
            "- raw/2026-01-01_hello_abc123.md",
            "source_threads:",
            "- '19b59cdc863ac109'",
        ],
    )
    errors, warnings = validator.check_legacy_sources_only(mini_wiki, strict=False)
    assert errors == []
    assert warnings == []


def test_page_with_only_source_threads_stays_clean(mini_wiki: Path) -> None:
    """The canonical post-U6 shape: `source_threads:` only, no warning."""
    _write_page(
        mini_wiki / "topics",
        "new-shape",
        extra_fm_lines=[
            "source_threads:",
            "- '19b59cdc863ac109'",
        ],
    )
    errors, warnings = validator.check_legacy_sources_only(mini_wiki, strict=False)
    assert errors == []
    assert warnings == []


def test_page_with_empty_sources_stays_clean(mini_wiki: Path) -> None:
    """An empty `sources:` list isn't evidence of legacy-shape; skip the warning."""
    _write_page(
        mini_wiki / "topics",
        "empty-sources",
        extra_fm_lines=[
            "sources: []",
        ],
    )
    errors, warnings = validator.check_legacy_sources_only(mini_wiki, strict=False)
    assert errors == []
    assert warnings == []


# ---------------------------------------------------------------------------
# Task 4 — strict mode promotes to ERRORs
# ---------------------------------------------------------------------------


def test_strict_no_sources_promotes_to_error(mini_wiki: Path) -> None:
    _write_page(
        mini_wiki / "topics",
        "legacy-page",
        extra_fm_lines=[
            "sources:",
            "- raw/2026-01-01_hello_abc123.md",
        ],
    )
    errors, warnings = validator.check_legacy_sources_only(mini_wiki, strict=True)
    assert warnings == []
    assert len(errors) == 1
    e = errors[0]
    assert e.page.name == "legacy-page.md"
    assert "legacy-sources-only" in e.reason


def test_strict_mode_run_returns_nonempty_errors(mini_wiki: Path) -> None:
    """Full run() with strict_no_sources=True surfaces legacy pages as errors."""
    _write_page(
        mini_wiki / "topics",
        "legacy-page",
        extra_fm_lines=[
            "sources:",
            "- raw/2026-01-01_hello_abc123.md",
        ],
    )
    errors, _ = validator.run(mini_wiki, strict_no_sources=True)
    assert any("legacy-sources-only" in e.reason for e in errors), (
        f"expected legacy-sources-only error, got {errors!r}"
    )


# ---------------------------------------------------------------------------
# CLI surface — flag default and strict behavior
# ---------------------------------------------------------------------------


def _run_cli(wiki_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke scripts/validate_wiki.py against `wiki_dir` via subprocess."""
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "validate_wiki.py"), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "WIKI_DIR": str(wiki_dir)},
        check=False,
    )


def test_cli_default_exits_zero_on_legacy_sources_only(mini_wiki: Path) -> None:
    """A wiki with ONLY legacy `sources:` exits 0 by default (warn-only)."""
    _write_page(
        mini_wiki / "topics",
        "legacy-page",
        extra_fm_lines=[
            "sources:",
            "- raw/2026-01-01_hello_abc123.md",
        ],
    )
    result = _run_cli(mini_wiki)
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "legacy-sources-only" in combined


def test_cli_strict_no_sources_exits_nonzero(mini_wiki: Path) -> None:
    """`--strict-no-sources` must exit 1 when legacy-sources-only pages exist."""
    _write_page(
        mini_wiki / "topics",
        "legacy-page",
        extra_fm_lines=[
            "sources:",
            "- raw/2026-01-01_hello_abc123.md",
        ],
    )
    result = _run_cli(mini_wiki, "--strict-no-sources")
    assert result.returncode == 1, (
        f"expected exit 1 under --strict-no-sources, got {result.returncode}\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "legacy-page.md" in combined
    assert "legacy-sources-only" in combined


def test_cli_strict_flag_default_off(mini_wiki: Path) -> None:
    """`--strict-no-sources` default (OFF) must not break clean wikis."""
    _write_page(
        mini_wiki / "topics",
        "new-shape",
        extra_fm_lines=[
            "source_threads:",
            "- '19b59cdc863ac109'",
        ],
    )
    assert _run_cli(mini_wiki).returncode == 0
    assert _run_cli(mini_wiki, "--strict-no-sources").returncode == 0
