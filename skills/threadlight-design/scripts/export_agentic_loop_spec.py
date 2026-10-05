#!/usr/bin/env python3
"""Export a Threadlight ``specs/SPEC.md`` as an Agentic Loop ``docs/spec.md``.

Agentic Loop / Spec2Cloud runs Specify -> Plan -> Implement -> Verify -> Deploy
and expects ``./docs/spec.md`` in the lean ``specify`` template shape. This
exporter writes that shape from an existing Threadlight SPEC so a team can hand
a Threadlight design to the Agentic Loop build loop without retyping it.

It is a one-way, deterministic, standard-library projection:

* business rules become traceable ``FR-xxx`` requirements (``BR-xxx`` kept);
* human action gates, tool side effects, data models, scenarios, security,
  residency and open questions are carried;
* missing inputs become ``[NEEDS CLARIFICATION: ...]`` markers, never guesses;
* a provenance appendix lists every source section and where it went, including
  the sections that are deliberately not carried.

The Threadlight SPEC stays the source of truth. Re-export after changing it.
This script does not run Agentic Loop, validate its policy layer or deploy.

Usage::

    python3 export_agentic_loop_spec.py specs/SPEC.md -o docs/spec.md [--force]
    python3 export_agentic_loop_spec.py specs/SPEC.md            # stdout
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

NEEDS = "[NEEDS CLARIFICATION: {}]"

SERVICE_NAMES = {
    "cosmos-db": "Azure Cosmos DB for NoSQL",
    "ai-search": "Azure AI Search",
    "doc-intel": "Azure AI Document Intelligence",
    "azure-vision": "Azure AI Vision",
    "azure-speech": "Azure AI Speech",
    "event-grid": "Azure Event Grid",
    "service-bus": "Azure Service Bus",
    "storage-blob": "Azure Storage (Blob)",
    "app-insights": "Application Insights",
    "aca-job": "Azure Container Apps jobs",
    "aca-mcp": "Azure Container Apps (MCP server)",
    "aca-bot": "Azure Container Apps (Teams bot)",
    "key-vault": "Azure Key Vault",
    "foundry-iq-index": "Foundry IQ knowledge base",
}

# (source-heading keyword, exported to, Threadlight skill that keeps owning it)
PROVENANCE = [
    ("process overview", "§ 1, § 2, § 3", None),
    ("process flow", "§ 6", None),
    ("business rules", "§ 4", None),
    ("data models", "§ 10", None),
    ("system integrations", "§ 11", None),
    ("external systems", None, "threadlight-mcp-aca"),
    ("tool contracts", "§ 11", None),
    ("knowledge sources", "§ 9", None),
    ("ai services", "§ 9", None),
    ("human interaction points", "§ 4, § 11", "threadlight-hitl-patterns"),
    ("workspace ux", None, "threadlight-workspace-ui"),
    ("success criteria", "§ 2, § 3, § 5", "threadlight-evals"),
    ("trigger", "§ 5, § 11", "threadlight-event-triggers"),
    ("security", "§ 5", "threadlight-govern"),
    ("tech stack", "§ 7, § 8", None),
    ("demo data", None, "threadlight-demo-data-factory"),
    ("workflow model", "§ 6", None),
    ("deployment posture", None, "threadlight-deploy"),
    ("production readiness", "§ 5", "threadlight-production-ready"),
    ("assumptions", "§ 12", None),
    ("value model", None, "threadlight-consumption-iq"),
]


# --------------------------------------------------------------------------- parsing


def split_sections(text: str, level: int) -> list[tuple[str, str]]:
    marker = "#" * level + " "
    pattern = re.compile(rf"^{re.escape(marker)}(.+)$", re.M)
    matches = list(pattern.finditer(text))
    out = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = re.sub(r"(?:\n\s*---\s*)+\s*$", "\n", text[match.end():end])
        out.append((match.group(1).strip(), body))
    return out


def title_key(heading: str) -> str:
    return re.sub(r"^\d+[a-z]?\.\s*", "", heading).strip().lower()


def find(sections: list[tuple[str, str]], keyword: str) -> str:
    for heading, body in sections:
        if keyword in title_key(heading):
            return body
    return ""


def find_h3(body: str, keyword: str) -> str:
    return find(split_sections(body, 3), keyword)


def field(body: str, name: str) -> str:
    match = re.search(rf"^[ \t]*(?:- )?\*\*{re.escape(name)}\*\*:[ \t]*(.*)$", body, flags=re.M)
    if not match:
        return ""
    first = match.group(1).strip()
    lines = [first]
    for line in body[match.end():].splitlines()[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith(("**", "- **", "#", "|", ">", "```")):
            break
        if stripped.startswith("- ") and first:
            break
        if stripped.startswith("- "):
            lines.append("; " + stripped[2:].strip())
        else:
            lines.append(" " + stripped)
    text = "".join(lines).strip()
    return re.sub(r"^;\s*", "", text).replace(":; ", ": ")


def field_any(body: str, *names: str) -> str:
    for name in names:
        value = field(body, name)
        if value:
            return value
    return ""


def yaml_list(body: str, key: str) -> list[str]:
    match = re.search(rf"^{re.escape(key)}:\s*\n((?:[ \t]+-[^\n]*\n?)+)", body, flags=re.M)
    if not match:
        return []
    return [line.strip()[1:].strip() for line in match.group(1).splitlines() if line.strip().startswith("-")]


def bullets(body: str) -> list[str]:
    items: list[str] = []
    for line in body.splitlines():
        if re.match(r"^\s*- (?!\[)", line) and not line.startswith("  "):
            items.append(line.strip()[2:].strip())
        elif items and line.startswith("  ") and line.strip() and not line.strip().startswith("- "):
            items[-1] += " " + line.strip()
        elif not line.strip() and items:
            items.append("")
    return [item for item in items if item]


def table(body: str) -> tuple[list[str], list[list[str]]]:
    rows = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", stripped[1:-1])]
            rows.append(cells)
        elif rows:
            break
    if len(rows) < 2:
        return [], []
    return rows[0], [r for r in rows[2:] if any(r)]


def cell(value: str) -> str:
    return re.sub(r"(?<!\\)\|", r"\\|", " ".join(value.split()))


def yaml_value(body: str, key: str) -> str:
    match = re.search(rf"^\s*`?{re.escape(key)}:\s*([^#\n`]+)", body, flags=re.M)
    return match.group(1).strip() if match else ""


# --------------------------------------------------------------------------- export


class Spec:
    def __init__(self, text: str) -> None:
        self.text = text
        title = re.search(r"^# (?:SpecKit:\s*)?(.+)$", text, flags=re.M)
        self.title = title.group(1).strip() if title else "Untitled process"
        generated = re.search(r"^> Generated:\s*(\S+)", text, flags=re.M)
        self.generated = generated.group(1) if generated else "{YYYY-MM-DD}"
        self.h2 = split_sections(text, 2)

    def sec(self, keyword: str) -> str:
        return find(self.h2, keyword)


def _participants(overview: str) -> list[list[str]]:
    header, rows = table(find_h3(overview, "participants"))
    return rows if header else []


def summary(spec: Spec) -> list[str]:
    overview = spec.sec("process overview")
    description = field(overview, "Description") or NEEDS.format("one-paragraph summary of what is built, for whom and why")
    lines = [description]
    domain = field(overview, "Domain")
    persona = field(overview, "Target Persona")
    extra = []
    if domain:
        extra.append(f"Domain: {domain}.")
    if persona:
        extra.append(f"Sponsor persona: {persona}")
    if extra:
        lines += ["", " ".join(extra)]
    return lines


def goals(spec: Spec) -> list[str]:
    overview = spec.sec("process overview")
    goal_items = bullets(find_h3(overview, "goals"))
    scope = find_h3(overview, "scope")
    in_scope = field(scope, "In scope")
    out_scope = field(scope, "Out of scope")
    success = spec.sec("success criteria")
    header, kpis = table(find_h3(success, "business kpis"))
    lines = ["**Goals**"]
    lines += [f"- {g}" for g in goal_items] or [f"- {NEEDS.format('measurable outcome')}"]
    if in_scope:
        lines.append(f"- In scope: {in_scope}")
    if header:
        idx = {h.lower(): i for i, h in enumerate(header)}
        for row in kpis:
            name = row[idx.get("kpi name", 1)] if len(row) > 1 else ""
            target = row[idx["target"]] if "target" in idx and idx["target"] < len(row) else ""
            br = row[idx["br"]] if "br" in idx else ""
            lines.append(f"- KPI {name}: target {target} ({br})")
    lines += ["", "**Non-Goals**"]
    lines.append(f"- {out_scope}" if out_scope else f"- {NEEDS.format('explicitly out of scope')}")
    return lines


def users(spec: Spec) -> list[str]:
    participants = _participants(spec.sec("process overview"))
    humans = [r for r in participants if len(r) > 1 and r[1].lower() == "human"]
    primary = humans[0][0] if humans else NEEDS.format("primary persona")
    header, scenarios = table(find_h3(spec.sec("success criteria"), "evaluation scenarios"))
    lines = ["| Persona | Scenario | Success Criteria |", "| --- | --- | --- |"]
    if header:
        idx = {h.lower(): i for i, h in enumerate(header)}
        for row in scenarios:
            get = lambda key: row[idx[key]] if key in idx and idx[key] < len(row) else ""
            scenario = f"{get('id')} {get('scenario')} — input: {get('input')}".strip()
            rules = get("business rules")
            outcome = get("expected output") + (f" ({rules})" if rules and rules != "—" else "")
            lines.append(f"| {cell(primary)} | {cell(scenario)} | {cell(outcome)} |")
    elif humans:
        for row in humans:
            lines.append(f"| {cell(row[0])} | {cell(row[2] if len(row) > 2 else '')} | {NEEDS.format('observable outcome')} |")
    else:
        lines.append(f"| {primary} | {NEEDS.format('scenario')} | {NEEDS.format('observable outcome')} |")
    if participants:
        lines += ["", "Participants (humans, agents and systems):", ""]
        for row in participants:
            kind = row[1] if len(row) > 1 else ""
            desc = row[2] if len(row) > 2 else ""
            lines.append(f"- **{row[0]}** ({kind}): {desc}")
    return lines


def requirements(spec: Spec) -> list[str]:
    lines = [
        "Business rules (`BR-xxx`) are carried as testable requirements; the source",
        "rule ID stays in each row so evaluations can trace back to it.",
        "",
        "| ID | Requirement | Priority |",
        "| --- | --- | --- |",
    ]
    n = 0
    for heading, body in split_sections(spec.sec("business rules"), 3):
        n += 1
        action = field(body, "Action") or NEEDS.format("action")
        condition = field(body, "Condition") or NEEDS.format("condition")
        exception = field(body, "Exception")
        text = f"{heading}. The system MUST apply this rule. When: {condition.rstrip('.')}. Then: {action.rstrip('.')}."
        if exception and exception.lower().rstrip(".") != "none":
            text += f" Exception: {exception}"
        lines.append(f"| FR-{n:03d} | {cell(text)} | Must |")
    for heading, body in _gates(spec):
        n += 1
        trigger = field(body, "Trigger") or NEEDS.format("trigger")
        actor = (field(body, "Actor") or NEEDS.format("human actor")).rstrip(".")
        gate = field(body, "Action gate")
        linked = field(body, "Linked business rules")
        text = (
            f"{heading}. The system MUST route the case to a human ({actor}). When: {trigger.rstrip('.')}"
            + (f"; gate: {gate.rstrip('.')}" if gate else "")
            + (f" ({linked.rstrip('.')})" if linked else "")
            + "."
        )
        lines.append(f"| FR-{n:03d} | {cell(text)} | Must |")
    if n == 0:
        lines.append(f"| FR-001 | {NEEDS.format('no business rules found in the SPEC')} | Must |")
    return lines


def nfrs(spec: Spec) -> list[str]:
    success = spec.sec("success criteria")
    security = spec.sec("security")
    trigger = spec.sec("trigger")
    readiness = spec.sec("production readiness")

    def perf_text(name: str) -> str:
        return " ".join(line.strip("- ").strip() for line in find_h3(success, name).strip().splitlines() if line.strip())

    rto = field(readiness, "rto")
    rpo = field(readiness, "rpo")
    sla = field(readiness, "sla")
    availability = ", ".join(p for p in (f"SLA {sla}" if sla else "", f"RTO {rto}" if rto else "", f"RPO {rpo}" if rpo else "") if p)
    if not availability:
        latency = field_any(trigger, "Latency/SLA", "SLA", "Primary SLA")
        availability = f"Response SLA {latency}; availability target not stated" if latency else ""
    privacy = "; ".join(
        f"{label}: {value.rstrip('.')}"
        for label, value in (
            ("PII", field_any(security, "PII involved", "PII")),
            ("Residency", field_any(security, "Data residency", "Residency", "Tenant boundary")),
            ("Retention", field_any(security, "Data retention", "Retention")),
            ("Regulatory", field_any(security, "Regulatory", "Regulatory / policy context", "Regulatory posture")),
        )
        if value
    )
    trace = re.search(r"Trace fields the agent must emit:\s*(.+?)(?:\n\n|\Z)", success, flags=re.S)
    observability = "OpenTelemetry traces" + (
        f"; emit {' '.join(line.strip('> ').strip() for line in trace.group(1).splitlines())}" if trace else ""
    )
    scalability = "; ".join(
        p.rstrip(".")
        for p in (
            field_any(trigger, "Expected volume", "Typical volume", "Narrative volume"),
            field(trigger, "Scale target"),
            field_any(trigger, "Concurrency", "Concurrency posture"),
        )
        if p
    )
    security_text = "; ".join(
        p.rstrip(".") for p in (field_any(security, "Auth model", "Auth", "Identity"), field(security, "Access control")) if p
    )
    rows = [
        ("Performance", perf_text("performance")),
        ("Quality", perf_text("quality")),
        ("Availability", availability),
        ("Security", security_text),
        ("Privacy & Compliance", privacy),
        ("Audit", field_any(security, "Audit requirements", "Audit")),
        ("Responsible AI", field_any(security, "Responsible AI posture", "Responsible AI", "Human control", "Human decision boundary")),
        ("Scalability", scalability),
        ("Observability", observability),
    ]
    lines = ["| Category | Requirement |", "| --- | --- |"]
    for label, value in rows:
        lines.append(f"| {label} | {cell(value) if value else NEEDS.format(label.lower())} |")
    return lines


def _gates(spec: Spec) -> list[tuple[str, str]]:
    return [
        (heading, body)
        for heading, body in split_sections(spec.sec("human interaction points"), 3)
        if field(body, "Trigger") or field(body, "Actor")
    ]


def _tools(spec: Spec) -> list[tuple[str, str]]:
    return split_sections(spec.sec("tool contracts"), 3)


def _node(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", name)


def architecture(spec: Spec) -> list[str]:
    workflow = yaml_value(spec.sec("workflow model"), "workflow_model")
    steps = [h for h, _ in split_sections(spec.sec("process flow"), 4)]
    participants = _participants(spec.sec("process overview"))
    humans = [r[0] for r in participants if len(r) > 1 and r[1].lower() == "human"]
    agents = [r[0] for r in participants if len(r) > 1 and r[1].lower() == "agent"]
    agent = agents[0] if agents else f"{spec.title} agent"
    lines = []
    if workflow:
        lines.append(f"Workflow model: `{workflow}` (Threadlight § 11e).")
    if steps:
        lines.append("Process steps: " + "; ".join(re.sub(r"^Step \d+:\s*", "", s) for s in steps) + ".")
    lines += ["", "```mermaid", "flowchart LR"]
    user = humans[0] if humans else "User"
    lines.append(f'  U["{user}"] --> A["{agent}"]')
    for heading, body in _tools(spec):
        backed = field(body, "Backed by")
        for tool in (t.strip() for t in heading.split("/")):
            tid = "T_" + _node(tool)
            lines.append(f'  A --> {tid}["{tool}"]')
            if backed:
                system = re.sub(r"\s*\(.*?\)", "", backed).split(",")[0].strip()
                lines.append(f'  {tid} --> S_{_node(system)}[("{system}")]')
    for heading, _ in split_sections(spec.sec("knowledge sources"), 3):
        lines.append(f'  A --> K_{_node(heading)}[("{heading}")]')
    for heading, body in _gates(spec):
        actor = (field(body, "Actor") or "Human reviewer").rstrip(".")
        lines.append(f'  A -. "{heading}" .-> H_{_node(actor)}["{actor}"]')
    lines.append("```")
    return lines


def tech_stack(spec: Spec) -> list[str]:
    workflow = yaml_value(spec.sec("workflow model"), "workflow_model") or NEEDS.format("workflow model")
    lines = [
        "| Layer | Choice | Rationale |",
        "| --- | --- | --- |",
        f"| Agent shape | `{workflow}` | Threadlight § 11e workflow model |",
        "| Agent framework | Agentic Loop policy default | Applied by the Agentic Loop policy layer after this spec |",
        "| IaC | `azd` + Bicep | Agentic Loop / Spec2Cloud default |",
    ]
    header, rows = table(spec.sec("tech stack"))
    if header:
        for row in rows:
            if len(row) >= 3 and row[1].lower().startswith("yes"):
                lines.append(f"| Module `{cell(row[0].strip('`'))}` | selected | {cell(row[2])} |")
    return lines


def services(spec: Spec) -> list[str]:
    lines = ["| Service | Purpose | SKU/Tier | Notes |", "| --- | --- | --- | --- |"]
    lines.append("| Microsoft Foundry | Models, hosted agent, evaluations | per plan | Managed identity |")
    header, rows = table(spec.sec("tech stack"))
    for row in rows if header else []:
        module = row[0].strip("`") if row else ""
        if len(row) >= 3 and row[1].lower().startswith("yes"):
            name = SERVICE_NAMES.get(module, module)
            lines.append(f"| {cell(name)} | {cell(row[2])} | per plan | Threadlight module `{module}` |")
    hub = find_h3(spec.sec("security"), "governance hub") or spec.sec("governance hub")
    if yaml_value(hub, "required").lower().startswith(("yes", "true")):
        block = hub.split("access_contracts:", 1)[-1].split("```", 1)[0]
        contracts = ", ".join(re.findall(r"^\s+-\s*([A-Za-z0-9_.-]+)\s*$", block, flags=re.M))
        if not contracts and yaml_value(hub, "access_contract"):
            contracts = "as declared in Threadlight § 11b"
        lines.append(
            f"| Existing AI governance hub (APIM gateway) | Model traffic governance | existing | "
            f"Consume access contract(s) {cell(contracts) or NEEDS.format('access contract')}; do not create a gateway |"
        )
    if not header:
        lines.append(f"| {NEEDS.format('services')} | | | |")
    return lines


def ai_foundry(spec: Spec) -> list[str]:
    lines = ["| Item | Choice | Rationale |", "| --- | --- | --- |"]
    models = [
        (h, b) for h, b in split_sections(spec.sec("ai services"), 3)
        if field_any(b, "Model + version", "Model", "Capability type")
    ]
    for heading, body in models:
        model = field(body, "Model + version") or " ".join(
            p for p in (field(body, "Model"), field(body, "Version")) if p
        ) or NEEDS.format("model")
        rationale = "; ".join(
            p for p in (field(body, "Region"), field(body, "Capacity (TPM)"), field(body, "Reasoning effort")) if p
        )
        lines.append(f"| Foundry model — {cell(heading)} | {cell(model)} | {cell(rationale)} |")
    header, rows = table(spec.sec("ai services"))
    if not models and header:
        idx = {h.lower(): i for i, h in enumerate(header)}
        col = next((i for h, i in idx.items() if h.startswith("model")), None)
        for row in rows:
            value = row[col] if col is not None and col < len(row) else ""
            if value and value.lower() not in ("n/a", "managed service") and "not selected" not in " ".join(row).lower():
                models.append((row[0], ""))
                lines.append(f"| Foundry model — {cell(row[0])} | {cell(value)} | {cell(' '.join(row[1:2]))} |")
    if not models:
        lines.append(f"| Foundry model(s) | {NEEDS.format('model and region')} | |")
    participants = _participants(spec.sec("process overview"))
    agents = [r[0] for r in participants if len(r) > 1 and r[1].lower() == "agent"]
    lines.append(
        f"| Hosted agent(s) | {cell(', '.join(agents) or spec.title)} | Protocol and framework per Agentic Loop policy |"
    )
    for heading, body in split_sections(spec.sec("knowledge sources"), 3):
        backing = field(body, "Backing service")
        citation = field(body, "Citation requirement")
        lines.append(
            f"| Knowledge — {cell(heading)} | "
            f"{cell(backing.replace('`', '').replace('foundry-iq', 'Foundry IQ') or NEEDS.format('backing service'))} | "
            f"Citations: {cell(citation) or 'n/a'} |"
        )
    header, scenarios = table(find_h3(spec.sec("success criteria"), "evaluation scenarios"))
    count = len(scenarios) if header else 0
    lines.append(
        f"| Evaluation | {count} scenarios in § 3 plus KPIs in § 2 | Keep every business rule covered |"
        if count
        else f"| Evaluation | {NEEDS.format('evaluation scenarios')} | |"
    )
    rai = field_any(spec.sec("security"), "Responsible AI posture", "Responsible AI", "Human control", "Human decision boundary")
    lines.append(f"| Guardrails | Content safety plus the human gates in § 4 | {cell(rai)} |")
    return lines


def data_model(spec: Spec) -> list[str]:
    body = spec.sec("data models").strip()
    lines = [body] if body else [NEEDS.format("key entities, fields and owners")]
    retention = field_any(spec.sec("security"), "Data retention", "Retention")
    if retention:
        lines += ["", f"Retention: {retention}"]
    return lines


def interfaces(spec: Spec) -> list[str]:
    lines = ["- **Agent tools** — each tool is a governed interface of the agent.", ""]
    tools = _tools(spec)
    if tools:
        lines += ["| Tool | Purpose | Inputs | Side effects | Backed by |", "| --- | --- | --- | --- | --- |"]
        for heading, body in tools:
            side = field(body, "Side Effects") or "none declared"
            lines.append(
                f"| `{cell(heading)}` | {cell(field(body, 'Description'))} | {cell(field(body, 'Inputs'))} | "
                f"{cell(side)} | {cell(field(body, 'Backed by'))} |"
            )
    else:
        lines.append(f"  {NEEDS.format('tool contracts')}")
    lines += ["", "- **External integrations**"]
    integrations = split_sections(spec.sec("system integrations"), 3)
    for heading, body in integrations:
        parts = [p for p in (field(body, "Direction"), field(body, "Auth"), field(body, "Availability")) if p]
        lines.append(f"  - {heading}: " + "; ".join(parts))
    if not integrations:
        lines.append(f"  - {NEEDS.format('systems of record and their auth')}")
    trigger = spec.sec("trigger")
    receiver = find_h3(trigger, "triggers") or spec.sec("receiver contract") or trigger
    events = [
        f"{label}: {value}"
        for label, value in (
            ("Trigger", field_any(trigger, "Trigger", "Execution pattern", "Run model")),
            ("Schedule", field(trigger, "Schedule")),
            ("Event source", field(trigger, "Event source")),
            ("Receiver", field(receiver, "Receiver type")),
            ("Idempotency key", field(receiver, "Idempotency key")),
            ("Dedup window", field(receiver, "Dedup window")),
            ("Dead-letter", field(receiver, "Dead-letter rule")),
        )
        if value
    ]
    lines += ["", "- **Events and triggers**"]
    lines += [f"  - {e}" for e in events] or [f"  - {NEEDS.format('trigger and run model')}"]
    gates = _gates(spec)
    if gates:
        lines += ["", "- **Human review channels**"]
        for heading, body in gates:
            lines.append(f"  - {heading}: {field(body, 'Channel') or NEEDS.format('channel')}")
    return lines


def open_questions(spec: Spec) -> list[str]:
    section = spec.sec("assumptions")
    questions = bullets(find_h3(section, "open questions")) or yaml_list(section, "open_questions")
    assumptions = [
        a for h, b in split_sections(section, 3) if title_key(h).endswith("assumptions") for a in bullets(b)
    ] or yaml_list(section, "assumptions")
    lines = [
        "Mark unknowns inline as `[NEEDS CLARIFICATION: <question>]`; track resolution here.",
        "",
        "| # | Question | Owner | Status |",
        "| --- | --- | --- | --- |",
    ]
    n = 0
    for q in questions:
        n += 1
        lines.append(f"| {n} | {cell(q)} | | open |")
    for a in assumptions:
        n += 1
        lines.append(f"| {n} | Confirm assumption: {cell(a)} | | assumed |")
    if n == 0:
        lines.append(f"| 1 | {NEEDS.format('open questions')} | | open |")
    lines += [
        "",
        "> Identity, secrets, and deployment targets live in `.azure/deployment-plan.md`,",
        "> produced by the Agentic Loop plan stage.",
    ]
    return lines


def provenance(spec: Spec) -> list[str]:
    lines = [
        "This file is an export. The Threadlight `specs/SPEC.md` stays the source of truth",
        "for governance, production readiness and value evidence. Re-export after changing",
        "the SPEC instead of editing both. The export does not run Agentic Loop, prove its",
        "policy layer accepted the spec, or deploy anything.",
        "",
        "| Threadlight section | Exported to | Keep using |",
        "| --- | --- | --- |",
    ]
    for heading, _ in spec.h2:
        key = title_key(heading)
        target, owner = None, None
        for keyword, dest, skill in PROVENANCE:
            if keyword in key:
                target, owner = dest, skill
                break
        where = target or "not carried"
        keep = f"`{owner}`" if owner else "—"
        lines.append(f"| § {cell(heading)} | {where} | {keep} |")
    return lines


def export(text: str) -> str:
    spec = Spec(text)
    out: list[str] = [
        f"# {spec.title} — Specification",
        "",
        f"> **Last updated:** {spec.generated}",
        "> Exported from a Threadlight `specs/SPEC.md` by `export_agentic_loop_spec.py`.",
        "> Section references (§) inside carried text point to that Threadlight SPEC.",
        "",
    ]
    sections = [
        ("1. Summary", summary),
        ("2. Goals & Non-Goals", goals),
        ("3. Users & Scenarios", users),
        ("4. Functional Requirements", requirements),
        ("5. Non-Functional Requirements", nfrs),
        ("6. Architecture Overview", architecture),
        ("7. Tech Stack", tech_stack),
        ("8. Azure Services", services),
        ("9. AI / Foundry", ai_foundry),
        ("10. Data Model", data_model),
        ("11. Interfaces", interfaces),
        ("12. Open Questions", open_questions),
        ("Appendix — Threadlight provenance", provenance),
    ]
    for heading, builder in sections:
        out += [f"## {heading}", ""] + builder(spec) + [""]
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("spec", type=Path, help="Threadlight specs/SPEC.md")
    parser.add_argument("-o", "--output", type=Path, help="write here (e.g. docs/spec.md); default stdout")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output file")
    args = parser.parse_args(argv)
    if not args.spec.is_file():
        parser.error(f"SPEC not found: {args.spec}")
    result = export(args.spec.read_text(encoding="utf-8"))
    if args.output is None:
        sys.stdout.write(result)
        return 0
    if args.output.exists() and not args.force:
        print(f"refusing to overwrite {args.output}; pass --force", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
