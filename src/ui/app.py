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

st.markdown("""
<style>
    .stApp { background-color: #1a1b2e; }

    /* Sidebar */
    section[data-testid="stSidebar"] { background: #232438; border-right: 1px solid #2e2f45; }
    .brand-box { text-align: center; padding: 2rem 1rem 1.2rem 1rem; border-bottom: 1px solid #2e2f45; margin-bottom: 1rem; }
    .brand-box .duck { font-size: 3.5rem; line-height: 1; }
    .brand-box .name { font-size: 1.6rem; font-weight: 700; color: #c4b5fd; margin-top: 0.4rem; }
    .brand-box .tagline { font-size: 0.85rem; color: #a0a0b8; margin-top: 0.15rem; }
    section[data-testid="stSidebar"] button {
        background: #2e2f45 !important; border: 1px solid #3a3b55 !important;
        color: #d4d4e8 !important; border-radius: 8px !important; font-size: 0.9rem !important;
    }
    section[data-testid="stSidebar"] button:hover { border-color: #c4b5fd !important; }

    /* Kill red focus */
    *:focus, *:active { outline: none !important; box-shadow: none !important; }

    /* Hide chat avatars — removes the colored circle icons */
    .stChatMessage [data-testid="chatAvatarIcon-user"],
    .stChatMessage [data-testid="chatAvatarIcon-assistant"],
    [data-testid="stChatMessageAvatarContainer"] {
        display: none !important;
        width: 0 !important;
        min-width: 0 !important;
    }

    /* HIGH CONTRAST text in chat messages */
    .stChatMessage p, .stChatMessage li, .stChatMessage td, .stChatMessage th {
        font-size: 1.1rem !important;
        line-height: 1.7 !important;
        color: #f0f0f5 !important;
    }
    .stChatMessage h1 { font-size: 1.5rem !important; color: #ffffff !important; }
    .stChatMessage h2 { font-size: 1.35rem !important; color: #ffffff !important; }
    .stChatMessage h3 { font-size: 1.2rem !important; color: #f0f0f5 !important; }
    .stChatMessage strong { color: #ffffff !important; }
    .stChatMessage code { color: #c4b5fd !important; background: #2e2f45 !important; }

    /* Chat input — fixed at bottom by Streamlit when at root level */
    [data-testid="stChatInput"] textarea {
        font-size: 1.1rem !important;
        background: #232438 !important;
        border: 1px solid #444 !important;
        border-radius: 10px !important;
        color: #f0f0f5 !important;
    }
    [data-testid="stChatInput"] textarea:focus { border-color: #666 !important; box-shadow: none !important; }
    [data-testid="stChatInput"] textarea::placeholder { color: #7c7c9a !important; }

    /* Source chips — higher contrast */
    .src-chip {
        display: inline-block; background: #2e2f45; border: 1px solid #4a4b65;
        border-radius: 20px; padding: 3px 14px; margin: 2px 3px;
        font-size: 0.85rem; color: #c4b5fd; font-family: monospace;
    }

    /* Debug trace */
    .dbg-trace {
        background: #232438; border-left: 3px solid #c4b5fd;
        padding: 5px 10px; margin: 3px 0; font-size: 0.85rem;
        border-radius: 0 6px 6px 0; color: #b0b0c8;
    }
    .dbg-trace b { color: #c4b5fd; }
    .dbg-trace .out { color: #7c7c9a; font-size: 0.78rem; }

    /* Status widget */
    [data-testid="stStatusWidget"] {
        background: #232438 !important; border: 1px solid #2e2f45 !important;
        border-radius: 8px !important;
    }

    /* Hide streamlit chrome */
    #MainMenu, footer { visibility: hidden; }
    header[data-testid="stHeader"] { background: #1a1b2e; }
</style>
""", unsafe_allow_html=True)

SUGGESTED_QUERIES = [
    "What is AuditMate and how does it work?",
    "Compare PhotoSearch vs Lens 2.0",
    "List all React/Node.js migrations at IndiaMART",
    "Who developed VANI 2.0 and who should I contact?",
    "I just joined trust and safety, brief me",
    "Where is WhatsApp used across products?",
    "What is the Complaint Agent v2?",
    "What projects has Swati Jain worked on?",
]

TOOL_ICONS = {
    "ls": "dir", "cat": "read", "grep": "search", "keyword_search": "find",
    "find": "filter", "head": "scan", "load_skill": "skill", "qmd_search": "hybrid",
    "related_pages": "graph", "quality_check": "check",
}


def run_query_with_callback(question, chat_history, status_container):
    from src.query.agents.main_agent import run_main_agent

    def on_tool(tool_name, tool_args, result_preview):
        label = TOOL_ICONS.get(tool_name, tool_name)
        args_str = str(tool_args).replace("{", "").replace("}", "").replace("'", "")[:60]
        status_container.write(f"**{label}** {args_str}")

    return run_main_agent(question=question, chat_history=chat_history, tool_callback=on_tool)


def render_citations(citations):
    if citations:
        chips = " ".join(f'<span class="src-chip">{c}</span>' for c in citations)
        st.markdown(f"**Wiki Sources:** {chips}", unsafe_allow_html=True)


