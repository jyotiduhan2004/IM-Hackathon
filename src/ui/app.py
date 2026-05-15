"""Streamlit chat UI for the Duckie query agent.

Run with: streamlit run src/ui/app.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

st.set_page_config(
    page_title="Duckie — IndiaMART Knowledge Assistant",
    page_icon="🦆",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
    .stApp { background-color: #0e1117; }
    .main-header {
        text-align: center;
        padding: 1rem 0 0.5rem 0;
    }
    .main-header h1 {
        color: #FFD700;
        font-size: 2.2rem;
        margin-bottom: 0.2rem;
    }
    .main-header p { color: #9ca3af; font-size: 0.95rem; }
    .source-chip {
        display: inline-block;
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 2px 10px;
        margin: 2px 3px;
        font-size: 0.82rem;
        color: #93c5fd;
    }
    .tool-trace {
        background: #1a1a2e;
        border-left: 3px solid #FFD700;
        padding: 6px 12px;
        margin: 4px 0;
        font-size: 0.85rem;
        border-radius: 0 6px 6px 0;
    }
    .suggested-btn button {
        width: 100%;
        text-align: left;
        font-size: 0.85rem;
    }
</style>
"""

SUGGESTED_QUERIES = [
    ("📖 Factual", "What is WebERP and how does it work?"),
    ("⚖️ Compare", "Compare WhatsApp 8181 vs WhatsApp 9696"),
    ("📊 List", "List all AI initiatives at IndiaMART"),
    ("🕐 Recent", "What changed recently in seller experience?"),
    ("👤 People", "Who is Amit Agarwal and what has he worked on?"),
    ("🎓 Onboard", "I'm new to the AI team, give me a comprehensive brief"),
    ("🔍 Cross-domain", "How is AI used across different domains at IndiaMART?"),
    ("💡 Glossary", "What does ISQ stand for?"),
]

TOOL_ICONS = {
    "ls": "📂", "cat": "📖", "grep": "🔎", "keyword_search": "🔤",
    "find": "🗂️", "head": "📋", "load_skill": "🧠", "qmd_search": "🔍",
    "related_pages": "🔗", "quality_check": "✅",
}


def run_query_with_callback(question: str, chat_history: list[dict], status_container) -> dict:
    """Run query with live tool trace updates."""
    from src.query.agents.main_agent import run_main_agent

    tool_count = [0]

    def on_tool(tool_name, tool_args, result_preview):
        tool_count[0] += 1
        icon = TOOL_ICONS.get(tool_name, "⚙️")
        args_str = str(tool_args).replace("{", "").replace("}", "").replace("'", "")[:80]
        status_container.write(f"{icon} **{tool_name}**({args_str})")

    result = run_main_agent(
        question=question,
        chat_history=chat_history,
        tool_callback=on_tool,
    )
    return result


def render_citations(citations: list[str], email_refs: list[str] | None = None):
    """Render source citations as styled chips."""
    if citations:
        chips = " ".join(f'<span class="source-chip">[[{c}]]</span>' for c in citations)
        st.markdown(f"**Sources:** {chips}", unsafe_allow_html=True)
    if email_refs:
        with st.expander(f"📧 {len(email_refs)} raw email references"):
            for ref in email_refs[:10]:
                st.code(ref, language=None)


