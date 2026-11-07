"""Pattern 0 is faithful to the design: AGENTS.md, SPEC section 6 tools, knowledge, approvals.

Real MAF dispatch with a deterministic chat client: only the model output is
synthetic, so no credentials or inference are needed.
"""
from __future__ import annotations

import asyncio
import json
import textwrap

import pytest
from agent_framework import Content

from threadlight_quickstart import agent_wiring
from threadlight_quickstart.discover import discover

from .test_skill_approval import Model, call, outputs

SPEC = textwrap.dedent("""\
    # SpecKit: Returns Triage (synthetic)

    ## 6. Tool Contracts

    ### `returns_get_case`
    - **Description**: fetch one return case by RMA id.
    - **Inputs**:
      | Parameter | Type | Required | Description |
      |-----------|------|----------|-------------|
      | rma_id | string | yes | `RMA-NNN` |
    - **Backed by**: Returns case store (mock)

    ### `customer_get_profile`
    - **Description**: fetch the customer profile (email masked).
    - **Inputs**:
      | Parameter | Type | Required | Description |
      |-----------|------|----------|-------------|
      | customer_id | string | yes | `CUST-NN` |
    - **Backed by**: Customer-profile service (mock)

    ### `returns_record_recommendation`
    - **Description**: store the triage recommendation on the case.
    - **Inputs**:
      | Parameter | Type | Required | Description |
      |-----------|------|----------|-------------|
      | rma_id | string | yes | Case id |
      | decision | string | yes | outcome |
      | citations | array | yes | clause ids |
    - **Backed by**: Returns case store (mock)

    ### `return_policy_search`
    - **Description**: retrieve policy clauses with ids for citation.
    - **Inputs**:
      | Parameter | Type | Required | Description |
      |-----------|------|----------|-------------|
      | query | string | yes | question |
    - **Backed by**: Return-policy knowledge base (Foundry IQ)

    ## 11a. Runtime Governance Contract

    ```yaml
    tools:
      - id: returns_record_recommendation
        consequence: write
    ```
    """)

DESIGN_SERVER = textwrap.dedent('''\
    from __future__ import annotations
    import json, os
    from pathlib import Path

    DECISIONS = {"approve_refund", "deny_refund", "escalate_to_supervisor"}

    def _load(name):
        data = Path(os.environ["SAMPLE_DATA_DIR"]) / f"{name}.json"
        return {r["id"]: r for r in json.loads(data.read_text())["records"]}

    RETURNS = _load("returns")
    CUSTOMERS = _load("customers")

    def returns_get_case(rma_id: str) -> dict:
        """Fetch one return case."""
        return RETURNS.get(rma_id) or {"error": "not_found"}

    def customer_get_profile(customer_id: str) -> dict:
        """Profile with masked email only."""
        c = CUSTOMERS[customer_id]
        return {"id": c["id"], "email_masked": c["email_masked"]}

    def returns_record_recommendation(rma_id: str, decision: str, citations: list[str]) -> dict:
        """Record; never settles."""
        if decision not in DECISIONS:
            return {"error": "invalid_decision"}
        RETURNS[rma_id]["recommendation"] = {"decision": decision, "citations": citations}
        return {"ok": True}

    def build_server():
        from fastmcp import FastMCP  # lazy: never imported by Pattern 0
        return FastMCP("x")
    ''')


def _project(tmp_path, *, agents=True, server=True):
    data = tmp_path / "specs" / "sample-data"
    data.mkdir(parents=True)
    (data / "returns.json").write_text(json.dumps({"records": [
        {"id": "RMA-001", "customer_id": "CUST-01", "refund_amount": 89.0, "status": "in_triage"},
        {"id": "RMA-002", "customer_id": "CUST-02", "refund_amount": 640.0, "status": "in_triage"}]}))
    (data / "customers.json").write_text(json.dumps({"records": [
        {"id": "CUST-01", "email": "ada@example.com", "email_masked": "a***@example.com"},
        {"id": "CUST-02", "email": "bo@example.com", "email_masked": "b***@example.com"}]}))
    (tmp_path / "specs" / "SPEC.md").write_text(SPEC)
    knowledge = tmp_path / "specs" / "knowledge"
    knowledge.mkdir()
    (knowledge / "return-policy.md").write_text(
        "# Policy\n\n## RP-2.1 Return window\nItems within 30 days may be refunded.\n\n"
        "## RP-4.1 Value ceiling\nRefunds above EUR 250 need a supervisor.\n")
    skill = tmp_path / "src" / "agent" / "skills" / "case-intake"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: case-intake\ndescription: Load the case\n---\nUse returns_get_case.\n")
    if agents:
        (tmp_path / "AGENTS.md").write_text("# AGENTS\n\nFIXTURE-INSTRUCTIONS: cite a clause every time.\n")
    if server:
        (tmp_path / "src" / "mcp").mkdir(parents=True)
        (tmp_path / "src" / "mcp" / "server.py").write_text(DESIGN_SERVER)
    return discover(tmp_path)


