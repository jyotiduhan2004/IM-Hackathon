"""Regenerate the auto-extracted sections of docs/architecture.md.

Pulls the live LangGraph topology and tool list from `create_compiler()` and
splices them into `docs/architecture.md` between sentinel comments. Run after
adding/removing tools or middleware.

Usage:
    uv run python scripts/dump_agent_diagram.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Anchor paths to the repo root so the script works whether it's run from
# the repo root, `scripts/`, or anywhere else (Makefile runs it from root,
# but manual dev use hits this otherwise).
REPO_ROOT = Path(__file__).resolve().parent.parent
ARCH_DOC = REPO_ROOT / "docs/architecture.md"
MMD_FILE = REPO_ROOT / "docs/diagrams/compile-agent-langgraph.mmd"

# Deep Agents builtin tool names — anything else in the bound tool list is
# treated as one of ours. Update if the upstream library renames or adds.
DEEP_AGENTS_BUILTIN_NAMES = {
    "read_file", "write_file", "edit_file", "ls", "glob", "grep",
    "write_todos", "task", "execute",
}
FILE_OPS = {"read_file", "write_file", "edit_file", "ls", "glob", "grep"}


def first_line(text: str | None, max_chars: int = 110) -> str:
    if not text:
        return ""
    line = text.strip().split("\n")[0].strip()
    if len(line) > max_chars:
        line = line[: max_chars - 1].rstrip() + "…"
    return line


def extract_langgraph_mermaid(agent: Any) -> str:
    """Build a hand-readable Mermaid graph from the agent's compiled topology.

    LangGraph's `draw_mermaid()` returns markup that escapes `.` in node
    IDs as `\\2e`. GitHub Mermaid choke on the raw form; we simplify to
    a hand-readable graph since the topology is small (5 nodes).
    """
    g = agent.get_graph()

    # Node id → friendly label
    label_for = {
        "__start__": "__start__",
        "__end__": "__end__",
        "model": "model<br/>(LiteLLM)",
        "tools": "tools",
        "TodoListMiddleware.after_model": "todo_router",
        "PatchToolCallsMiddleware.before_agent": "patch_tool_calls",
    }
    # `end` is reserved in Mermaid (closes subgraphs). Use a safe alias.
    short_for = {
        "TodoListMiddleware.after_model": "todo",
        "PatchToolCallsMiddleware.before_agent": "patch",
        "__start__": "start",
        "__end__": "terminus",
    }

    def short(nid: str) -> str:
        return short_for.get(nid, nid)

    lines = ["flowchart LR"]
    for nid in g.nodes:
        label = label_for.get(nid, nid)
        if nid in ("__start__", "__end__"):
            lines.append(f"    {short(nid)}([{label}])")
        elif "Middleware" in nid:
            lines.append(f"    {short(nid)}[/{label}/]")
        else:
            lines.append(f"    {short(nid)}[{label}]")
    for e in g.edges:
        s = short(e.source)
        t = short(e.target)
        arrow = "-.->" if e.conditional else "-->"
        lines.append(f"    {s} {arrow} {t}")
    return "\n".join(lines)


def extract_tool_table(agent: Any) -> str:
    """Build a markdown table of all tools actually bound to the agent.

    Pulls the live `tools_by_name` mapping from the compiled ToolNode so the
    table reflects whatever `create_compiler()` registered — no drift between
    code and docs.
    """
    tools_by_name = agent.nodes["tools"].bound.tools_by_name

    def source_label(name: str) -> str:
        if name in FILE_OPS:
            return "Deep Agents · file ops"
        if name in DEEP_AGENTS_BUILTIN_NAMES:
            return "Deep Agents · workflow"
        return "custom"

    # Stable order: customs first (alphabetical), then file ops, then workflow.
    customs = sorted(n for n in tools_by_name if n not in DEEP_AGENTS_BUILTIN_NAMES)
    file_ops = sorted(n for n in tools_by_name if n in FILE_OPS)
    workflow = sorted(n for n in tools_by_name if n in DEEP_AGENTS_BUILTIN_NAMES - FILE_OPS)
    ordered = customs + file_ops + workflow

    rows = ["| Tool | Source | First line of docstring |", "|------|--------|------------------------|"]
    for name in ordered:
        tool = tools_by_name[name]
        desc = first_line(getattr(tool, "description", ""))
        rows.append(f"| `{name}` | {source_label(name)} | {desc} |")
    return "\n".join(rows)


def splice(doc: str, marker: str, body: str) -> str:
    """Replace the content between `<!-- BEGIN: marker -->` and `<!-- END: marker -->`."""
    pattern = re.compile(
        rf"(<!-- BEGIN: {re.escape(marker)} -->)(.*?)(<!-- END: {re.escape(marker)} -->)",
        re.DOTALL,
    )
    if not pattern.search(doc):
        raise SystemExit(f"missing marker pair for '{marker}' in {ARCH_DOC}")
    return pattern.sub(rf"\1\n{body}\n\3", doc)


def main() -> None:
    if not ARCH_DOC.exists():
        raise SystemExit(f"{ARCH_DOC} not found — create the skeleton first")

    from src.compile.compiler import create_compiler

    # Build the agent once — two calls to create_compiler() were not only
    # double the build cost but a drift risk: if construction isn't perfectly
    # deterministic (e.g. tool list depends on env state), the two snapshots
    # could describe different agents.
    agent = create_compiler("z-ai/glm-5")

    graph_mermaid = extract_langgraph_mermaid(agent)
    tool_table = extract_tool_table(agent)

    doc = ARCH_DOC.read_text(encoding="utf-8")
    doc = splice(doc, "agent-graph", f"```mermaid\n{graph_mermaid}\n```")
    doc = splice(doc, "agent-tools", tool_table)
    ARCH_DOC.write_text(doc, encoding="utf-8")
    print(f"updated {ARCH_DOC}")

    # Keep the standalone `.mmd` file in sync — its header claims to be
    # auto-extracted, so let's make that true. We write the same Mermaid
    # source we spliced into the doc.
    if MMD_FILE.exists():
        mmd_header = (
            "%% Auto-extracted from dump_agent_diagram.py :: extract_langgraph_mermaid()\n"
            "%% Source: src/compile/compiler.py :: create_compiler()\n"
            "%% Regenerate: make dump-agent-diagram\n"
        )
        MMD_FILE.write_text(mmd_header + graph_mermaid + "\n", encoding="utf-8")
        print(f"updated {MMD_FILE}")


if __name__ == "__main__":
    main()
