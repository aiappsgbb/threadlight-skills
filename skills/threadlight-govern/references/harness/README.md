# Governed writes for Copilot harness agents (client function tools)

`agent_type: harness` (Foundry Copilot harness, preview, user opt-in only:
`skills/_shared/foundry-only-agents.md` item 2d) runs the agent loop in
Foundry. The harness has **no Agent Hooks surface**, and the service manages
approval for its built-in tools
([Learn](https://learn.microsoft.com/azure/foundry/agents/how-to/use-tools-skills-copilot-harness)).
So the in-process `pre_tool_call` PEP of the hosted runtime cannot run there.

For a consequential write, declare it on the agent as a **client-executed
function tool** (non-strict schema; `strict: true` is not supported by the
harness). The turn ends with a `function_call` item carrying the exact write
arguments, and [`function_bridge.py`](function_bridge.py) on the application
side:

1. answers **only its own declared function names**: harness-internal tools
   (`skill`, `view`, ...) also surface as `function_call` items, and answering
   their `call_id` returns HTTP 400 (tested Nov 2026);
2. runs the call through the existing governed tool gateway
   (`GovernedDispatcher.dispatch`): signed fresh policy, authenticated one-use
   approval, trusted facts and the central audit ACK before any effect, with
   idempotency key `harness:<conversation>:<call_id>`;
3. returns the result as `function_call_output` (`DENIED_BY_POLICY` with
   `effect: none` on a denial; the model was observed to report "not persisted"
   and not to retry);
4. reconciles a transient `failed` turn before replay: it lists the
   conversation items and resends only outputs the conversation did not record,
   never re-executing a call.

The app executes one declared tool per call; it does not choose tools, call
the model or loop. The alternative seam is an MCP tool on a governed MCP server
with `require_approval` (the harness honours `mcp_approval_request` /
`mcp_approval_response` in the same conversation).

## Which governance claims apply

This is a **different PEP placement** from the hosted runtime.

| Claim | Harness function-tool route |
| --- | --- |
| Signed fresh policy, one-use approval, trusted facts, audit ACK before the effect | **applies**: enforced by the same gateway dispatcher, for declared function tools only |
| Exactly one effect per `call_id`, idempotent replay | **applies**: gateway reservation keyed by conversation and `call_id` |
| Denial has no effect and returns a receipt | **applies** |
| In-process ACS on every tool call (Agent Hooks `pre_tool_call`) | **does not apply**: no hook surface; harness built-ins (shell, web, file write) are governed only by being disabled and by the egress policy |
| Hosted LOCAL-14 native-local evidence and the CTK vectors | **does not apply**: they test the hosted interceptor, not this placement |
| Governance of reads through MCP tools | **does not apply** unless those tools are served by the governed gateway |
| Live write against a real backend | not evidenced; the local tests use the real ACS/OPA, control plane and dispatcher with an HTTP double for the backend and the Foundry endpoint |

Tests: `skills/threadlight-govern/tests/test_harness_function_bridge.py`
(native gateway run of `scripts/ci/run-governance-pin-tests.py`).