def _results(model):
    return [content.result for message in model.requests[-1] for content in message.contents
            if content.type == "function_result"]


def test_project_agents_md_becomes_instructions_with_safe_defaults(tmp_path):
    layout = _project(tmp_path)
    model = Model(outputs())
    agent, _ = agent_wiring.build_agent(layout, chat_client=model)
    asyncio.run(agent.run("hello"))
    instructions = model.options["instructions"]
    assert "FIXTURE-INSTRUCTIONS" in instructions
    assert "human approval" in instructions and "sample data" in instructions


def test_default_instructions_without_agents_md(tmp_path):
    layout = _project(tmp_path, agents=False)
    model = Model(outputs())
    agent, _ = agent_wiring.build_agent(layout, chat_client=model)
    asyncio.run(agent.run("hello"))
    assert "Pattern 0 quickstart agent" in model.options["instructions"]


def test_spec_tool_names_replace_generic_crud(tmp_path):
    layout = _project(tmp_path)
    model = Model(outputs())
    agent, _ = agent_wiring.build_agent(layout, chat_client=model)
    asyncio.run(agent.run("hello"))
    modes = {t.name: t.approval_mode for t in model.tools}
    for name in ("returns_get_case", "customer_get_profile", "return_policy_search"):
        assert modes[name] == "never_require", name
    assert modes["returns_record_recommendation"] == "always_require"
    assert not [n for n in modes if n.startswith(("list_", "get_", "update_"))]


def test_design_server_safeguards_and_knowledge_resolve_without_approval(tmp_path):
    layout = _project(tmp_path)
    model = Model(outputs(
        call("customer_get_profile", customer_id="CUST-01"),
        call("return_policy_search", query="RP-4.1 value ceiling"),
    ))
    agent, _ = agent_wiring.build_agent(layout, chat_client=model)
    result = asyncio.run(agent.run("Triage RMA-002"))
    assert not result.user_input_requests
    text = " ".join(str(r) for r in _results(model))
    assert "a***@example.com" in text and "ada@example.com" not in text
    assert "RP-4.1" in text and "local" in text


def test_adapter_serves_spec_tools_over_sample_data_without_design_server(tmp_path):
    layout = _project(tmp_path, server=False)
    model = Model(outputs(call("returns_get_case", rma_id="RMA-002")))
    agent, _ = agent_wiring.build_agent(layout, chat_client=model)
    asyncio.run(agent.run("Load RMA-002"))
    assert "640.0" in " ".join(str(r) for r in _results(model))


@pytest.mark.parametrize("server", [True, False])
def test_headless_turn_denies_unapproved_write_and_finishes(tmp_path, server):
    from threadlight_quickstart.approvals import run_turn

    layout = _project(tmp_path, server=server)
    model = Model(outputs(
        call("returns_get_case", rma_id="RMA-001"),
        call("returns_record_recommendation", rma_id="RMA-001", decision="approve_refund", citations=["RP-2.1"]),
    ))
    agent, stores = agent_wiring.build_agent(layout, chat_client=model)
    turn = asyncio.run(run_turn(agent, "Triage RMA-001"))
    assert turn.text == '{"status":"finished"}'
    assert [(d["tool"], d["approved"]) for d in turn.decisions] == [("returns_record_recommendation", False)]
    assert "recommendation" not in stores["returns"].get("RMA-001")
    rejected = [r for r in _results(model)]
    assert rejected, "the model must see the rejection and continue"


def test_headless_turn_runs_an_explicitly_approved_write(tmp_path):
    from threadlight_quickstart.approvals import run_turn

    layout = _project(tmp_path)
    model = Model(outputs(
        call("returns_record_recommendation", rma_id="RMA-001", decision="approve_refund", citations=["RP-2.1"]),
    ))
    agent, _ = agent_wiring.build_agent(layout, chat_client=model)
    turn = asyncio.run(run_turn(agent, "Triage RMA-001", approve_tools={"returns_record_recommendation"}))
    assert [(d["tool"], d["approved"]) for d in turn.decisions] == [("returns_record_recommendation", True)]
    assert "ok" in " ".join(str(r) for r in _results(model))


