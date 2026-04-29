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
    # quietly diverge. (#192 fix: kept pointing at minimax-m2.7 after
    # the pool rewrite, meaning scripts/tests silently used a model
    # that had been excluded for gate-loop behavior.)
    llm_model: str = "x-ai/grok-4.1-fast"

    # Per-batch model A/B pool — comma-separated. Each batch picks one
    # uniformly at random and stamps the choice in
    # `messages.compile_model` so we can join model → outcome later.
    #
    # A coordinator-side auto-exclusion guard in compile_all.py drops
    # any model with >50% fail_rate over ≥5 attempts OR ≥10 absolute
    # hard failures (timeouts NOT counted toward the abs cap; they
    # still drag fail_rate so a consistently-slow model is still
    # caught) in the last 24h. That lets us re-enable historically
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
    # - moonshotai/kimi-k2.6 (2026-04-24): 100% valid across 12 attempts
    #   (3 compiled / 9 correct already_captured skips / 0 failures).
    #   High skip discrimination; strong frontier-tier behavior.
    #   Same-email cross-check confirmed skips were correct for content
    #   already in the wiki. Added to pool.
    # - qwen/qwen3.5-122b-a10b (2026-04-23): 5-attempt one-off, 0
    #   compiled / 4 trivial-skips / 1 recursion-fail. Replaced by
    #   qwen3.6-plus (next entry).
    # - qwen/qwen3.6-plus (2026-04-24): REMOVED — not advertised by
    #   the LiteLLM proxy. `curl $LITELLM_BASE_URL/models` confirmed
    #   today that the id has never existed upstream; every batch
    #   pick was 404-ing. `_filter_pool_to_available_models` catches
    #   it at run-start but keep it out of config so we stop rolling
    #   that dice. Do NOT re-add without first verifying it appears
    #   in the proxy `/models` listing.
    # - z-ai/glm-5 + z-ai/glm-5.1 (re-added 2026-04-24): gate-loop
    #   root causes landed via Wave 1 — #168 idempotent
    #   check_my_work cache, #167 people-wikilink warning-not-
    #   blocker, #169 scoped find_touched_pages — so the PR #225
    #   regression signal should be gone. `_healthy_pool` will
    #   requarantine within 5 attempts if not. glm-5.1 was 400-ing
    #   via LiteLLM on 2026-04-15 but now shows up in the proxy
    #   catalog; worth retrying. Both <$1/M input.
    # - anthropic/claude-sonnet-4.5 (2026-04-24): rejected for the
    #   pool because $3/M input blows the user's $1/M per-token
    #   ceiling. Available on the proxy but cost-gated out.
    llm_model_pool: str = "x-ai/grok-4.1-fast,moonshotai/kimi-k2.6,z-ai/glm-5.1,z-ai/glm-5"

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
    # qmd_timeout_s caps the per-call subprocess wall-clock. Originally
    # 45s (covered worst cold-start rerank in the 2026-04-23 spike), but
    # trace audit on run 5928c151 (2026-04-29) showed 15.4% of calls
    # hitting the cap (vs 8.9% on prior run 3e88f996) with p95 latency
    # plastered at 45.1s. Bumped to 60s as a band-aid while embedding-
    # service degradation RCA runs separately.
    use_semantic_resolve: bool = False
    qmd_timeout_s: int = 60

    # Per-`agent.ainvoke` wall-clock timeout in seconds. Caps a single LLM
    # round so a wedged proxy connection (2026-04-22 grok-4.1-fast: 5h31m
    # mid-round hang) fails fast instead of exhausting `--batch-timeout`,
    # which tracks cumulative time across model retries and can't bound a
    # single hung round.
    #
    # 2026-04-24: raised 150 -> 900 after #195. kimi-k2.6 on 5-thread
    # batches routinely runs one ainvoke round for 4-6 minutes on its
    # own; 150 s clipped 18 consecutive legitimate batches in the
    # 2026-04-23 smoke. 900 s still catches true hangs (the
    # batch-52-style 5h31m case) without false-killing real work.
    #
    # 2026-04-28: raised 900 -> 960 after PR #249 bumped ChatOpenAI
    # `timeout` 120 -> 300. SDK retries are 300 s x 3 = 900 s per
    # ultimate failure; without margin, `asyncio.wait_for` races the
    # SDK and usually wins, raising InvokeWallClockTimeout instead of
    # the APITimeoutError that `_is_model_unavailable_error` matches
    # on. 60 s margin lets SDK retries surface "Request timed out."
    # cleanly so the batch routes to pool retry.
    invoke_timeout_s: int = 960

    # Heartbeat for the per-tool-call return timestamp. The
    # `invoke_timeout_s` cap above lumps two failure shapes into one
    # 16-min wait: (1) wedged LLM round (provider stops responding),
    # (2) slow but productive deliberation (kimi on 50k input takes
    # 4-6 min per round). Wall-clock alone can't tell them apart.
    #
    # `compile_stuck_after_s` adds a second signal: if no tool call
    # has returned in this many seconds AND the agent task is still
    # running, treat it as a wedge and raise `StuckLLMRoundError`.
    # The coordinator routes that to pool retry the same way it does
    # `SilentModelFailError`. 300s = 5 min is generous for slow
    # providers but cuts the worst-case wait from 16 min to 5 min.
    compile_stuck_after_s: int = 300

    @property
    def attachments_dir(self) -> Path:
        return self.raw_dir / "attachments"


settings = Settings()
