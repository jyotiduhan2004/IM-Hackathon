"""Application configuration via environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _discover_env_file(repo_root: Path) -> str | None:
    """Return the .env file for this checkout.

    Normal checkouts use ``<repo>/.env`` directly. Linked worktrees often omit
    that file so secrets stay in the main checkout only; in that case fall back
    to the main checkout's ``.env`` via git's ``commondir`` metadata.
    """

    local_env = repo_root / ".env"
    if local_env.exists():
        return str(local_env)

    git_path = repo_root / ".git"
    if not git_path.is_file():
        return None

    try:
        gitdir_line = git_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    prefix = "gitdir:"
    if not gitdir_line.startswith(prefix):
        return None

    git_dir = Path(gitdir_line.removeprefix(prefix).strip())
    if not git_dir.is_absolute():
        git_dir = (repo_root / git_dir).resolve()

    commondir_path = git_dir / "commondir"
    if commondir_path.exists():
        try:
            relative_common_dir = commondir_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        common_dir = (git_dir / relative_common_dir).resolve()
    else:
        try:
            common_dir = git_dir.parents[1]
        except IndexError:
            return None

    shared_env = common_dir.parent / ".env"
    if shared_env.exists():
        return str(shared_env)

    return None


class Settings(BaseSettings):
    """Email Knowledge Base configuration.

    All settings can be overridden via environment variables or .env file.
    """

    model_config = SettingsConfigDict(
        env_file=_discover_env_file(_REPO_ROOT),
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Tolerate unknown env keys so a newer `.env` (from a newer branch
        # that added fields like `USE_SEMANTIC_RESOLVE` or `QMD_TIMEOUT_S`)
        # doesn't break Settings() on this branch. Those keys just aren't
        # read here — the features they gate live on other branches.
        extra="ignore",
    )

    # LLM — `llm_model_pool` is the source of truth; every batch picks
    # one entry uniformly at random (after the auto-exclusion guard in
    # `scripts/compile_all.py::_healthy_pool` drops known-broken ones).
    # `llm_model` is a fallback for code paths that invoke
    # `run_compilation` without a model override (one-off scripts,
    # tests); it mirrors the first pool entry so behavior doesn't
    # quietly diverge.
    llm_model: str = "minimax/minimax-m2.7"

    # Per-batch model A/B pool — comma-separated. Each batch picks one
    # uniformly at random and stamps the choice in
    # `messages.compile_model` so we can join model → outcome later.
    #
    # A coordinator-side auto-exclusion guard in compile_all.py drops
    # any model with >50% failure over ≥5 attempts OR ≥10 absolute
    # failures in the last 24h. That lets us re-enable historically
    # flaky models here without a manual post-mortem every cycle — if
    # the proxy still rejects them or they still loop recursively, the
    # guard drops them at the next run-start.
    #
    # Pool history (the guard handles short-term flap; these comments
    # capture the "don't re-add yet" judgment calls the guard can't):
    # - z-ai/glm-5.1 (2026-04-13): LiteLLM proxy returned 400 on every
    #   call. Re-added 2026-04-15 — proxy still returned "Invalid model
    #   name" on every attempt, so dropped again. Do NOT re-add until
    #   someone confirms the upstream model ID on the LiteLLM side.
    # - z-ai/glm-4.6 (2026-04-14): 52% recursion-limit fail rate across
    #   44 batches (minimax-m2.7 and glm-5 ran ~5% on the same prompt).
    #   Kept OUT of the pool until someone investigates why it loops
    #   past 120 tool-calls without converging — the 24h guard window
    #   doesn't retain week-old failures, so it won't preemptively drop
    #   glm-4.6 if naively re-added.
    # - deepseek/deepseek-v3.2, xiaomi/mimo-v2-pro (2026-04-16): removed
    #   because team-key access isn't provisioned on the LiteLLM proxy
    #   (every call 401s). Re-add only after proxy-team provisioning is
    #   confirmed. x-ai/grok-4.1-fast stays in the pool — it was added
    #   alongside these two on 2026-04-15 for wider A/B coverage and its
    #   team-key access is working.
    llm_model_pool: str = "minimax/minimax-m2.7,z-ai/glm-5,x-ai/grok-4.1-fast"

    litellm_base_url: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    @property
    def model_pool(self) -> list[str]:
        """Parsed `llm_model_pool` as a list. Empty → [llm_model]."""
        if not self.llm_model_pool.strip():
            return [self.llm_model]
        return [m.strip() for m in self.llm_model_pool.split(",") if m.strip()]

    # Gmail
    gmail_credentials_path: str = "credentials.json"
    gmail_token_path: str = "token.json"
    mailing_list_address: str = ""
    gmail_delegated_user: str | None = None

    # Langfuse
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_enabled: bool = True

    # Database — Postgres catalog (queue + future provenance)
    database_url: str = "postgresql://email_kb_app:email_kb@localhost:5432/email_kb"

    # Paths
    raw_dir: Path = Path("raw")
    wiki_dir: Path = Path("wiki")

    # qmd semantic retriever (Phase 1). When True, resolve_page routes
    # ambiguous queries through the qmd CLI before SQL fallback.
    # qmd_timeout_s caps the per-call subprocess wall-clock (45s covers
    # worst cold-start rerank observed in the spike).
    use_semantic_resolve: bool = False
    qmd_timeout_s: int = 45

    @property
    def attachments_dir(self) -> Path:
        return self.raw_dir / "attachments"


settings = Settings()
