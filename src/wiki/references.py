"""Wiki ``## References`` helpers — shared by the coordinator backfill and
the mkdocs hook.

Mkdocs renders ``## References`` from inline ``[^msg-xxx]`` markers at
build time only. The coordinator backfill persists the same defs into
the .md source after every batch so non-mkdocs viewers (GitHub, plain
``cat``) see verifiable citations too. Both call sites import the
regex / raw-index / render primitives below so they can't drift.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.utils import split_frontmatter

# Stdlib logging here so call sites (coordinator + mkdocs hook) can capture
# warnings via ``caplog`` in tests. The coordinator wrapper in
# ``src.coordinator.post_batch`` still emits its own structlog event for
# top-level batch telemetry.
_log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_DIR = REPO_ROOT / "raw"

# Inline footnote markers in body prose: ``[^msg-cda09a3d]``. The trailing
# ``(?!:)`` lookahead skips the definition shape (``[^msg-x]: …``) so we only
# pick up usages. Hash is the 8-char raw-filename suffix, e.g. ``cda09a3d``.
FOOTNOTE_USE_RE = re.compile(r"\[\^(msg-[a-z0-9-]+)\](?!:)", re.IGNORECASE)
FOOTNOTE_DEF_RE = re.compile(r"^\[\^(msg-[a-z0-9-]+)\]:", re.IGNORECASE | re.MULTILINE)
_H2_REFERENCES_RE = re.compile(r"^##\s+References\b", re.MULTILINE)
_H2_NEXT_RE = re.compile(r"^##\s+", re.MULTILINE)

# Cache `<short_id>` → `raw/<filename>.md` per raw dir. Raw files are
# immutable post-ingest, so an in-memory cache is safe across an entire
# build / coordinator run. Single-writer (post-batch coordinator is
# single-threaded; mkdocs builds are single-process) — no lock needed.
_raw_index_cache: dict[Path, dict[str, str]] = {}


def clear_raw_index_cache() -> None:
    """Reset the raw-index cache. Tests stage their own ``raw/`` fixtures
    and need a clean slate between runs."""
    _raw_index_cache.clear()


def build_raw_index(raw_dir: Path) -> dict[str, str]:
    """Map the 8-char hash suffix of every raw file to its repo-relative
    path. Cached per ``raw_dir`` — raw files are immutable post-ingest.

    ``raw_dir`` is the actual directory containing ``YYYY-MM-DD_<subj>_<hash>.md``
    files; pass ``settings.raw_dir`` so a non-default ``RAW_DIR`` env var
    is honoured.
    """
    key = raw_dir.resolve()
    cached = _raw_index_cache.get(key)
    if cached is not None:
        return cached
    index: dict[str, str] = {}
    if raw_dir.exists():
        for path in raw_dir.glob("*.md"):
            stem = path.stem
            if "_" not in stem:
                continue
            short = stem.rsplit("_", 1)[-1]
            if short:
                index[short.lower()] = f"raw/{path.name}"
    _raw_index_cache[key] = index
    return index


def resolve_footnote_path(short_hash: str, raw_dir: Path) -> str | None:
    """Return ``raw/<file>.md`` for a footnote hash, or None if unknown.

    ``short_hash`` may include the ``msg-`` prefix or not.
    """
    index = build_raw_index(raw_dir)
    key = short_hash.removeprefix("msg-").lower()
    return index.get(key)


def ordered_unique_footnotes(body: str) -> list[str]:
    """Inline footnote tags (``msg-xxxxxxxx``) in first-appearance order.

    De-duplicates so a tag cited five times only renders once in References.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for match in FOOTNOTE_USE_RE.finditer(body):
        tag = match.group(1).lower()
        if tag not in seen:
            seen.add(tag)
            ordered.append(tag)
    return ordered


def _render_def_line(tag: str, raw_dir: Path, page_path: str = "") -> str:
    """One ``[^msg-x]: <path>`` line, with the unknown-raw fallback."""
    path = resolve_footnote_path(tag, raw_dir)
    if path:
        return f"[^{tag}]: `{path}`"
    if page_path:
        _log.warning(
            "references: footnote [^%s] on %s has no matching raw file",
            tag,
            page_path,
        )
    return f"[^{tag}]: *(raw file not found for `{tag}`)*"


