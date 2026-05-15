"""Simplified LangGraph pipeline — single tool-calling agent, no router/graders."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.query.state import QueryState
from src.query.agents.main_agent import run_main_agent


def agent_node(state: dict) -> dict:
    """Run the main tool-calling agent."""
    question = state["original_question"]
    chat_history = state.get("chat_history", [])

    result = run_main_agent(question, chat_history)

    return {
        "answer": result.get("answer", ""),
        "citations": result.get("citations", []),
        "wiki_pages_read": result.get("wiki_pages_read", []),
        "tool_calls": result.get("tool_calls", []),
    }


def build_graph() -> StateGraph:
    """Build the simplified query graph."""
    graph = StateGraph(QueryState)
    graph.add_node("agent", agent_node)
    graph.set_entry_point("agent")
    graph.add_edge("agent", END)
    return graph


def compile_graph():
    """Build and compile the query graph."""
    return build_graph().compile()