def render_email_sources(email_sources):
    resolved = [e for e in email_sources if e.get("subject")]
    if not resolved:
        return
    with st.expander(f"Source Emails ({len(resolved)})", expanded=False):
        for e in resolved[:15]:
            subj = e["subject"]
            sender = e.get("from", "").split("<")[0].strip()
            date = e.get("date", "")[:10]
            st.markdown(
                f'<div style="font-size:0.85rem;color:#b0b0c8;padding:2px 0;">'
                f'📧 <b style="color:#c4b5fd">{subj}</b>'
                f' <span style="color:#7c7c9a">— {sender}, {date}</span></div>',
                unsafe_allow_html=True,
            )


def render_debug():
    if not st.session_state.debug_data:
        st.caption("No queries yet.")
        return
    for i, d in enumerate(reversed(st.session_state.debug_data)):
        idx = len(st.session_state.debug_data) - i
        with st.expander(f"Query {idx}: {d['question'][:50]}", expanded=(i == 0)):
            st.caption(f"{len(d['wiki_pages_read'])} pages read  |  {len(d['tool_calls'])} tool calls  |  {d['elapsed']:.1f}s")
            for tc in d.get("tool_calls", []):
                t = tc.get("tool", "?")
                inp = tc.get("input", "")
                out = tc.get("output", "")
                label = TOOL_ICONS.get(t, t)
                st.markdown(
                    f'<div class="dbg-trace"><b>{label}</b> {inp[:80]}'
                    f'<br/><span class="out">{out[:90]}</span></div>',
                    unsafe_allow_html=True,
                )
            st.caption("Pages: " + ", ".join(d.get("wiki_pages_read", [])))


def main():
    for key, default in [
        ("messages", []), ("chat_history", []), ("query_count", 0),
        ("debug_data", []), ("pending_query", None), ("show_debug", False),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # ── Sidebar ──
    with st.sidebar:
        st.markdown(
            '<div class="brand-box">'
            '<div class="duck">🦆</div>'
            '<div class="name">Duckie</div>'
            '<div class="tagline">IndiaMART Knowledge Assistant</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.markdown("**Suggested queries**")
        for query in SUGGESTED_QUERIES:
            if st.button(query, key=f"sq_{hash(query)}", use_container_width=True, type="secondary"):
                st.session_state.pending_query = query
        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Clear", use_container_width=True, type="secondary"):
                for k in ["messages", "chat_history", "debug_data"]:
                    st.session_state[k] = []
                st.session_state.query_count = 0
                st.rerun()
        with col2:
            lbl = "Hide Debug" if st.session_state.show_debug else "Debug"
            if st.button(lbl, use_container_width=True, type="secondary"):
                st.session_state.show_debug = not st.session_state.show_debug
                st.rerun()

    # ── Main area ──
    if st.session_state.show_debug:
        chat_col, debug_col = st.columns([3, 2])
    else:
        chat_col = st.container()
        debug_col = None

    with chat_col:
        # Welcome screen
        if not st.session_state.messages:
            st.markdown("")
            st.markdown("")
            st.markdown(
                '<div style="text-align:center; padding:4rem 0 2rem 0;">'
                '<div style="font-size:4.5rem;">🦆</div>'
                '<div style="font-size:2rem; color:#c4b5fd; font-weight:700; margin-top:0.6rem;">'
                "Hey, I'm Duckie</div>"
                '<div style="color:#b0b0c8; margin-top:0.5rem; font-size:1.15rem;">'
                'Ask me anything about IndiaMART products, systems, people, and initiatives</div>'
                '<div style="color:#7c7c9a; font-size:0.9rem; margin-top:1rem;">'
                'Covers Jan to mid-Feb 2026</div>'
                '</div>',
                unsafe_allow_html=True,
            )

        # Chat messages
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("email_sources"):
                    render_email_sources(msg["email_sources"])

    # Debug column
    if debug_col is not None:
        with debug_col:
            st.markdown("#### Debug Trace")
            render_debug()

    # ── Chat input at ROOT LEVEL — this makes it stick to bottom ──
    pending = st.session_state.pending_query
    prompt = st.chat_input("Ask anything about IndiaMART...")
    if pending:
        prompt = pending
        st.session_state.pending_query = None

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})

        # We need to rerun to show the user message and then process
        # But first, store the query to process after rerun
        if "processing" not in st.session_state:
            st.session_state.processing = None

        st.session_state.processing = prompt
        st.rerun()

    # Process pending query after rerun
    if st.session_state.get("processing"):
        query = st.session_state.processing
        st.session_state.processing = None

        # Show all existing messages first (including the user's new message)
        # Then process the query
        with st.chat_message("assistant"):
            status = st.status("Thinking...", expanded=True)
            start = time.time()
            result = run_query_with_callback(query, st.session_state.chat_history, status)
            elapsed = time.time() - start
            status.update(label="Done", state="complete", expanded=False)

            answer = result.get("answer", "I couldn't find an answer.")
            citations = result.get("citations", [])
            tool_calls = result.get("tool_calls", [])
            wiki_pages_read = result.get("wiki_pages_read", [])
            email_sources = result.get("email_sources", [])

            st.markdown(answer)
            render_email_sources(email_sources)

        st.session_state.messages.append({"role": "assistant", "content": answer, "citations": citations, "email_sources": email_sources})
        st.session_state.chat_history.append({"role": "user", "content": query})
        st.session_state.chat_history.append({"role": "assistant", "content": answer})
        st.session_state.query_count += 1
        st.session_state.debug_data.append({
            "question": query, "elapsed": elapsed, "tool_calls": tool_calls,
            "citations": citations, "wiki_pages_read": wiki_pages_read, "answer": answer,
        })
        st.rerun()


if __name__ == "__main__":
    main()
