"""Wiki compiler — Deep Agents workflow that compiles raw emails into wiki pages."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml
from langchain_core.tools import tool

from src.compile.prompts import COMPILER_SYSTEM_PROMPT
from src.config import settings

logger = structlog.get_logger(__name__)


# === Custom Tools for the Compiler Agent ===


@tool
def list_uncompiled_emails(raw_dir: str = "raw") -> list[dict[str, str]]:
    """List raw email files that haven't been compiled yet.

    Returns one entry per uncompiled email with lightweight metadata the agent
    can use to plan without reading each file. Sorted oldest-first by date.

    Args:
        raw_dir: Directory containing raw/*.md files (default "raw")

    Returns:
        List of dicts with keys: path, date, subject, from, thread_id.
        Empty list if no uncompiled emails.
    """
    raw_path = Path(raw_dir)
    if not raw_path.exists():
        return []

    uncompiled: list[dict[str, str]] = []
    for md_file in sorted(raw_path.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            frontmatter = _extract_frontmatter(content)
            if frontmatter.get("compiled") is False:
                uncompiled.append(
                    {
                        "path": str(md_file),
                        "date": str(frontmatter.get("date", "")),
                        "subject": str(frontmatter.get("subject", "")),
                        "from": str(frontmatter.get("from", "")),
                        "thread_id": str(frontmatter.get("thread_id", "")),
                    }
                )
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            logger.warning("skipping malformed raw file", path=str(md_file), error=str(e))

    return uncompiled


@tool
def list_wiki_pages(wiki_dir: str = "wiki") -> dict[str, list[str]]:
    """List all existing wiki pages grouped by category.

    Call this BEFORE creating new pages so you know what already exists and
    can update the existing page instead of duplicating.

    Returns:
        Dict with keys: topics, entities, policies, timelines, conflicts.
        Each value is a list of page names (without .md extension).
        These names are what you should use in [[wikilinks]].
    """
    wiki_path = Path(wiki_dir)
    result: dict[str, list[str]] = {
        "topics": [],
        "entities": [],
        "systems": [],
        "policies": [],
        "timelines": [],
        "conflicts": [],
    }
    if not wiki_path.exists():
        return result

    for category in result:
        cat_dir = wiki_path / category
        if cat_dir.exists():
            result[category] = sorted(f.stem for f in cat_dir.glob("*.md"))
    return result


@tool
def stamp_page_compiled_at(file_path: str) -> dict[str, str]:
    """Set last_compiled on a wiki page to the current real-world UTC time.

    Use this INSTEAD OF writing last_compiled yourself in the page frontmatter.
    You do not know the current date; this tool uses the system clock.

    Args:
        file_path: Path to the wiki page markdown file

    Returns:
        Dict with "ok" (bool), "last_compiled" (ISO string), "path" (str).
    """
    path = Path(file_path)
    if not path.exists():
        return {"ok": "false", "error": f"file not found: {file_path}"}

    content = path.read_text(encoding="utf-8")
    frontmatter = _extract_frontmatter(content)
    body = _extract_body(content)

    now_iso = datetime.now(UTC).isoformat()
    frontmatter["last_compiled"] = now_iso

    new_content = _render_with_frontmatter(frontmatter, body)
    path.write_text(new_content, encoding="utf-8")
    return {"ok": "true", "last_compiled": now_iso, "path": file_path}


@tool
def mark_as_compiled(file_path: str) -> dict[str, str | int]:
    """Mark a raw email as compiled. Call ONLY after the email's content has
    been merged into the correct wiki pages.

    Sets compiled: true and compiled_at (real UTC time) in the raw file's
    frontmatter. Does not modify email body.

    Args:
        file_path: Path to the raw email markdown file (e.g., "raw/2026-04-11_foo_abc12345.md")

    Returns:
        Dict with "ok" (bool), "remaining_uncompiled" (int count), "path" (str).
    """
    path = Path(file_path)
    if not path.exists():
        return {"ok": "false", "error": f"file not found: {file_path}"}

    content = path.read_text(encoding="utf-8")
    frontmatter = _extract_frontmatter(content)
    body = _extract_body(content)

    frontmatter["compiled"] = True
    frontmatter["compiled_at"] = datetime.now(UTC).isoformat()

    new_content = _render_with_frontmatter(frontmatter, body)
    path.write_text(new_content, encoding="utf-8")

    remaining = sum(
        1
        for md in path.parent.glob("*.md")
        if _extract_frontmatter(md.read_text(encoding="utf-8")).get("compiled") is False
    )

    return {
        "ok": "true",
        "remaining_uncompiled": remaining,
        "path": file_path,
    }


@tool
def update_wiki_index(wiki_dir: str = "wiki") -> str:
    """Regenerate wiki/index.md by scanning all wiki pages and their frontmatter.

    Also auto-stamps `last_compiled` on any page missing the field, using the
    current real UTC time. This guarantees every page has a timestamp without
    relying on the agent to call `stamp_page_compiled_at` for each one.

    Args:
        wiki_dir: Root wiki directory

    Returns:
        Summary: pages indexed + pages auto-stamped
    """
    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return f"ERROR: wiki directory not found: {wiki_dir}"

    categories: dict[str, list[str]] = {
        "policies": [],
        "topics": [],
        "entities": [],
        "systems": [],
        "timelines": [],
        "conflicts": [],
    }
    stamped = 0
    now_iso = datetime.now(UTC).isoformat()

    for category in categories:
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                fm = _extract_frontmatter(content)
                # Auto-stamp only if the frontmatter looks complete enough.
                # A broken frontmatter (e.g., only `last_compiled` present) means
                # the agent's edit_file mangled the page — don't overwrite it,
                # or we'll destroy what's left.
                has_real_fields = "title" in fm or "page_type" in fm
                if "last_compiled" not in fm and has_real_fields:
                    fm["last_compiled"] = now_iso
                    body = _extract_body(content)
                    md_file.write_text(_render_with_frontmatter(fm, body), encoding="utf-8")
                    stamped += 1
                title = fm.get("title", md_file.stem)
                status = fm.get("status", "current")
                name = md_file.stem
                entry = f"- [[{name}]] — {title}"
                if status != "current":
                    entry += f" *({status})*"
                categories[category].append(entry)
            except (yaml.YAMLError, UnicodeDecodeError):
                continue

    lines = [
        "# Knowledge Base Index",
        "",
        f"Last updated: {now_iso}",
        "",
    ]
    total = 0
    for cat_name, entries in categories.items():
        if entries:
            lines.append(f"## {cat_name.title()} ({len(entries)})")
            lines.extend(entries)
            lines.append("")
            total += len(entries)

    lines.insert(3, f"Total pages: {total}")
    lines.insert(4, "")

    index_path = wiki_path / "index.md"
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return (
        f"updated index: {total} pages across "
        f"{sum(1 for v in categories.values() if v)} categories; "
        f"auto-stamped {stamped} pages with last_compiled"
    )


@tool
def append_to_log(entry: str, wiki_dir: str = "wiki") -> str:
    """Append a timestamped entry to wiki/log.md.

    Args:
        entry: Human-readable description of what was compiled
        wiki_dir: Root wiki directory

    Returns:
        Confirmation
    """
    wiki_path = Path(wiki_dir)
    wiki_path.mkdir(parents=True, exist_ok=True)
    log_path = wiki_path / "log.md"

    timestamp = datetime.now(UTC).isoformat()

    if not log_path.exists():
        header = "# Compilation Log\n\n| Timestamp | Event |\n|---|---|\n"
        log_path.write_text(header, encoding="utf-8")

    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"| {timestamp} | {entry} |\n")

    return f"logged: {entry}"


# === Frontmatter helpers ===


def _extract_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML frontmatter from a markdown file."""
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        parsed = yaml.safe_load(parts[1])
        return parsed if isinstance(parsed, dict) else {}
    except yaml.YAMLError:
        return {}


def _extract_body(content: str) -> str:
    """Extract the body (everything after frontmatter)."""
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content
    return parts[2].lstrip("\n")


def _render_with_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    """Render a markdown file with YAML frontmatter and body."""
    yaml_block = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, width=120
    ).rstrip()
    return f"---\n{yaml_block}\n---\n\n{body}"


# === Compiler Factory ===


def _make_chat_model(model_name: str) -> Any:
    """Build a chat model, routing through LiteLLM proxy if configured.

    LiteLLM proxies expose an OpenAI-compatible API, so we use langchain-openai's
    ChatOpenAI and point it at the proxy's base URL. This works for any model
    string the proxy knows (e.g. "z-ai/glm-5", "anthropic/claude-opus-4-6"),
    regardless of whether langchain has a native provider for it.
    """
    if settings.litellm_base_url:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            base_url=settings.litellm_base_url,
            api_key=settings.openai_api_key or "dummy",
        )

    # Fallback: use langchain's provider inference
    from langchain.chat_models import init_chat_model

    return init_chat_model(model_name)


def create_compiler(
    model_name: str | None = None,
    raw_dir: str = "raw",
    wiki_dir: str = "wiki",
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
        raw_dir: Path to raw/ directory
        wiki_dir: Path to wiki/ directory

    Returns:
        A compiled LangGraph agent ready to invoke.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend

    model_name = model_name or settings.llm_model
    logger.info(
        "creating wiki compiler",
        model=model_name,
        via_proxy=bool(settings.litellm_base_url),
    )

    model = _make_chat_model(model_name)

    # Deep Agents defaults to a virtual (in-memory) filesystem. We need real disk
    # so read_file/write_file/edit_file operate on raw/ and wiki/ directly.
    # virtual_mode=True with root_dir="." means:
    # - Absolute paths and ".." traversal are blocked (security guardrail)
    # - Agent must use relative paths like "raw/foo.md", "wiki/topics/bar.md"
    # Per FilesystemBackend docs, this is the right mode for bounded workflows.
    cwd = Path.cwd().resolve()
    backend = FilesystemBackend(root_dir=str(cwd), virtual_mode=True)

    system_prompt = (
        COMPILER_SYSTEM_PROMPT
        + f"\n\n## Context\n\n- raw_dir: {raw_dir}\n- wiki_dir: {wiki_dir}\n"
        + "- ALL file paths MUST be relative (no leading /, no ..). Examples:\n"
        + f"  - GOOD: `{raw_dir}/2026-04-11_subject_abc.md`\n"
        + f"  - GOOD: `{wiki_dir}/topics/my-topic.md`\n"
        + "  - BAD: `/Users/...` (absolute paths are blocked)\n"
        + "  - BAD: `/raw/foo.md` (leading slash means absolute; blocked)\n"
        + "- Do NOT call `ls` on absolute paths or `/`. Use `ls raw` or "
        + f"`ls {wiki_dir}/topics` or use `glob` with patterns.\n"
    )

    return create_deep_agent(
        model=model,
        tools=[
            list_uncompiled_emails,
            list_wiki_pages,
            mark_as_compiled,
            stamp_page_compiled_at,
            update_wiki_index,
            append_to_log,
        ],
        system_prompt=system_prompt,
        backend=backend,
    )


def get_langfuse_handler() -> Any | None:
    """Return a Langfuse callback handler if configured, else None."""
    if not settings.langfuse_enabled:
        return None
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    try:
        from langfuse.callback import CallbackHandler

        return CallbackHandler(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except ImportError:
        logger.warning("langfuse not installed, tracing disabled")
        return None


def run_compilation(
    instruction: str = "Compile all uncompiled raw emails into wiki pages.",
    model_name: str | None = None,
    raw_dir: str = "raw",
    wiki_dir: str = "wiki",
    recursion_limit: int = 150,
) -> dict[str, Any]:
    """Run a compilation pass. Returns the agent's final state.

    recursion_limit of 150 accommodates ~3-10 emails per batch. Each email
    typically takes 10-20 agent steps (read, classify, read existing pages,
    write/edit pages, stamp timestamps, mark compiled). Bump higher if batches
    hit the limit.
    """
    agent = create_compiler(model_name=model_name, raw_dir=raw_dir, wiki_dir=wiki_dir)

    callbacks = []
    lf = get_langfuse_handler()
    if lf:
        callbacks.append(lf)

    config: dict[str, Any] = {}
    if callbacks:
        config["callbacks"] = callbacks
    config["recursion_limit"] = recursion_limit

    logger.info(
        "running compilation",
        instruction=instruction[:100],
        recursion_limit=recursion_limit,
    )
    return agent.invoke(
        {"messages": [{"role": "user", "content": instruction}]},
        config=config,
    )