def render_references_block(
    footnotes: list[str], raw_dir: Path, page_path: str = ""
) -> str:
    """Render a ``## References`` block from inline footnote tags.

    Each tag becomes a footnote definition pointing to its raw email path.
    Unresolvable tags still render with a clear marker so MkDocs / readers
    don't drop the citation silently.
    """
    lines = ["", "## References", ""]
    for tag in footnotes:
        lines.append(_render_def_line(tag, raw_dir, page_path))
    lines.append("")
    return "\n".join(lines)


def _existing_def_tags(body: str) -> set[str]:
    return {m.group(1).lower() for m in FOOTNOTE_DEF_RE.finditer(body)}


def _split_at_references_h2(body: str) -> tuple[str, str, str] | None:
    """Return (before, references_block, tail) when a ``## References`` H2
    exists, else None. ``references_block`` runs from the H2 line through
    just before the next H2 (or EOF).
    """
    match = _H2_REFERENCES_RE.search(body)
    if match is None:
        return None
    start = match.start()
    rest = body[match.end() :]
    next_h2 = _H2_NEXT_RE.search(rest)
    end = match.end() + next_h2.start() if next_h2 else len(body)
    return body[:start], body[start:end], body[end:]


def backfill_references(page_path: Path, raw_dir: Path) -> bool:
    """Append/extend ``## References`` to cover every body ``[^msg-xxx]``.

    ``raw_dir`` points to the actual directory containing the raw email
    files (i.e. ``settings.raw_dir`` resolved to an absolute path),
    honouring a non-default ``RAW_DIR`` env override.

    Idempotent: returns False when the page already has every def. When
    a ``## References`` H2 exists, append only the missing defs into it
    (preserving any hand-authored def text). Otherwise, append a fresh
    H2 + defs at the end of the body.

    Returns True iff the page was rewritten.
    """
    try:
        content = page_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    # Cheap early-out before frontmatter parse: most touched pages have no
    # footnote refs at all (domain hubs, glossary, decision stubs).
    if not FOOTNOTE_USE_RE.search(content):
        return False

    # Preserve frontmatter as raw text. Using `split_frontmatter` directly
    # (rather than parse → re-emit) round-trips malformed YAML losslessly —
    # `extract_frontmatter` returns ``{}`` on a parse failure, but
    # ``extract_body`` still strips the frontmatter block, so a YAML error
    # would otherwise drop the whole metadata block on rewrite.
    fm_text, body = split_frontmatter(content)
    refs = ordered_unique_footnotes(body)
    if not refs:
        return False
    existing_defs = _existing_def_tags(body)
    missing = [tag for tag in refs if tag not in existing_defs]
    if not missing:
        return False

    page_relpath = _safe_relpath(page_path, raw_dir.parent)

    split = _split_at_references_h2(body)
    if split is not None:
        before, section, tail = split
        new_lines = [_render_def_line(t, raw_dir, page_relpath) for t in missing]
        # Trailing "\n\n" preserves the blank line between the last def and
        # any following H2 (e.g. ## Related). Without it mdlint flags
        # MD022 (no-blank-before-heading) on the appended block.
        section_extended = section.rstrip() + "\n" + "\n".join(new_lines) + "\n\n"
        new_body = before + section_extended + tail
    else:
        block = render_references_block(missing, raw_dir, page_relpath)
        new_body = body.rstrip() + "\n" + block

    new_content = f"---\n{fm_text}---\n\n{new_body}" if fm_text else new_body
    if new_content == content:
        return False
    try:
        page_path.write_text(new_content, encoding="utf-8")
    except OSError:
        return False
    return True


def _safe_relpath(page_path: Path, repo_root: Path) -> str:
    """Best-effort repo-relative path for log/error messages."""
    try:
        return str(page_path.relative_to(repo_root))
    except ValueError:
        return str(page_path)
