THREADLIGHT_CANARY_ONLY = True
TOOL_GOVERNANCE_BINDING = "threadlight.tool-governance/mcp-server/v1"


def before_tool_call(tool_name: str) -> str:
    return f"governed:{tool_name}"
