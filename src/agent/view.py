"""Per-run filesystem view-root construction for the compile agent.

Extracted from the legacy `src/compile/compiler.py` (Phase 1C). Builds the
chrooted view the FilesystemBackend uses, plus the preflights that
fail-fast when the mount is wrong.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def _build_compile_view(raw_dir: Path, wiki_dir: Path) -> Path:
    """Resolve the filesystem view-root for the compile agent.

    Returns the common parent of the resolved raw_dir and wiki_dir, which
    is what FilesystemBackend's virtual_mode uses as `root_dir`. Inside the
    view, the agent sees `/raw` and `/wiki` as virtual paths.

    Why not a tempdir + symlinks: FilesystemBackend resolves symlinks then
    checks `relative_to(root_dir)`. A symlink target outside the tempdir
    fails that check (`Path:... outside root directory: /tmp/...`). We
    instead anchor `root_dir` at the real common parent so resolution
    stays inside.

    The host path is still hidden from the LLM via the prompt + the
    path_autoheal middleware, which rewrites accidental host-prefix leaks
    back to virtual `/raw/...` / `/wiki/...` form.
    """
    raw_real = raw_dir.resolve()
    wiki_real = wiki_dir.resolve()
    if raw_real.parent != wiki_real.parent:
        # Non-default layout (raw and wiki in different parent dirs). Fall
        # back to cwd so the backend at least doesn't reject every call.
        # Operator should fix the layout — log so this is loud.
        logger.warning(
            "compile_view_mismatched_parents",
            raw_parent=str(raw_real.parent),
            wiki_parent=str(wiki_real.parent),
            falling_back_to=str(Path.cwd().resolve()),
        )
        return Path.cwd().resolve()
    return raw_real.parent


def _cleanup_compile_view(view_root: Path) -> None:
    """No-op when the view-root is a real repo dir (the typical case).

    Kept for forward compatibility — earlier iterations of the design used
    a tempdir + symlinks per run. If we ever revive that approach, the
    cleanup goes here. Today the view-root is the repo's working dir (or
    a parent of raw/+wiki/) and must NOT be deleted.
    """
    _ = view_root  # placeholder — see docstring


def _count_view_raw_md_files(view_root: Path) -> int:
    """Count ``.md`` files at top-level of ``view_root/raw``.

    Reported as ``mounted_raw_file_count`` in trace metadata so Langfuse
    can correlate "agent wrote nothing" traces with a zero-file mount.
    Top-level-only — attachments/ holds binaries the agent can't read.
    """
    raw_root = view_root / "raw"
    if not raw_root.exists():
        return 0
    return sum(1 for _ in raw_root.glob("*.md"))


def _preflight_view_resolves_paths(view_root: Path, raw_paths: list[str]) -> None:
    """Assert every batch raw_path resolves inside ``view_root``.

    The agent's filesystem is chrooted to ``view_root`` (FilesystemBackend's
    ``root_dir`` with ``virtual_mode=True``). If a batch path lives outside
    the view, every `read_file("/raw/...md")` the agent tries will
    silently fail because the chroot rejects it. Count mismatches and
    abort with a clear error so we don't waste an LLM call discovering it.
    """
    view_root_abs = view_root.resolve()
    missing = []
    for p in raw_paths:
        resolved = Path(p).resolve()
        try:
            resolved.relative_to(view_root_abs)
        except ValueError:
            missing.append(p)
    if missing:
        raise RuntimeError(
            f"view-root /raw is missing {len(missing)} of {len(raw_paths)} "
            f"expected raw paths (view_root={view_root_abs}); "
            f"first few: {missing[:3]}"
        )
