"""Wiki compiler — Deep Agents workflow that compiles raw emails into wiki pages.

Extracted from the legacy `src/compile/compiler.py` (Phase 1C). Holds the
agent factory (`create_compiler`) and the orchestrator entry point
(`run_compilation`) that the coordinator calls per batch.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

import structlog

from src.agent.prompts import COMPILER_SYSTEM_PROMPT
from src.agent.reviewer_result import _check_silent_fail
from src.agent.run_state import _compute_batch_cutoff_date
from src.agent.run_state import _current_batch_cutoff_date
from src.agent.run_state import _current_batch_sibling_slugs_written
from src.agent.run_state import _current_batch_thread_id
from src.agent.run_state import _current_batch_topic_slugs_written
from src.agent.run_state import _current_raw_paths
from src.agent.run_state import _extract_raw_paths_from_instruction
from src.agent.run_state import _preflight_raw_paths_exist
from src.agent.runtime import _ainvoke_with_timeout
from src.agent.runtime import _make_chat_model
from src.agent.runtime import get_langfuse_handler
from src.agent.tools.entities import create_entities
from src.agent.tools.insights import log_insight
from src.agent.tools.legacy import check_my_work
from src.agent.tools.pages import patch_page
from src.agent.tools.pages import validate_page_draft
from src.agent.tools.raw_access import get_thread_context
from src.agent.tools.raw_access import resolve_page
from src.agent.tools.sources import get_page_summary
from src.agent.tools.sources import list_wiki_pages
from src.agent.view import _build_compile_view
from src.agent.view import _cleanup_compile_view
from src.agent.view import _count_view_raw_md_files
from src.agent.view import _preflight_view_resolves_paths
from src.config import settings
from src.wiki.draft import write_draft_page

if TYPE_CHECKING:
    from src.observability.tool_call_log import ToolCallLogHandler

logger = structlog.get_logger(__name__)


def create_compiler(
    model_name: str | None = None,
    raw_dir: str = "raw",
    wiki_dir: str = "wiki",
    view_root: Path | None = None,
    extra_middlewares: list[Any] | None = None,
) -> Any:
    """Create a Deep Agents wiki compiler.

    Model routing:
    - If LITELLM_BASE_URL is set, routes all models through the LiteLLM proxy
      using an OpenAI-compatible client. This lets us use any model name the
      proxy knows (e.g. "z-ai/glm-5", "anthropic/claude-opus-4-6").
    - Otherwise uses init_chat_model's provider inference (requires provider
      prefix like "openai:gpt-4o" or a recognized model name).

    Args:
        model_name: Model string. Defaults to settings.llm_model.
        raw_dir: Path to raw/ directory. Used for symlink target inside
            the view-root and for ergonomic error messages.
        wiki_dir: Path to wiki/ directory. Same treatment as raw_dir.
        view_root: Pre-built view-root to use (for tests/reuse). When None,
            a fresh per-run view is built with symlinks to raw_dir/wiki_dir.
            The caller MUST clean up the view when done (run_compilation
            does this automatically).

    Returns:
        A compiled LangGraph agent ready to invoke.
    """
    from deepagents import FilesystemPermission
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend

    from src.agent.middleware.chronological_scope import ChronologicalScopeMiddleware
    from src.agent.middleware.edit_payload_sanity import EditPayloadSanityMiddleware
    from src.agent.middleware.edit_staleness import EditStalenessMiddleware
    from src.agent.middleware.entity_write_autoheal import EntityWriteAutohealMiddleware
    from src.agent.middleware.glob_narrowing import GlobNarrowingMiddleware
    from src.agent.middleware.legacy_page_hint import LegacyPageHintMiddleware
    from src.agent.middleware.path_autoheal import PathAutohealMiddleware
    from src.agent.middleware.read_file_truncation_hint import ReadFileTruncationHintMiddleware
    from src.agent.middleware.reconnaissance_paralysis import ReconnaissanceParalysisMiddleware
    from src.agent.middleware.same_thread_topic_guard import SameThreadTopicGuardMiddleware
    from src.agent.middleware.sibling_draft_check import SiblingDraftCheckMiddleware
    from src.agent.reviewer import build_reviewer_subagent

    model_name = model_name or settings.llm_model
    logger.info(
        "creating wiki compiler",
        model=model_name,
        via_proxy=bool(settings.litellm_base_url),
    )

    model = _make_chat_model(model_name)

    # Per-run view: chroot the agent's filesystem to {view}/raw and
    # {view}/wiki only. Host paths like /Users/... are not visible.
    # The view is built here when none is passed in (typical path). Caller
    # (run_compilation) cleans up on exit.
    if view_root is None:
        view_root = _build_compile_view(Path(raw_dir), Path(wiki_dir))
        logger.info("compile view built", view_root=str(view_root))

    backend = FilesystemBackend(root_dir=str(view_root), virtual_mode=True)

    # Permission rules, evaluated in declaration order:
    # 1. deny write anywhere under /raw — emails are immutable source of truth.
    # 2. deny read of /raw/attachments — binary data; agent can't do anything
    #    useful with it and reading eats context.
    # 3. reads/writes elsewhere fall through to the permissive default.
    permissions = [
        FilesystemPermission(operations=["write"], paths=["/raw/**"], mode="deny"),
        FilesystemPermission(operations=["read"], paths=["/raw/attachments/**"], mode="deny"),
    ]

    system_prompt = (
        COMPILER_SYSTEM_PROMPT
        + "\n\n## Runtime context\n\n"
        + "- Your filesystem is chrooted. Only `/raw/` and `/wiki/` exist.\n"
        + f"- Model: `{model_name}`.\n"
    )

    reviewer_spec = build_reviewer_subagent()

    # Compile agent surface:
    # - Custom tools: list_wiki_pages, resolve_page, create_entities,
    #   write_draft_page, log_insight, check_my_work, get_page_summary,
    #   get_thread_context, patch_page, validate_page_draft.
    # - Inherited filesystem tools (ls, read_file, write_file, edit_file,
    #   glob, grep) from FilesystemMiddleware auto-added by create_deep_agent.
    # - Middleware: path_autoheal (rewrites host-path leaks) +
    #   entity_write_autoheal (nudges raw entity writes toward create_entities)
    #   + legacy_page_hint (touch-it-fix-it annotation on reads of
    #   legacy-ontology wiki pages — once-per-page-per-run)
    #   + check_my_work_gate (short-circuits check_my_work calls made before
    #   any content-page write succeeds — live traces show 59-78% of
    #   batches hit the validator before writing anything)
    #   + glob_narrowing (rejects `**/<slug>.md` slug-lookup globs;
    #   24.5% of glob calls were timing out at the 20s deepagents cap —
    #   per-obs scores issue #185).
    # - Subagent: reviewer (read-only, structured verdict; retains glob
    #   for its grep-heavy review workflow).
    # Bookkeeping tools (mark_as_compiled, stamp_page_compiled_at,
    # append_to_log, update_wiki_index) remain importable but NOT bound —
    # the coordinator handles them deterministically post-run.
    # NOTE: `list_uncompiled_emails` + `find_new_sources` are deliberately
    # NOT exposed to the agent. The coordinator owns the compile queue and
    # already passes the batch file list in the user instruction. Agent
    # queue-discovery is pure context tax. Historical trace data showed
    # `find_new_sources(thread_id=...)` being used as a stand-in for
    # `get_thread_context(thread_id)` — same information, clearer intent;
    # the latter is the right tool. Both functions remain importable for
    # coordinator + script use.
    from src.agent.middleware import CheckMyWorkGateMiddleware
    from src.agent.middleware import TerminalDecisionGuardMiddleware

    middleware_list: list[Any] = [
        PathAutohealMiddleware(),
        ChronologicalScopeMiddleware(),
        EditPayloadSanityMiddleware(),
        EntityWriteAutohealMiddleware(),
        LegacyPageHintMiddleware(),
        SameThreadTopicGuardMiddleware(),
        # Sibling-aware draft check (v11-U9): catches batch-local
        # near-duplicates BEFORE they hit disk, complementing
        # SameThreadTopicGuardMiddleware (which is a hard topic-only
        # block) and the v9-U14 reviewer merge-candidate queue
        # (which is a post-hoc catch). Conservative thresholds to
        # avoid false positives that erode agent trust.
        SiblingDraftCheckMiddleware(),
        CheckMyWorkGateMiddleware(),
        # V12 audit fix-C (2026-04-23): block batch exit without a
        # terminal commitment. Batch 45 (kimi-k2.6) completed with
        # `turns=6 tools=8 writes=0` and no log_insight — email
        # stayed pending, got re-queued, agent paid same cost twice.
        # This guard injects a nudge before END and loops the agent
        # back to the model; after `_MAX_NUDGES` the coordinator's
        # `mark_skipped("agent_exited_without_terminal_decision")`
        # fallback kicks in.
        TerminalDecisionGuardMiddleware(),
        # Glob narrowed 2026-04-18 (v10-U5): 24.5% of glob calls were
        # `**/<slug>.md` slug lookups timing out at 20s. Reject those
        # with a pointer to resolve_page; legitimate enumeration
        # patterns (wiki/topics/*.md) pass through. Reviewer subagent
        # keeps glob — see src/agent/reviewer.py.
        GlobNarrowingMiddleware(),
        # v11-U3: every `read_file` ToolMessage gets a footer with
        # `total_lines` (and a `next offset=` hint when truncated).
        # Inherited deepagents tool defaults to limit=100 and gives
        # zero signal that more content exists below — agent flies
        # blind on 83% of compile traces. View-root binds at
        # construction so we can map virtual paths to disk.
        ReadFileTruncationHintMiddleware(view_root=view_root),
        # Smoke-99a267f4 audit (2026-04-28): glm-5.1 burned 12 of 30
        # turns recovering from edit_file `String not found` errors
        # — agent's `old_string` was built from a stale mental model
        # after 5 sequential edits without a re-read. Reactive +
        # proactive nudges to break the spiral. See
        # docs/audits/smoke-99a267f4-recursion-deep-dive-2026-04-28.md.
        EditStalenessMiddleware(),
        # Smoke-02c9d536 audit (2026-04-28): kimi-k2.6 burned 575s
        # of 900s budget on thread 19be9883c6d921a6 with 14 reads,
        # 6 resolves, 0 edits / writes / log_insights. Sibling to
        # EditStaleness — that one watches the edit storm; this one
        # catches the failure to ever start the edit phase. Fires
        # once per batch at the 8th read with zero commits.
        ReconnaissanceParalysisMiddleware(),
    ]
    if extra_middlewares:
        # Caller-supplied middlewares are PREPENDED so they wrap the
        # full standard stack (outermost). The heartbeat middleware
        # specifically MUST be outermost — inner middlewares (e.g.
        # `GlobNarrowingMiddleware`) can short-circuit without calling
        # `handler`, which would skip an inner heartbeat's `mark()` and
        # let `last_tool_return_at` go stale on every short-circuited
        # tool call. Codex review on PR #253.
        middleware_list = list(extra_middlewares) + middleware_list

    return create_deep_agent(
        model=model,
        tools=[
            list_wiki_pages,
            resolve_page,
            create_entities,
            write_draft_page,
            log_insight,
            check_my_work,
            get_page_summary,
            get_thread_context,
            patch_page,
            validate_page_draft,
        ],
        system_prompt=system_prompt,
        backend=backend,
        permissions=permissions,
        middleware=middleware_list,
        subagents=[cast(Any, reviewer_spec)],
    )


def run_compilation(
    instruction: str = "Compile all uncompiled raw emails into wiki pages.",
    model_name: str | None = None,
    raw_dir: str = "raw",
    wiki_dir: str = "wiki",
    recursion_limit: int = 250,
    cache_stats: Any | None = None,
    tool_log: ToolCallLogHandler | None = None,
    run_name: str | None = None,
    trace_metadata: dict[str, Any] | None = None,
    trace_tags: list[str] | None = None,
    raw_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Run a compilation pass. Returns the agent's final state.

    recursion_limit of 250 accommodates ~5 emails per batch with 2-page
    multi-system writes + reviewer subagents. LangGraph counts every node
    visit (model + ToolNode + each `after_model` middleware) as a super-
    step, not a parent turn. Today there are 3 active middlewares
    (TodoListMiddleware, CheckMyWorkGateMiddleware, terminal_decision_guard)
    so each parent turn costs ~5 super-steps; reviewer subagents share the
    parent's budget. Smoke run `99a267f4` (2026-04-28) trace audit found
    legitimate 5-email batches with two reviewer rounds + one stale-date
    re-loop costing ~180-220 super-steps even when the work itself was
    clean. Lifting to 250 (was 150) gives headroom without rewarding
    pathological loops — the existing `_check_my_work_cache` per-write-
    epoch dedupe still catches genuine spirals.

    Pass a `CacheStatsCallback` as `cache_stats` to capture per-batch prompt-
    caching metrics (hit rate, cached tokens, total tokens). See
    `src/observability/cache_stats.py`.

    Pass a `ToolCallLogHandler` as `tool_log` to capture per-tool-call
    telemetry (name, inputs, latency, status). See
    `src/observability/tool_call_log.py`.

    `raw_paths` is optional. When provided, the coordinator's list of raw
    paths for this batch is injected into the `_current_raw_paths` ContextVar
    so `create_entities` can use them without the LLM threading them
    through. When None, we grep them out of the instruction string.
    """
    raw_dir_abs = Path(raw_dir).resolve()
    wiki_dir_abs = Path(wiki_dir).resolve()
    # Build the view-root here so cleanup is paired with this run's
    # lifecycle, not the cached agent graph.
    view_root = _build_compile_view(Path(raw_dir), Path(wiki_dir))
    try:
        effective_raw_paths = raw_paths or _extract_raw_paths_from_instruction(instruction)

        # Per-batch preflight: fail fast if the filesystem mount doesn't
        # contain the files the DB says we should read. F3 fix — live
        # Tier-A traces on 2026-04-16 silently failed because
        # ``find_new_sources`` returned DB paths but read_file saw an
        # empty /raw mount.
        if effective_raw_paths:
            _preflight_raw_paths_exist(effective_raw_paths)
            _preflight_view_resolves_paths(view_root, effective_raw_paths)

        mounted_raw_count = _count_view_raw_md_files(view_root)
        logger.info(
            "run_compilation_preflight",
            raw_dir=str(raw_dir_abs),
            wiki_dir=str(wiki_dir_abs),
            view_root=str(view_root),
            mounted_raw_file_count=mounted_raw_count,
            batch_raw_paths=len(effective_raw_paths),
        )

        # Per-batch heartbeat state. The middleware stamps
        # `last_tool_return_at` on every tool-call return; the watcher
        # task in `_ainvoke_with_timeout` reads it to detect a wedged
        # LLM round (no tool returns for `compile_stuck_after_s`s while
        # the agent task is still running).
        from src.agent.middleware.stuck_heartbeat import StuckHeartbeatMiddleware
        from src.agent.middleware.stuck_heartbeat import StuckHeartbeatState

        heartbeat_state = StuckHeartbeatState()

        agent = create_compiler(
            model_name=model_name,
            raw_dir=raw_dir,
            wiki_dir=wiki_dir,
            view_root=view_root,
            extra_middlewares=[StuckHeartbeatMiddleware(heartbeat_state)],
        )

        callbacks = []
        lf = get_langfuse_handler(update_trace=True)
        if lf:
            callbacks.append(lf)
        if cache_stats is not None:
            callbacks.append(cache_stats)
        if tool_log is not None:
            callbacks.append(tool_log)

        # Enrich trace metadata with mount-sanity info so Langfuse can
        # surface the infra-vs-synthesis distinction. Deterministic —
        # safe to add to every trace.
        enriched_metadata: dict[str, Any] = dict(trace_metadata) if trace_metadata else {}
        enriched_metadata.setdefault("cwd", str(Path.cwd()))
        enriched_metadata.setdefault("raw_dir", str(raw_dir_abs))
        enriched_metadata.setdefault("wiki_dir", str(wiki_dir_abs))
        enriched_metadata.setdefault("view_root", str(view_root))
        enriched_metadata.setdefault("mounted_raw_file_count", mounted_raw_count)
        enriched_metadata.setdefault("missing_raw_paths_count", 0)

        config: dict[str, Any] = {}
        if callbacks:
            config["callbacks"] = callbacks
        config["recursion_limit"] = recursion_limit
        if run_name:
            config["run_name"] = run_name
        config["metadata"] = enriched_metadata
        if trace_tags:
            config["tags"] = trace_tags

        logger.info(
            "running compilation",
            instruction=instruction[:100],
            recursion_limit=recursion_limit,
            raw_paths_count=len(effective_raw_paths),
        )

        cutoff_date = _compute_batch_cutoff_date(effective_raw_paths)
        if cutoff_date:
            logger.info(
                "batch_cutoff_date",
                cutoff_date=cutoff_date,
                raw_paths_count=len(effective_raw_paths),
            )

        from src.db.messages import shared_thread_id_for_paths

        batch_thread_id = shared_thread_id_for_paths(effective_raw_paths)
        if batch_thread_id:
            logger.info(
                "batch_thread_id",
                thread_id=batch_thread_id,
                raw_paths_count=len(effective_raw_paths),
            )

        raw_paths_token = _current_raw_paths.set(effective_raw_paths)
        cutoff_token = _current_batch_cutoff_date.set(cutoff_date)
        thread_id_token = _current_batch_thread_id.set(batch_thread_id)
        topic_slugs_token = _current_batch_topic_slugs_written.set(set())
        sibling_slugs_token = _current_batch_sibling_slugs_written.set(set())
        try:
            # Wrap the single agent round in `asyncio.wait_for` — the outer
            # `--batch-timeout` tracks cumulative wall-clock across model
            # retries and can't bound a single hung round (2026-04-22
            # grok-4.1-fast: 5h31m mid-round hang). The paired
            # `heartbeat_state` lets `_ainvoke_with_timeout` fire faster
            # (`compile_stuck_after_s`) when the agent goes idle on the
            # model side specifically, distinguishing "wedged round" from
            # "slow but productive deliberation".
            result = asyncio.run(
                _ainvoke_with_timeout(
                    agent,
                    instruction,
                    config,
                    settings.invoke_timeout_s,
                    heartbeat_state=heartbeat_state,
                    stuck_after_s=settings.compile_stuck_after_s,
                )
            )
            _check_silent_fail(result, model=model_name)
            return result
        finally:
            _current_batch_sibling_slugs_written.reset(sibling_slugs_token)
            _current_batch_topic_slugs_written.reset(topic_slugs_token)
            _current_batch_thread_id.reset(thread_id_token)
            _current_batch_cutoff_date.reset(cutoff_token)
            _current_raw_paths.reset(raw_paths_token)
    finally:
        _cleanup_compile_view(view_root)
