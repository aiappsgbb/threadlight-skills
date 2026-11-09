"""Action mode of a pilot workspace: may the agent act, or only recommend?

threadlight-design Fast-PoC pilots are **recommend-only**: ``specs/manifest.json``
declares ``"action_mode": "recommend-only"``, SPEC § 6 exposes only read tools
and SPEC § 11a records governance as not selected (``mode: off``) with every
read unbound. The agent proposes the decision with citations; recording or
applying it stays a human action. Adding a consequential tool is the Production
path (Full-mode design plus threadlight-govern), never an unbound write.

An absent ``action_mode`` keeps the existing behaviour (the SPEC decides).
This is an offline design-consistency check, not runtime evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

RECOMMEND_ONLY = "recommend-only"
ACT = "act"
ACTION_MODES = frozenset({RECOMMEND_ONLY, ACT})



def read_action_mode(workspace: Path) -> str | None:
    """``action_mode`` declared in ``specs/manifest.json``, or None when absent."""
    path = Path(workspace) / "specs" / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"specs/manifest.json unreadable: {exc}") from exc
    if not isinstance(manifest, dict) or "action_mode" not in manifest:
        return None
    mode = manifest["action_mode"]
    if mode not in ACTION_MODES:
        raise ValueError(f"specs/manifest.json action_mode {mode!r} is not one of {sorted(ACTION_MODES)}")
    return mode


def is_recommend_only(workspace: Path) -> bool:
    return read_action_mode(workspace) == RECOMMEND_ONLY


def section6_tools(spec: str) -> list[str]:
    """Tool names SPEC § 6 exposes, parsed exactly as threadlight-deploy builds its MCP tool plan."""
    from skills._shared.spec_tool_contracts import section6_inventory

    return [tool["name"] for tool in section6_inventory(spec)["tools"]]


def recommend_only_gaps(workspace: Path) -> list[str]:
    """Why a recommend-only workspace is not consistent; ``[]`` when it is (or is not recommend-only)."""
    workspace = Path(workspace)
    if not is_recommend_only(workspace):
        return []
    from skills._shared.governance_selection import spec_contract

    spec_path = workspace / "specs" / "SPEC.md"
    if not spec_path.is_file():
        return ["action-mode: recommend-only pilot has no specs/SPEC.md"]
    try:
        document = spec_contract(workspace)[0]
    except (ValueError, OSError) as exc:
        return [f"action-mode: recommend-only pilot has an unreadable SPEC § 11a contract ({exc})"]
    if not isinstance(document, dict) or not isinstance(document.get("governance"), dict):
        return ["action-mode: recommend-only pilot must record SPEC § 11a governance as `mode: off`"]
    gaps: list[str] = []
    governance = document["governance"]
    if governance.get("mode") != "off":
        gaps.append(f"action-mode: recommend-only pilot must record governance `mode: off` in SPEC § 11a "
                    f"(found `{governance.get('mode')}`)")
    if governance.get("lifecycle_bindings"):
        gaps.append("action-mode: recommend-only pilot declares lifecycle bindings")
    reads: set[str] = set()
    for tool in document.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        tool_id = tool.get("id")
        if tool.get("consequence") == "read" and tool.get("enforcement_path", "none") == "none":
            reads.add(tool_id)
        else:
            gaps.append(f"action-mode: recommend-only pilot exposes consequential tool {tool_id} "
                        f"({tool.get('consequence', 'unknown')}); move it to a human step (SPEC § 8) "
                        "or take the Production path with governance")
    from skills._shared.spec_tool_contracts import section6_inventory

    inventory = section6_inventory(spec_path.read_text(encoding="utf-8"))
    for entry in inventory["unparsed"]:
        # Fail closed: an entry the deploy parser cannot read could be a write.
        gaps.append(f"action-mode: recommend-only pilot has a SPEC § 6 {entry} that is not a parseable "
                    "tool contract; write it as `### `tool_name`` with its bullets, or move it out of § 6")
    for tool in inventory["tools"]:
        name = tool["name"]
        if any(f" {name} " in f"{gap} " for gap in gaps):
            continue
        if tool["kind"] != "read" or name not in reads:
            gaps.append(f"action-mode: recommend-only pilot exposes SPEC § 6 tool {name} "
                        "that SPEC § 11a does not classify as a read")
        elif tool.get("side_effects"):
            gaps.append(f"action-mode: recommend-only pilot exposes SPEC § 6 tool {name} that declares a "
                        f"side effect ({tool['side_effects'][:80]}); move it to a human step (SPEC § 8)")
    return gaps
