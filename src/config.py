"""Application configuration via environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    """Email Knowledge Base configuration.

    All settings can be overridden via environment variables or .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # LLM — single-model default (legacy single-model path). For A/B
    # routing, set LLM_MODEL_POOL (comma-separated) and the per-batch
    # selector in compile_all.py picks uniformly per batch.
    #
    # We default to z-ai/glm-4.6 because glm-5.1 does NOT cache prompts
    # through OpenRouter (verified 2026-04-13, see
    # docs/reviews/prompt-caching-20260413.md); glm-4.6 caches ~20% of
    # our 3000-token system prompt, which compounds over a full compile
    # to a ~3-4x cost delta.
    llm_model: str = "z-ai/glm-4.6"

    # Per-batch model A/B pool — comma-separated. Empty → single-model
    # (uses `llm_model` above). Each batch picks one uniformly at random
    # and stamps the choice in `messages.compile_model` so we can join
    # model → outcome later.
    #
    # Pool history:
    # - z-ai/glm-5.1 (2026-04-13): LiteLLM proxy returns 400
    #   ("Invalid model name ... Call /v1/models") on every call —
    #   upstream routing issue, not key-access. Dropped.
    # - z-ai/glm-4.6 (2026-04-14): across 44 batch attempts across 5
    #   runs it failed 52% of the time, almost always hitting the
    #   recursion limit (model loops past 120 tool-calls without
    #   converging). minimax-m2.7 and glm-5 both run ~5% failure on
    #   the same workload. Dropped until we understand why glm-4.6
    #   doesn't converge on our 3000-token tool-heavy prompt.
    llm_model_pool: str = "minimax/minimax-m2.7,z-ai/glm-5"

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

    @property
    def attachments_dir(self) -> Path:
        return self.raw_dir / "attachments"


settings = Settings()
