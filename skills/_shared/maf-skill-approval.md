# Autonomous MAF skill reads

MAF Python **1.10.0** intentionally made all `SkillsProvider` tools
`always_require` ([upstream change](https://github.com/microsoft/agent-framework/pull/6754)).
An advertised skill name or a successful business tool call is not proof that
the model received the skill body. A host that only reads `result.text` can
mistake an approval pause for an empty completed report.

For **trusted local/packaged business skills**, configure the provider itself:

```python
from agent_framework import SkillsProvider

skills_provider = SkillsProvider.from_paths(
    skills_dir,
    disable_load_skill_approval=True,
    disable_read_skill_resource_approval=True,
)
```

These native flags were added in
[Python 1.11.0](https://github.com/microsoft/agent-framework/pull/6867) and are
supported by the quickstart's **1.13.0** baseline and the generated host's
**1.14.0** pin. No SDK upgrade is needed. Leave
`disable_run_skill_script_approval` at its default `False`; do not add a script
runner implicitly. Preserve separately reviewed script policies, business-tool
approvals, Agent Hooks, ACS and authenticated HITL paths.

The trust decision belongs to the application owner: the configured directory
must contain reviewed, packaged instructions and static resources, not
user uploads or remotely mutable/untrusted skills. The exemption applies only
to this provider's two read tools. It does not authorize scripts, business
effects, other providers, MCP tools or hosted same-name tools.

The [official read-only middleware alternative](https://learn.microsoft.com/en-us/agent-framework/agents/skills?pivots=programming-language-python#tool-approval)
is `ToolApprovalMiddleware(auto_approval_rules=[SkillsProvider.read_only_tools_auto_approval_rule])`.
It requires an `AgentSession` on every run in these SDK versions and matches
local calls by name; reserve those names and exclude collisions. Its native
`server_label` check rejects hosted calls, but a local MCP wrapper with a
colliding name is still a local tool. The provider flags avoid that broader
name-based grant and fit Threadlight's existing session/middleware composition.
Never substitute all-tools auto approval or a blanket approving lambda.

## Host and generation contract

- Quickstart and generated MAF hosts install `RequireResolvedApprovals` from the
  portable `skill_approval.py` helper. Deploy vendors the quickstart's exact
  helper into generated packages. It raises `ApprovalRequiredError` with
  `approval_required` when `user_input_requests` remain, for both complete
  responses and streaming updates/final responses. It never fabricates consent
  or changes a tool's approval mode.
- Keep existing middleware ordering and governance. The quickstart UI reports
  the error through its existing error display/transcript path; headless callers
  must treat it as an incomplete/failed turn, not JSON report text. Generated
  Responses hosts use their native error response/event path. Partial streamed
  text is not a completed report.
- An application with a real approval/resume channel should handle native
  `user_input_requests` in that channel instead of installing an unattended-host
  rejection boundary. Signed deferred gateway review operations are a separate
  protocol and retain their existing pending/resume behavior.
- For each generated MAF agent/executor, carry this provider configuration into
  the actual runtime, not just the SPEC. Prove actual `load_skill` and
  `read_skill_resource` results reach the model and that it continues. Test script,
  consequential-tool, untrusted-provider and same-name remote approvals
  separately. A boot check, catalog list or successful JSON parse is insufficient.

This is local runtime compatibility, not live deployment or whole-agent
governance evidence. GHCP has a different skill loader; do not apply these MAF
flags to it. Existing Kratos exports keep their runtime ownership: report a
missing policy/approval handler as a compatibility gap, not permission to
silently regenerate the export. External `awesome-gbb/foundry-hosted-agents`
examples may need the same scoped update; they are not changed by this catalog.
