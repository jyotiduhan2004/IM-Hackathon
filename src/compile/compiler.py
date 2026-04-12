"""Wiki compiler — Deep Agents workflow that compiles raw emails into wiki pages."""

from __future__ import annotations

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
def list_uncompiled_emails(raw_dir: str = "raw") -> list[str]:
    """List all raw email files that haven't been compiled yet.

    Reads YAML frontmatter of each .md file in raw_dir and returns
    paths where compiled: false, sorted chronologically by filename
    (which starts with YYYY-MM-DD).

    Returns:
        List of relative paths like ["raw/2026-04-01_subject_abc12345.md", ...]
    """
    raw_path = Path(raw_dir)
    if not raw_path.exists():
        return []

    uncompiled: list[str] = []
    for md_file in sorted(raw_path.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            frontmatter = _extract_frontmatter(content)
            if frontmatter.get("compiled") is False:
                uncompiled.append(str(md_file))
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            logger.warning("skipping malformed raw file", path=str(md_file), error=str(e))

    return uncompiled


@tool
def mark_as_compiled(file_path: str) -> str:
    """Mark a raw email as compiled by setting compiled: true in frontmatter.

    Args:
        file_path: Path to the raw email markdown file

    Returns:
        Confirmation message
    """
    path = Path(file_path)
    if not path.exists():
        return f"ERROR: file not found: {file_path}"

    content = path.read_text(encoding="utf-8")
    frontmatter = _extract_frontmatter(content)
    body = _extract_body(content)

    frontmatter["compiled"] = True
    frontmatter["compiled_at"] = datetime.now().isoformat() + "Z"

    new_content = _render_with_frontmatter(frontmatter, body)
    path.write_text(new_content, encoding="utf-8")

    return f"marked compiled: {file_path}"


@tool
def update_wiki_index(wiki_dir: str = "wiki") -> str:
    """Regenerate wiki/index.md by scanning all wiki pages and their frontmatter.

    Args:
        wiki_dir: Root wiki directory

    Returns:
        Summary of what was indexed
    """
    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return f"ERROR: wiki directory not found: {wiki_dir}"

    categories = {
        "policies": [],
        "topics": [],
        "entities": [],
        "timelines": [],
        "conflicts": [],
    }

    for category in categories:
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                fm = _extract_frontmatter(content)
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
        f"Last updated: {datetime.now().isoformat()}Z",
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

    return f"updated index: {total} pages across {sum(1 for v in categories.values() if v)} categories"


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

    timestamp = datetime.now().isoformat() + "Z"

    if not log_path.exists():
        header = (
            "# Compilation Log\n\n"
            "| Timestamp | Event |\n"
            "|---|---|\n"
        )
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


def create_compiler(
    model_name: str | None = None,
    raw_dir: str = "raw",
    wiki_dir: str = "wiki",
) -> Any:
    """Create a Deep Agents wiki compiler.

    Args:
        model_name: LiteLLM model string (e.g., "gpt-4o"). Defaults to settings.llm_model.
        raw_dir: Path to raw/ directory
        wiki_dir: Path to wiki/ directory

    Returns:
        A compiled LangGraph agent ready to invoke.
    """
    from deepagents import create_deep_agent
    from langchain.chat_models import init_chat_model

    model_name = model_name or settings.llm_model
    logger.info("creating wiki compiler", model=model_name)

    model = init_chat_model(model_name)

    system_prompt = (
        COMPILER_SYSTEM_PROMPT
        + f"\n\n## Context\n\n- raw_dir: {raw_dir}\n- wiki_dir: {wiki_dir}\n"
    )

    return create_deep_agent(
        model=model,
        tools=[
            list_uncompiled_emails,
            mark_as_compiled,
            update_wiki_index,
            append_to_log,
        ],
        system_prompt=system_prompt,
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
) -> dict[str, Any]:
    """Run a compilation pass. Returns the agent's final state."""
    agent = create_compiler(model_name=model_name, raw_dir=raw_dir, wiki_dir=wiki_dir)

    callbacks = []
    lf = get_langfuse_handler()
    if lf:
        callbacks.append(lf)

    config: dict[str, Any] = {}
    if callbacks:
        config["callbacks"] = callbacks
    config["recursion_limit"] = 100

    logger.info("running compilation", instruction=instruction[:100])
    result = agent.invoke(
        {"messages": [{"role": "user", "content": instruction}]},
        config=config,
    )
    return result
