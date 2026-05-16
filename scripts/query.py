"""CLI query interface for testing the adaptive query agent.

Usage:
    uv run python scripts/query.py "What is Seller ISQ?"
    uv run python scripts/query.py "How does PhotoSearch differ from Lens?"
    uv run python scripts/query.py --interactive
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import click

from src.query.graph import compile_graph


def run_query(app, question: str, chat_history: list[dict] | None = None) -> dict:
    """Run a single query through the adaptive pipeline."""
    initial_state = {
        "original_question": question,
        "chat_history": chat_history or [],
        "answer": "",
        "citations": [],
        "wiki_pages_read": [],
        "tool_calls": [],
    }

    result = app.invoke(initial_state)
    return result


def print_result(result: dict) -> None:
    """Pretty-print a query result."""
    print("\n" + "=" * 60)
    print(f"Pages Read: {', '.join(result.get('wiki_pages_read', []))}")
    print("-" * 60)
    print(result.get("answer", "(no answer)"))
    print("-" * 60)
    citations = result.get("citations", [])
    if citations:
        print(f"Sources: {', '.join(f'[[{c}]]' for c in citations)}")
    print("=" * 60)

    tool_calls = result.get("tool_calls", [])
    if tool_calls:
        print("\nAgent Trace:")
        for tc in tool_calls:
            print(f"  [{tc.get('tool', '?')}] {tc.get('input', '')[:80]} → {tc.get('output', '')[:80]}")


@click.command()
@click.argument("question", required=False)
@click.option("--interactive", "-i", is_flag=True, help="Interactive mode with session memory")
def main(question: str | None, interactive: bool) -> None:
    """Query the IndiaMART wiki knowledge base."""
    print("Compiling query graph...")
    app = compile_graph()
    print("Ready.\n")

    if interactive:
        chat_history: list[dict] = []
        print("Interactive mode. Type 'quit' to exit.\n")
        while True:
            q = input("You: ").strip()
            if q.lower() in ("quit", "exit", "q"):
                break
            if not q:
                continue

            result = run_query(app, q, chat_history)
            print_result(result)

            chat_history.append({"role": "user", "content": q})
            chat_history.append({"role": "assistant", "content": result.get("answer", "")})
    elif question:
        result = run_query(app, question)
        print_result(result)
    else:
        print("Usage: python scripts/query.py 'your question'")
        print("       python scripts/query.py --interactive")


if __name__ == "__main__":
    main()
