from pathlib import Path

from src.config import _discover_env_file
from src.config import settings


def test_discover_env_file_prefers_local_env(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    local_env = repo_root / ".env"
    local_env.write_text("LANGFUSE_ENABLED=true\n", encoding="utf-8")

    assert _discover_env_file(repo_root) == str(local_env)


def test_discover_env_file_falls_back_to_main_checkout_env_for_worktree(tmp_path: Path) -> None:
    main_repo = tmp_path / "email-knowledge-base"
    main_repo.mkdir()
    shared_env = main_repo / ".env"
    shared_env.write_text("LANGFUSE_ENABLED=true\n", encoding="utf-8")

    git_dir = main_repo / ".git" / "worktrees" / "feature"
    git_dir.mkdir(parents=True)
    (git_dir / "commondir").write_text("../..", encoding="utf-8")

    worktree = tmp_path / "worktrees" / "feature"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")

    assert _discover_env_file(worktree) == str(shared_env)


def test_discover_env_file_returns_none_without_local_or_shared_env(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").write_text("gitdir: /tmp/missing-gitdir\n", encoding="utf-8")

    assert _discover_env_file(repo_root) is None


def test_model_pool_default() -> None:
    """Default pool: grok + kimi + qwen3.6-plus (post PR #225 gate-flip dipstick)."""
    assert settings.model_pool == [
        "x-ai/grok-4.1-fast",
        "moonshotai/kimi-k2.6",
        "qwen/qwen3.6-plus",
    ]
