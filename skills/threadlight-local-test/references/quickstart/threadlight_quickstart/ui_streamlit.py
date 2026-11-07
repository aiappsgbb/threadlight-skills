"""Minimal Streamlit UI for Pattern 0.

Runs via ``streamlit run ui_streamlit.py``; the CLI sets two env vars
to wire the page to a specific PoC root:

  THREADLIGHT_QUICKSTART_ROOT       absolute path to the PoC root
  THREADLIGHT_QUICKSTART_SIMULATOR  "1" to enable the prompt simulator

After every turn the UI appends a row to ``<root>/tests/quickstart.jsonl``
in the shape ``microsoft-foundry`` consumes (query + response). Disable by
setting ``THREADLIGHT_QUICKSTART_NO_TRANSCRIPT=1``.

Deliberately stays single-file — no extra dirs, no theme, no auth.
The PoC's own ``src/workspace/`` React UI keeps working unchanged
for prod-parity demos; this page is the "I just want to see it work"
fallback.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
from pathlib import Path

import streamlit as st

from threadlight_quickstart.agent_wiring import build_agent
from threadlight_quickstart.approvals import continue_turn, pending_from_error
from threadlight_quickstart.discover import PoCLayoutError, discover
from threadlight_quickstart.simulator import PromptCursor, load_simulator_prompts


st.set_page_config(
    page_title="Threadlight — Pattern 0 Quickstart",
    page_icon="🧵",
    layout="wide",
)


@st.cache_resource(show_spinner="Wiring agent…")
def _build(root: str):
    layout = discover(Path(root))
    agent, stores = build_agent(layout)
    prompts = load_simulator_prompts(layout)
    return layout, agent, stores, prompts


async def _stream_response(agent, prompt: str, placeholder, pending_out: list | None = None) -> str:
    """Stream `agent.run(prompt, stream=True)` into the placeholder.

    Returns the full concatenated text so callers can persist a
    transcript row. A native approval pause is reported as an error and,
    when ``pending_out`` is given, appended there so the page can ask a
    human to approve or reject it.
    """
    chunks: list[str] = []
    updates: list = []
    try:
        async for update in agent.run(prompt, stream=True):
            updates.append(update)
            text = getattr(update, "text", None) or ""
            if text:
                chunks.append(text)
                placeholder.markdown("".join(chunks))
        return "".join(chunks)
    except Exception as exc:  # noqa: BLE001
        msg = f"_error: {type(exc).__name__}: {exc}_"
        placeholder.markdown(msg)
        if pending_out is not None:
            pending = pending_from_error(prompt, updates, exc)
            if pending is not None:
                pending_out.append(pending)
        return msg


def _approval_panel(agent, layout) -> None:
    """Show a paused write (or script) call and resume the turn with the human's decision."""
    pending = st.session_state.get("pending")
    if pending is None:
        return
    with st.chat_message("assistant"):
        st.warning("Human approval needed before this call runs:")
        for row in pending.describe():
            st.code(f"{row['tool']}({row['arguments']})", language="text")
        approve, reject = st.columns(2)
        decision = None
        if approve.button("✅ Approve and continue", use_container_width=True):
            decision = True
        if reject.button("⛔ Reject", use_container_width=True):
            decision = False
    if decision is None:
        return
    st.session_state.pending = None
    turn = asyncio.run(continue_turn(agent, pending, approved=decision))
    verdict = "approved" if decision else "rejected"
    tools = ", ".join(d["tool"] for d in turn.decisions[:len(pending.requests)])
    text = turn.text or f"_error: {turn.error}_"
    response = f"_{verdict} by you: {tools}_\n\n{text}"
    st.session_state.messages.append({"role": "assistant", "content": response})
    if turn.pending is not None:
        st.session_state.pending = turn.pending
    _append_transcript(layout.root, f"[approval {verdict}] {tools}", response)
    st.rerun()


