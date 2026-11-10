"""Action mode of a pilot workspace: may the agent act, or only recommend?

threadlight-design Fast-PoC pilots are **recommend-only**: ``specs/manifest.json``
declares ``"action_mode": "recommend-only"``, SPEC § 6 exposes only read tools
and SPEC § 11a records governance as not selected (``mode: off``) with every
read unbound. The agent proposes the decision with citations; recording or
applying it stays a human action. Adding a consequential tool is the Production
path (Full-mode design plus threadlight-govern), never an unbound write.

Workshop **governed-write** pilots (``"action_mode": "governed-write"``) show
ONE governed write live: SPEC § 11a selects governance (``mode: selective``)
for exactly one consequential tool bound to the governed tool gateway at
``pre_tool_call``; every other tool stays an unbound read. Human approval is
selected in the signed gateway registry (``approval_mode: deferred``,
``approval_requirement: policy``), so the contract never requires ``approval``.
The SPEC records the policy rules, the downstream service and the shared
governance foundation that must exist before deploy.

An absent ``action_mode`` keeps the existing behaviour (the SPEC decides).
These are offline design-consistency checks, not runtime evidence.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RECOMMEND_ONLY = "recommend-only"
ACT = "act"
GOVERNED_WRITE = "governed-write"
ACTION_MODES = frozenset({RECOMMEND_ONLY, ACT, GOVERNED_WRITE})
GOVERNED_WRITE_FRAMEWORKS = frozenset({"github-copilot-sdk", "microsoft-agent-framework"})
GOVERNED_WRITE_REQUIRES = ("authorization", "signed-policy-bundle", "decision-receipt", "idempotency-or-transaction")
CONTRACT_APPROVAL_TOKENS = frozenset({"approval", "human-approval-record"})


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


def is_governed_write(workspace: Path) -> bool:
    return read_action_mode(workspace) == GOVERNED_WRITE


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


def _bold_bullet(spec: str, label: str) -> str | None:
    """Text of ``- **label** ...`` up to the next bold bullet, heading or code fence (sub-items kept)."""
    m = re.search(rf"^[ \t]*[-*][ \t]*\*\*{label}\*\*(.*?)(?=^[ \t]*[-*][ \t]*\*\*|^#|^```|\Z)",
                  spec, re.M | re.S | re.I)
    return m.group(1) if m else None


def _design_record_gaps(spec: str, binding: str | None) -> list[str]:
    """SPEC § 11a/§ 13 must record the policy rules, the downstream service and the foundation."""
    gaps: list[str] = []
    rules = _bold_bullet(spec, r"Policy\s+rules")
    items = re.findall(r"^[ \t]+[-*][ \t]+(.+)$", rules or "", re.M)
    outcomes = {o for item in items for o in ("allow", "deny", "escalate") if re.search(rf"\b{o}", item, re.I)}
    missing = [o for o in ("allow", "deny", "escalate") if o not in outcomes]
    if rules is None or len(items) < 3 or missing:
        gaps.append(f"action-mode: governed-write pilot must record SPEC § 11a **Policy rules** for "
                    f"`{binding or 'the binding'}`: the 3-4 business rules the Rego policy implements, as sub-items "
                    "covering allow, deny and escalate (a person approves an escalation)"
                    + (f"; missing {', '.join(missing)}" if rules is not None and missing else ""))
    service = _bold_bullet(spec, r"Downstream\s+service")
    if service is None or not re.search(r"\bPOST\b", service) or not re.search(r"\bGET\b", service):
        gaps.append("action-mode: governed-write pilot must record the SPEC § 11a **Downstream service** the "
                    "gateway calls: an idempotent POST endpoint for the effect and a GET outcome endpoint, "
                    "authorizing the gateway identity separately")
    foundation = _bold_bullet(spec, r"Governance\s+foundation")
    if foundation is None or not foundation.strip(" :\n"):
        gaps.append("action-mode: governed-write pilot must record the SPEC § 11a **Governance foundation** "
                    "(shared control plane, gateway, Key Vault signing key, Cosmos audit/approvals) that must "
                    "exist before deploy")
    return gaps


def _governed_write_contract(workspace: Path):
    """``(spec_text, normalized_contract, gaps)`` for a governed-write workspace."""
    from skills._shared.governance import GovernanceContractError, validate_governance_contract
    from skills._shared.governance_selection import spec_contract

    spec_path = workspace / "specs" / "SPEC.md"
    if not spec_path.is_file():
        return None, None, ["action-mode: governed-write pilot has no specs/SPEC.md"]
    try:
        document = spec_contract(workspace)[0]
    except (ValueError, OSError) as exc:
        return None, None, [f"action-mode: governed-write pilot has an unreadable SPEC § 11a contract ({exc})"]
    if not isinstance(document, dict) or not isinstance(document.get("governance"), dict):
        return None, None, ["action-mode: governed-write pilot must record SPEC § 11a governance as "
                            "`mode: selective`"]
    governance = document["governance"]
    gaps: list[str] = []
    if governance.get("mode") != "selective":
        gaps.append(f"action-mode: governed-write pilot must record governance `mode: selective` in SPEC § 11a "
                    f"(found `{governance.get('mode')}`)")
    if governance.get("lifecycle_bindings"):
        gaps.append("action-mode: governed-write pilot declares lifecycle bindings; it binds one tool only")
    if document.get("framework") not in GOVERNED_WRITE_FRAMEWORKS:
        gaps.append(f"action-mode: governed-write pilot framework `{document.get('framework')}` is not "
                    "github-copilot-sdk or microsoft-agent-framework")
    try:
        contract = validate_governance_contract(document, deployment_target="demo-sandbox")
    except (GovernanceContractError, ValueError) as exc:
        gaps.append(f"action-mode: governed-write pilot has an invalid SPEC § 11a contract ({exc})")
        return spec_path.read_text(encoding="utf-8"), None, gaps
    flagged = {raw.get("id") for raw in document.get("tools") or []
               if isinstance(raw, dict) and raw.get("approval_required") is True}
    for tool in contract["tools"]:
        tool["approval_required"] = tool["id"] in flagged
    return spec_path.read_text(encoding="utf-8"), contract, gaps


def _bound_write_gaps(tool: dict) -> list[str]:
    tool_id = tool["id"]
    gaps: list[str] = []
    if tool["consequence"] == "unknown":
        gaps.append(f"action-mode: governed-write tool {tool_id} has `consequence: unknown`; classify the "
                    "governed write (write, external-egress or irreversible)")
    if tool["enforcement_path"] != "governed-tool-gateway" or tool["policy_binding"] is None:
        gaps.append(f"action-mode: governed-write tool {tool_id} must be bound (`policy_binding`) to "
                    f"`enforcement_path: governed-tool-gateway` (found `{tool['enforcement_path']}`); "
                    "no local hooks and never an unbound write")
    if "pre_tool_call" not in tool["intervention_points"]:
        gaps.append(f"action-mode: governed-write tool {tool_id} must intervene at `pre_tool_call`")
    requires = tool.get("requires") or []
    missing = [token for token in GOVERNED_WRITE_REQUIRES if token not in requires]
    if missing:
        gaps.append(f"action-mode: governed-write tool {tool_id} must require {', '.join(missing)}")
    approval = sorted(CONTRACT_APPROVAL_TOKENS & set(requires))
    if approval or tool.get("approval_required") is True:
        gaps.append(f"action-mode: governed-write tool {tool_id} must not require contract approval "
                    f"({', '.join(approval) or 'approval_required: true'}): approval is selected in the signed "
                    "gateway registry as `approval_mode: deferred`, `approval_requirement: policy`, so an ACS "
                    "allow stays autonomous and only an escalate needs a person")
    return gaps


def governed_write_gaps(workspace: Path) -> list[str]:
    """Why a governed-write workspace is not consistent; ``[]`` when it is (or is not governed-write)."""
    workspace = Path(workspace)
    if not is_governed_write(workspace):
        return []
    spec, contract, gaps = _governed_write_contract(workspace)
    if spec is None:
        return gaps
    bound: list[dict] = []
    reads: set[str] = set()
    for tool in (contract or {}).get("tools", []):
        if tool["consequence"] != "read":
            bound.append(tool)
        elif tool["enforcement_path"] == "none":
            reads.add(tool["id"])
        else:
            gaps.append(f"action-mode: governed-write pilot binds read tool {tool['id']}; every tool but the "
                        "one write must be an unbound read")
    if contract is not None:
        if len(bound) != 1:
            found = ", ".join(t["id"] for t in bound) or "none"
            gaps.append(f"action-mode: governed-write pilot must bind exactly one consequential tool in SPEC § 11a "
                        f"(found {found}); move any other write to a human step (SPEC § 8)")
        for tool in bound:
            gaps.extend(_bound_write_gaps(tool))
    write = bound[0]["id"] if len(bound) == 1 else None
    from skills._shared.spec_tool_contracts import section6_inventory

    inventory = section6_inventory(spec)
    for entry in inventory["unparsed"]:
        # Fail closed: an entry the deploy parser cannot read could be a second write.
        gaps.append(f"action-mode: governed-write pilot has a SPEC § 6 {entry} that is not a parseable "
                    "tool contract; write it as `### `tool_name`` with its bullets, or move it out of § 6")
    section6 = {tool["name"]: tool for tool in inventory["tools"]}
    if write is not None and write not in section6:
        gaps.append(f"action-mode: governed-write tool {write} is not listed in SPEC § 6")
    elif write is not None and not section6[write].get("side_effects"):
        gaps.append(f"action-mode: governed-write tool {write} must declare its side effect in SPEC § 6 "
                    "(**Side Effects**)")
    for name, tool in section6.items():
        if name == write or (contract is not None and name in {t["id"] for t in bound}):
            continue
        if tool["kind"] != "read" or name not in reads:
            gaps.append(f"action-mode: governed-write pilot exposes SPEC § 6 tool {name} that SPEC § 11a does not "
                        "classify as an unbound read; only the one governed write may change anything")
        elif tool.get("side_effects"):
            gaps.append(f"action-mode: governed-write pilot exposes SPEC § 6 tool {name} that declares a side "
                        f"effect ({tool['side_effects'][:80]}); only the one governed write may change anything")
    gaps.extend(_design_record_gaps(spec, bound[0]["policy_binding"] if len(bound) == 1 else None))
    return gaps


def governed_write_tool(workspace: Path) -> str | None:
    """The one SPEC § 11a tool a governed-write workspace binds to the gateway, or None.

    None when the workspace is not governed-write or does not bind exactly one
    consequential tool; callers check :func:`governed_write_gaps` first.
    """
    workspace = Path(workspace)
    if not is_governed_write(workspace):
        return None
    _spec, contract, _gaps = _governed_write_contract(workspace)
    bound = [t["id"] for t in (contract or {}).get("tools", []) if t["consequence"] != "read"]
    return bound[0] if len(bound) == 1 else None
