"""GHCP hosted agents expose only the pilot's MCP/custom tools and skills, not Copilot CLI built-ins.

Live 2026-11-11 (#192): a returns-triage pilot called `bash` (359 times, up to
260 s), `task`, `web_fetch`, `rg`, `view` and `glob`, none of them in the SPEC.
A default session lists 17 tools (bash, create, edit, web_fetch, task, sql, ...).
`available_tools` is the session-wide allowlist ("only these tools exist for
anyone"): https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/custom-agents
Entries are source-qualified (`builtin:`, `mcp:`, `custom:`; SDK ToolSet).
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

pytest.importorskip("starlette")

import test_ghcp_session_isolation as base  # noqa: E402
from test_ghcp_session_isolation import runtime  # noqa: E402,F401  (fixture)

REFERENCES = Path(__file__).resolve().parents[1] / "references"
DEFAULT = ["mcp:*", "custom:*", "builtin:skill"]


def _kwargs_seen(module, session=None):
    seen = []
    original_create, original_resume = base.FakeClient.create_session, base.FakeClient.resume_session

    async def create(self, *, session_id=None, **kwargs):
        seen.append(("create", kwargs))
        return await original_create(self, session_id=session_id, **kwargs)

    async def resume(self, session_id, **kwargs):
        seen.append(("resume", kwargs))
        return await original_resume(self, session_id, **kwargs)

    base.FakeClient.create_session, base.FakeClient.resume_session = create, resume
    try:
        base._invoke(module, "Triage RMA-1", session=session)
    finally:
        base.FakeClient.create_session, base.FakeClient.resume_session = original_create, original_resume
    return seen


def test_sessions_allow_only_mcp_custom_tools_and_skills_by_default(runtime):
    for kind, kwargs in _kwargs_seen(runtime) + _kwargs_seen(runtime, session="s1"):
        assert kwargs["available_tools"] == DEFAULT, kind
    assert not any(t.startswith("builtin:") and t != "builtin:skill" for t in runtime._available_tools())


def test_resumed_sessions_keep_the_allowlist(runtime):
    _kwargs_seen(runtime, session="s2")
    runtime._sessions.clear()  # force a resume from disk
    seen = _kwargs_seen(runtime, session="s2")
    assert [k for k, _ in seen] == ["resume"]
    assert seen[0][1]["available_tools"] == DEFAULT


def test_spec_opt_in_adds_named_builtins_only(runtime, monkeypatch):
    monkeypatch.setenv("THREADLIGHT_BUILTIN_TOOLS", "web_fetch, view")
    assert runtime._available_tools() == DEFAULT + ["builtin:web_fetch", "builtin:view"]
    for bad in ("*", "bash;rm", "builtin:*", "mcp:*", "../x"):
        monkeypatch.setenv("THREADLIGHT_BUILTIN_TOOLS", bad)
        assert runtime._available_tools() == DEFAULT, bad


def _create_session_keywords(path: Path) -> list[set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [{k.arg for k in node.keywords} for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "create_session"]


def test_governed_ghcp_host_has_a_fixed_allowlist_without_builtins():
    path = REFERENCES / "governance" / "ghcp-container.py"
    calls = _create_session_keywords(path)
    assert calls and all("available_tools" in kw for kw in calls)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
              and any(getattr(t, "id", "") == "GOVERNED_AVAILABLE_TOOLS" for t in node.targets)]
    assert values == [("mcp:*", "builtin:skill")]
