# Phase 1d: Runtime variant comparison

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when comparing the GHCP SDK, MAF Agent and MAF Workflow variants.

| | **github-copilot-sdk** (`default-agent` route) | **MAF Agent** (`maf-agent-capabilities` route) | **MAF Workflow** (`deterministic-workflow` route when `workflow_model: "workflow"` in SPEC § 11e) |
|--|----------------------|-------------------|-----|
| **Runtime** | `CopilotClient` + `InvocationAgentServerHost` | `Agent` + `FoundryChatClient` + `ResponsesHostServer` | `Workflow` + typed `Executor` nodes + `FoundryChatClient` |
| **Protocol** | Invocations (SSE streaming) | Responses | Responses (workflow orchestrator manages step sequence) |
| **Orchestration** | Agent-driven (LLM decides tool order) | Agent-driven (LLM decides tool order) | **Deterministic** (workflow graph defines phase order; LLM runs inside individual executors) |
| **Skill loading** | `SkillsProvider` (progressive) | `SkillsProvider.from_paths()` (progressive, recommended) | Per-executor skill context (each phase reads its own skills) |
| **MCP** | `mcp_servers` parameter | `client.get_mcp_tool()` | Per-executor MCP tools (same as MAF agent) |
| **HITL** | Tool-mediated | Tool-mediated | **Workflow pause points** (durable wait-for-external-event; persona gates map to pause/resume) |
| **Custom `@tool`** | ❌ Not supported | ✅ Supported | ✅ Supported (inside executors) |
| **Tool loop timeout** | No limit (SSE keeps alive) | 120s gateway timeout | Per-executor timeout (workflow manages overall) |
| **Auth** | BYOK (`DefaultAzureCredential` → bearer token) | `DefaultAzureCredential` → `FoundryChatClient` | Same as MAF Agent |
| **Best for** | Open-ended chat, Q&A, RAG, exploration | Data queries, Toolbox, file generation | **Deterministic multi-phase processes with persona gates** (expense claim, hiring, KYC, contract review) |

> **MAF Workflow column = target design, not generated output.** Threadlight
> does not ship MAF Workflow (`Workflow` / `Executor` / workflow-graph)
> templates: workflow-graph generation is **not shipped**. For the
> `deterministic-workflow` route, Phase 2 generates the **MAF Agent** container
> (`Agent` + `FoundryChatClient` + `ResponsesHostServer`, Responses) — see the
> Workflow model gate in Phase 2.