def main():
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "query_count" not in st.session_state:
        st.session_state.query_count = 0
    if "debug_data" not in st.session_state:
        st.session_state.debug_data = []
    if "pending_query" not in st.session_state:
        st.session_state.pending_query = None

    # Sidebar
    with st.sidebar:
        st.markdown("## 🦆 Duckie")
        st.markdown("IndiaMART's AI Knowledge Assistant")
        st.divider()

        st.markdown("### Try a query")
        for label, query in SUGGESTED_QUERIES:
            if st.button(f"{label}: {query[:40]}...", key=f"sq_{label}", use_container_width=True):
                st.session_state.pending_query = query

        st.divider()

        st.markdown("### Stats")
        st.metric("Queries this session", st.session_state.query_count)
        st.metric("Wiki coverage", "Jan-mid Feb 2026")
        st.metric("Pages indexed", "2,300+")

        st.divider()
        if st.button("🗑️ Clear conversation", use_container_width=True):
            st.session_state.messages = []
            st.session_state.chat_history = []
            st.session_state.debug_data = []
            st.session_state.query_count = 0
            st.rerun()

    # Main area
    tab_chat, tab_debug = st.tabs(["💬 Chat", "🔧 Debug"])

    with tab_chat:
        st.markdown(
            '<div class="main-header">'
            '<h1>🦆 Duckie</h1>'
            '<p>Ask anything about IndiaMART — products, systems, people, initiatives</p>'
            '</div>',
            unsafe_allow_html=True,
        )

        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("citations"):
                    render_citations(msg["citations"], msg.get("email_refs"))

        pending = st.session_state.pending_query
        prompt = st.chat_input("Ask about IndiaMART...")

        if pending:
            prompt = pending
            st.session_state.pending_query = None

        if prompt:
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                status = st.status("🦆 Duckie is thinking...", expanded=True)
                start = time.time()

                result = run_query_with_callback(prompt, st.session_state.chat_history, status)

                elapsed = time.time() - start
                status.update(
                    label=f"Done in {elapsed:.1f}s — read {len(result.get('wiki_pages_read', []))} pages",
                    state="complete",
                    expanded=False,
                )

                answer = result.get("answer", "I couldn't find an answer.")
                citations = result.get("citations", [])
                email_refs = result.get("email_refs", [])
                tool_calls = result.get("tool_calls", [])
                wiki_pages_read = result.get("wiki_pages_read", [])

                st.markdown(answer)
                render_citations(citations, email_refs)

                # Show quality badges if quality_check was called
                for tc in tool_calls:
                    if tc.get("tool") == "quality_check":
                        qc_output = tc.get("output", "")
                        if qc_output:
                            st.markdown(f"<small style='color:#9ca3af'>{qc_output}</small>", unsafe_allow_html=True)
                        break

            st.session_state.messages.append({
                "role": "assistant",
                "content": answer,
                "citations": citations,
                "email_refs": email_refs,
            })
            st.session_state.chat_history.append({"role": "user", "content": prompt})
            st.session_state.chat_history.append({"role": "assistant", "content": answer})
            st.session_state.query_count += 1

            st.session_state.debug_data.append({
                "question": prompt,
                "elapsed": elapsed,
                "tool_calls": tool_calls,
                "citations": citations,
                "wiki_pages_read": wiki_pages_read,
                "email_refs": email_refs,
                "answer": answer,
            })

    with tab_debug:
        st.markdown("### 🔧 Agent Reasoning Trace")

        if not st.session_state.debug_data:
            st.info("No queries yet. Ask a question in the Chat tab first.")
        else:
            for i, debug in enumerate(reversed(st.session_state.debug_data)):
                idx = len(st.session_state.debug_data) - i
                with st.expander(f"Query #{idx}: {debug['question'][:80]}", expanded=(i == 0)):
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Time", f"{debug['elapsed']:.1f}s")
                    col2.metric("Pages Read", len(debug["wiki_pages_read"]))
                    col3.metric("Tool Calls", len(debug["tool_calls"]))

                    st.divider()
                    st.markdown("**Agent Trace:**")
                    for tc in debug.get("tool_calls", []):
                        tool = tc.get("tool", "?")
                        inp = tc.get("input", "")
                        out = tc.get("output", "")
                        icon = TOOL_ICONS.get(tool, "⚙️")
                        is_skill = tc.get("from_skill", False)
                        indent = "margin-left:20px;border-left-color:#6366f1;" if is_skill else ""
                        prefix = "↳ " if is_skill else ""
                        st.markdown(
                            f'<div class="tool-trace" style="{indent}">{prefix}{icon} <b>{tool}</b>({inp[:120]})<br/>'
                            f'<span style="color:#6b7280">{out[:150]}</span></div>',
                            unsafe_allow_html=True,
                        )

                    st.divider()
                    st.markdown("**Pages Read:**")
                    for slug in debug.get("wiki_pages_read", []):
                        st.markdown(f"- `[[{slug}]]`")

                    if debug.get("email_refs"):
                        st.markdown(f"**Email References:** {len(debug['email_refs'])}")

                    st.divider()
                    st.markdown("**Full Answer:**")
                    st.markdown(debug["answer"])


if __name__ == "__main__":
    main()
