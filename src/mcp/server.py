"""Duckie MCP Server — exposes IndiaMART wiki to Claude Code.

Run: uv run python src/mcp/server.py

When a developer working on IndiaMART code in Claude Code encounters
unknown terms or needs internal context, Claude Code calls ask_duckie()
which runs the full Duckie agent (search, read, reason) and returns
the answer with sources.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("duckie")


@mcp.tool()
def ask_duckie(question: str) -> str:
    """Ask IndiaMART's internal wiki anything. Duckie searches 2,300+ wiki pages
    compiled from internal launch emails (Jan-mid Feb 2026) covering products,
    systems, people, teams, and initiatives across 8 business domains.

    Use this when you encounter unknown IndiaMART terms, acronyms, product names,
    team names, or need internal context about any IndiaMART system or initiative.

    Examples:
      ask_duckie('What is ISQ at IndiaMART?')
      ask_duckie('Who owns PhotoSearch?')
      ask_duckie('What changes were made to GLAdmin in January 2026?')
      ask_duckie('What is MCAT and how does it work?')
    """
    from src.query.agents.main_agent import run_main_agent

    result = run_main_agent(question, max_iterations=15)

    answer = result.get("answer", "No answer found.")
    citations = result.get("citations", [])
    tool_calls = result.get("tool_calls", [])

    parts = [answer]

    email_sources = result.get("email_sources", [])
    resolved = [e for e in email_sources if e.get("subject")]
    if resolved:
        parts.append("\n\nSource Emails:")
        for e in resolved[:10]:
            subj = e["subject"]
            sender = e.get("from", "")
            date = e.get("date", "")[:10]
            parts.append(f"  - {subj} ({sender}, {date})")

    parts.append(f"\n[Duckie searched {len(tool_calls)} steps, read {len(citations)} wiki pages, traced {len(resolved)} source emails]")

    return "\n".join(parts)


if __name__ == "__main__":
    mcp.run()
