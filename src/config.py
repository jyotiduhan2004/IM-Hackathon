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
    # z-ai/glm-5.1 was in this pool on 2026-04-13 but the LiteLLM proxy
    # returns 400 ("Invalid model name ... Call /v1/models") on every
    # call — upstream routing issue, not a key-access problem. Removed
    # to stop burning 25% of batches on guaranteed failures. Re-add
    # once the proxy routes it.
    llm_model_pool: str = "minimax/minimax-m2.7,z-ai/glm-5,z-ai/glm-4.6"

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
