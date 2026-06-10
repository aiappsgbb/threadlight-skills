---
kind: repo-edit
summary: Add HITL gate implementation to agent code
target_file: src/app.py
edit_type: insert
---

## Target file
`src/app.py` (or the main agent orchestration module — search for the entry point that processes tool calls or agent actions).

## Edit type
`insert`

## Edit recipe
1. Locate the function that executes tool calls or mutations (e.g., `execute_tool()`, `run_action()`, or the main agent loop).
2. Before any external API call or state mutation, insert a call to `require_human_approval()` or similar gate:

   ```python
   async def require_human_approval(action: str, context: dict) -> bool:
       """HITL gate: request human approval before executing action."""
       approval_id = str(uuid.uuid4())
       # Log the request to audit trail (reference HITL-003 for storage)
       await log_hitl_event(approval_id, "approval_requested", action, context)
       # Send approval card to Teams / Slack / webhook (reference HITL-001 for channel)
       await send_approval_card(approval_id, action, context)
       # Wait for response (with timeout, e.g., 1 hour)
       approved = await wait_for_approval(approval_id, timeout=3600)
       await log_hitl_event(approval_id, "approval_response", {"approved": approved}, context)
       return approved
   ```

3. In the tool-call handler, add a guard:

   ```python
   if is_mutating_action(tool_name):
       if not await require_human_approval(tool_name, {"input": args}):
           return {"status": "rejected", "reason": "Human approval declined"}
   result = await execute_tool(tool_name, args)
   ```

4. Ensure idempotency: pass an `idempotency_key` or correlation ID with every approval request and log it alongside the action.

## Verification
Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`. HITL-002 should flip from `fail` to `pass` (regex will find "hitl\|approval_gate\|human.in.the.loop" in src/).

If still failing, confirm the search pattern matches; check the exact function name in your codebase and adjust the regex if needed.
