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

    # LLM
    llm_model: str = "gpt-4o"
    litellm_base_url: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

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