def test_headless_turn_never_approves_skill_scripts(tmp_path):
    from threadlight_quickstart.approvals import run_turn

    layout = _project(tmp_path)
    model = Model(outputs(call("run_skill_script", skill_name="case-intake", script_name="return_policy_search")))
    agent, _ = agent_wiring.build_agent(layout, chat_client=model)
    turn = asyncio.run(run_turn(agent, "go", approve_tools={"run_skill_script"}))
    assert [(d["tool"], d["approved"]) for d in turn.decisions] == [("run_skill_script", False)]
    assert turn.text == '{"status":"finished"}'


def test_check_reports_spec_tools_and_knowledge(tmp_path, capsys):
    from threadlight_quickstart import cli

    layout = _project(tmp_path)
    assert cli._check(layout) == 0
    out = capsys.readouterr().out
    for marker in ("returns_get_case", "design server", "return_policy_search", "knowledge",
                   "returns_record_recommendation", "approval", "AGENTS.md"):
        assert marker in out, marker


def test_ask_cli_runs_headless_prompts(tmp_path, monkeypatch, capsys):
    from threadlight_quickstart import cli

    layout = _project(tmp_path)
    model = Model(outputs(call("return_policy_search", query="RP-2.1")))
    monkeypatch.setattr(agent_wiring, "build_chat_client", lambda: model)
    assert cli.main(["--root", str(layout.root), "--ask", "Which clause covers the window?"]) == 0
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["turns"][0]["text"] == '{"status":"finished"}'
    assert report["turns"][0]["error"] is None


def test_ui_resolves_approval_with_human_decision(tmp_path):
    from threadlight_quickstart.approvals import PendingApproval, continue_turn, pending_from_error

    layout = _project(tmp_path)
    model = Model(outputs(
        call("returns_record_recommendation", rma_id="RMA-001", decision="approve_refund", citations=["RP-2.1"]),
    ))
    agent, _ = agent_wiring.build_agent(layout, chat_client=model)

    async def run():
        updates = []
        try:
            async for update in agent.run("Triage RMA-001", stream=True):
                updates.append(update)
        except RuntimeError as exc:
            pending = pending_from_error("Triage RMA-001", updates, exc)
        assert isinstance(pending, PendingApproval)
        assert [r["tool"] for r in pending.describe()] == ["returns_record_recommendation"]
        return await continue_turn(agent, pending, approved=True)

    turn = asyncio.run(run())
    assert turn.text == '{"status":"finished"}'
    assert "ok" in " ".join(str(r) for r in _results(model))


def test_streamlit_turn_hands_a_pending_write_to_the_approval_panel(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    monkeypatch.delenv("THREADLIGHT_QUICKSTART_ROOT", raising=False)
    from threadlight_quickstart.ui_streamlit import _stream_response

    class Placeholder:
        text = ""

        def markdown(self, value):
            self.text = value

    layout = _project(tmp_path)
    model = Model(outputs(
        call("returns_record_recommendation", rma_id="RMA-001", decision="approve_refund", citations=["RP-2.1"]),
    ))
    agent, _ = agent_wiring.build_agent(layout, chat_client=model)
    pending = []
    response = asyncio.run(_stream_response(agent, "Triage RMA-001", Placeholder(), pending))
    assert "approval_required" in response
    assert [r["tool"] for r in pending[0].describe()] == ["returns_record_recommendation"]


def test_knowledge_search_cites_paragraph_clauses(tmp_path):
    from threadlight_quickstart import knowledge

    (tmp_path / "policy.md").write_text(
        "# Policy\n\n## RP-3 Fault claims\n\nRP-3.1 **Fault reasons** are defective and arrived_damaged.\n\n"
        "RP-3.2 **Evidence.** A defective claim needs at least one photo of the fault.\n")
    hit = knowledge.search(tmp_path, "RP-3.2 photo evidence")
    assert hit["citations"][0]["clause_id"] == "RP-3.2"
    assert "photo" in hit["citations"][0]["text"]
    assert knowledge.search(tmp_path, "unrelated zebra")["citations"] == []