def _append_transcript(root: Path, query: str, response: str) -> None:
    """Append one row to ``<root>/tests/quickstart.jsonl`` (microsoft-foundry shape).

    Shape matches the `tool_definitions` / `tool_calls` schema documented
    in the official microsoft-foundry ``foundry-agent/eval-datasets`` guidance; for the quickstart
    we only have query + response (tools are stub CRUD), which is the
    minimum the eval pipeline accepts.
    """
    if os.environ.get("THREADLIGHT_QUICKSTART_NO_TRANSCRIPT") == "1":
        return
    try:
        tests_dir = root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        path = tests_dir / "quickstart.jsonl"
        row = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "query": query,
            "response": response,
        }
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        # Never block the demo on a write failure.
        pass


def _sidebar(layout, stores, prompts, simulator_on: bool):
    st.sidebar.title("PoC")
    st.sidebar.code(str(layout.root), language="bash")

    st.sidebar.subheader("Entities (in-memory stores)")
    if stores:
        for name, store in stores.items():
            st.sidebar.markdown(f"- **{name}** — {len(store.records)} record(s)")
    else:
        st.sidebar.markdown("_(none — Pattern 0 needs at least one)_")

    st.sidebar.subheader("Skills (SkillsProvider)")
    if layout.skill_names:
        for name in layout.skill_names:
            st.sidebar.markdown(f"- {name}")
    else:
        st.sidebar.markdown("_(none — agent runs without progressive disclosure)_")

    backend = os.environ.get("LLM_BACKEND", "foundry")
    st.sidebar.subheader("LLM backend")
    st.sidebar.markdown(f"`LLM_BACKEND={backend}`")

    st.sidebar.subheader("Simulator")
    if not simulator_on:
        st.sidebar.markdown("_(off — start with `--simulator`)_")
    elif not prompts:
        st.sidebar.markdown("_(on, but no prompts found)_")
    else:
        st.sidebar.markdown(f"loaded **{len(prompts)}** prompt(s)")

    st.sidebar.subheader("Transcript")
    if os.environ.get("THREADLIGHT_QUICKSTART_NO_TRANSCRIPT") == "1":
        st.sidebar.markdown("_(disabled)_")
    else:
        st.sidebar.markdown(f"appending to `tests/quickstart.jsonl`")


def main():
    root = os.environ.get("THREADLIGHT_QUICKSTART_ROOT")
    simulator_on = os.environ.get("THREADLIGHT_QUICKSTART_SIMULATOR", "0") == "1"

    if not root:
        st.error(
            "THREADLIGHT_QUICKSTART_ROOT is unset. Launch via "
            "`python -m threadlight_quickstart` so the CLI configures the page."
        )
        return

    try:
        layout, agent, stores, prompts = _build(root)
    except PoCLayoutError as exc:
        st.error(str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        st.error(f"Agent wiring failed: {type(exc).__name__}: {exc}")
        return

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "cursor" not in st.session_state:
        st.session_state.cursor = PromptCursor(prompts if simulator_on else [])

    cursor: PromptCursor = st.session_state.cursor

    _sidebar(layout, stores, prompts, simulator_on)

    st.title("🧵 Threadlight — Pattern 0")
    st.caption("MAF Agent + SkillsProvider + the SPEC's own tools over sample data — no Docker, no MCP server. "
               "Write tools wait for your approval.")

    # Render history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    _approval_panel(agent, layout)

    # Simulator next-prompt button
    queued: str | None = None
    if simulator_on and cursor.remaining > 0:
        nxt = cursor.peek()
        if st.button(f"▶️ Next demo prompt ({cursor.remaining} left) — {nxt!s:.80}", use_container_width=True):
            queued = cursor.advance()
    elif simulator_on:
        st.info("Simulator finished. Click reset to replay.")
        if st.button("🔁 Reset simulator"):
            cursor.reset()
            st.rerun()

    typed = st.chat_input("Ask the agent something…")
    prompt = queued or typed

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            placeholder = st.empty()
            pending: list = []
            response = asyncio.run(_stream_response(agent, prompt, placeholder, pending))
        _append_transcript(layout.root, prompt, response)
        if pending:
            response = ("⏸ **Waiting for your approval.** The agent wants to run a tool that needs approval; "
                        "nothing has been recorded yet. Approve or reject it below.")
        st.session_state.messages.append({"role": "assistant", "content": response})
        if pending:
            st.session_state.pending = pending[0]
            st.rerun()


main()
