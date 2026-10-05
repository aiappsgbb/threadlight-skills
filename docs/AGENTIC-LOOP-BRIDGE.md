# Agentic Loop Bridge — hand a Threadlight design to the Agentic Loop build loop

> **One design, two loops, one-way export.** **Agentic Loop** (with Spec2Cloud)
> is the GBB inner build loop: Specify → Plan → Implement → Verify → Deploy, with
> Foundry defaults applied by its policy layer. **Threadlight** is the design and
> production path: a business-process SPEC with numbered rules, human gates,
> evaluation scenarios, governance and value evidence. When a team has already
> designed a process with Threadlight and wants the Agentic Loop to build it, it
> exports the SPEC once into the `docs/spec.md` shape the loop reads.

This page is additive. The `threadlight-design` from-scratch flow and the
[Kratos bridge](KRATOS-BRIDGE.md) are unchanged. Nothing here modifies Agentic
Loop or Spec2Cloud.

---

## 1. When to use which

| You have | Start with | Why |
|----------|------------|-----|
| An idea and a short time box | Agentic Loop `/spec2cloud` | Fastest path from a prompt to a deployed Foundry agent. |
| A detailed business process (rules, approvals, systems, KPIs) | `threadlight-design`, then this export | The SPEC keeps every rule traceable; the loop builds from it. |
| A running pilot that must reach production | Threadlight production skills | Evidence for evaluation, governance, cost and release gates. |

The two are complementary: the loop builds, Threadlight keeps the design and
production evidence honest. You can use both on the same pilot.

---

## 2. Export the SPEC

```bash
python3 skills/threadlight-design/scripts/export_agentic_loop_spec.py \
  specs/SPEC.md -o docs/spec.md
```

- Standard library only; no network, no Azure calls.
- Deterministic: the same SPEC always produces the same file.
- Refuses to overwrite an existing `docs/spec.md` unless you pass `--force`.
- Omit `-o` to print to stdout and review first.
- Run it from a checkout or plugin install: the exporter is not in the Cowork
  `threadlight-design.zip`, which has no shell.

Then continue in the Agentic Loop as usual: install its policy skill, run it on
`docs/spec.md`, and advance with `plan`, `implement`, `verify`, `deploy`. See the
Agentic Loop getting-started playbook for the exact commands in your version.

---

## 3. What the export carries

The output follows the twelve sections of the lean Spec2Cloud `specify`
template (checked against `Azure-Samples/Spec2Cloud` commit `6ea82279`).

| Agentic Loop section | Filled from the Threadlight SPEC |
|----------------------|----------------------------------|
| 1 Summary | § 1 description, domain, sponsor persona |
| 2 Goals & Non-Goals | § 1 goals and scope, § 9 business KPIs with targets |
| 3 Users & Scenarios | § 9 evaluation scenarios with expected outcomes; § 1 participants |
| 4 Functional Requirements | every `BR-xxx` rule as an `FR-xxx` row (rule ID kept), plus every § 8 human gate |
| 5 Non-Functional Requirements | § 9 performance/quality, § 11 security, privacy, audit, responsible AI, § 10 volume, § 12 RTO/RPO/SLA |
| 6 Architecture Overview | § 11e workflow model, § 2 steps, a Mermaid diagram of agent, tools, systems, knowledge and reviewers |
| 7 Tech Stack | § 11e and the selected § 11c modules |
| 8 Azure Services | selected § 11c modules; an existing governance hub from § 11b, consumed rather than created |
| 9 AI / Foundry | § 7b models, § 7 knowledge sources, scenario count, guardrails |
| 10 Data Model | § 4 tables verbatim, § 11 retention |
| 11 Interfaces | § 6 tool contracts with side effects, § 5 integrations, § 10 triggers, § 8 review channels |
| 12 Open Questions | § 13 open questions, plus each assumption as "confirm" |

Anything the SPEC does not state becomes `[NEEDS CLARIFICATION: ...]`, the
Spec2Cloud convention for unknowns. The exporter never invents a value.

Framework, hosting protocol and infrastructure choices are left to the Agentic
Loop policy layer, which applies its own defaults after the spec is written.

## 4. What stays in Threadlight

The file ends with a provenance table listing every source section and where it
went. Some sections are deliberately **not carried**, because they are not build
inputs and have their own owners:

| Not carried | Keep using |
|-------------|------------|
| Mock/MCP contract (§ 5b) | `threadlight-mcp-aca` |
| Workspace UX (§ 8b) | `threadlight-workspace-ui` |
| Demo data (§ 11d) | `threadlight-demo-data-factory` |
| Deployment posture (§ 11f) | `threadlight-deploy` |
| Value model (§ 14) | `threadlight-consumption-iq` |

The Threadlight `specs/SPEC.md` stays the source of truth. After changing it,
re-export instead of editing both files.

---

## 5. Worked example

[`returns-triage.spec.md`](../skills/threadlight-design/references/agentic-loop-export/returns-triage.spec.md)
is the export of the public
[returns-triage example SPEC](../examples/returns-triage-governed/specs/SPEC.md):
five business rules and two human gates become seven requirements, nine
evaluation scenarios become the scenario table, and the one write tool keeps its
conditional-write side effects in the interface table. A test regenerates it and
fails if the committed file drifts.

---

## 6. What this does not prove

- The export is checked against the template's section list, not by running the
  Agentic Loop policy layer or `/spec2cloud` end to end.
- It does not deploy, provision or evaluate anything.
- Governance controls in the SPEC (approvals, signed policy, audit) are carried
  as requirements. The loop's generated code does not inherit Threadlight's
  governed runtime; verify those controls with the Threadlight production path
  before calling the pilot production-ready.
