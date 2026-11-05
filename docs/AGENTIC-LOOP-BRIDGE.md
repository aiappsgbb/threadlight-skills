# Agentic Loop Bridge — hand a Threadlight design to the Agentic Loop build loop

> **One design, two loops, one-way export.** The **Foundry Agentic Loop** is the
> GBB build loop, published as the
> [`foundry-agentic-loop`](https://github.com/github/awesome-copilot/tree/main/plugins/foundry-agentic-loop)
> plugin in `github/awesome-copilot`. Its spec-driven development (SDD) mode runs
> **Specify → Implement → Verify → Deploy** from a brief, behind evidence gates.
> **Threadlight** is the design and production path: a business-process SPEC with
> numbered rules, human gates, evaluation scenarios, governance and value
> evidence. When a team has already designed a process with Threadlight and wants
> the Agentic Loop to build it, it exports the SPEC once as that brief.

This page is additive. The `threadlight-design` from-scratch flow and the
[Kratos bridge](KRATOS-BRIDGE.md) are unchanged. Nothing here modifies the
Agentic Loop.

---

## 1. When to use which

| You have | Start with | Why |
|----------|------------|-----|
| An idea and a short time box | Agentic Loop, rapid prototyping or SDD | Fastest path from a prompt to a deployed Foundry agent. |
| A detailed business process (rules, approvals, systems, KPIs) | `threadlight-design`, then this export | The SPEC keeps every rule traceable; the loop builds from it. |
| A running pilot that must reach production | Threadlight production skills | Evidence for evaluation, governance, cost and release gates. |

The two are complementary: the loop builds, Threadlight keeps the design and
production evidence honest. You can use both on the same pilot.

---

## 2. Export the brief

```bash
python3 skills/threadlight-design/scripts/export_agentic_loop_spec.py \
  specs/SPEC.md -o docs/threadlight-brief.md
```

- Standard library only; no network, no Azure calls.
- Deterministic: the same SPEC always produces the same file.
- Refuses to overwrite an existing output file unless you pass `--force`.
- Omit `-o` to print to stdout and review first.
- Run it from a checkout or plugin install: the exporter is not in the Cowork
  `threadlight-design.zip`, which has no shell.

Then build it with the published plugin:

```bash
copilot plugin install foundry-agentic-loop@awesome-copilot
```

In the GitHub Copilot App, ask Copilot to **open the agentic loop**, choose
**SDD** and start a build with the whole of `docs/threadlight-brief.md` as the
brief. The SDD workflow takes a self-contained `brief`; its Specify phase writes
`docs/spec.md` (EARS acceptance criteria, a frozen implementation contract,
workstreams and a verification plan) and `.azure/deployment-plan.md`, then pauses
for your approval of the spec and again before `azd up`. See the plugin README
for the exact commands in your version.

**Legacy five-stage workspaces.** An existing `/spec2cloud` workspace that still
reads the lean Spec2Cloud `docs/spec.md` can keep using
`--target spec2cloud -o docs/spec.md`. That output is unchanged and follows the
twelve sections of the lean `specify` template (checked against
`Azure-Samples/Spec2Cloud` commit `6ea82279`); its worked example is
[`returns-triage.spec.md`](../skills/threadlight-design/references/agentic-loop-export/returns-triage.spec.md).

---

## 3. What the brief carries

The brief maps onto the sections of the plugin's SDD spec template (checked
against `github/awesome-copilot` commit `60988c0d`, plugin version 1.0.0), so
Specify can fill each section from it.

| Brief section | Filled from the Threadlight SPEC | Feeds SDD spec section |
|---------------|----------------------------------|------------------------|
| Solution | § 1 description, domain, sponsor persona | 1 Summary |
| Goals and non-goals | § 1 goals and scope, § 9 business KPIs with targets | 2 Goals and non-goals |
| Users and scenarios | § 9 evaluation scenarios with expected outcomes; § 1 participants | 3 Users and scenarios, 12 Verification plan |
| Functional requirements and human gates | every `BR-xxx` rule (ID kept), plus every § 8 human gate | 4 Functional requirements (written as EARS by Specify) |
| Non-functional requirements | § 9 performance/quality, § 11 security, privacy, audit, responsible AI, § 10 volume, § 12 RTO/RPO/SLA | 5 Non-functional requirements |
| Process flow | § 11e workflow model, § 2 steps, a Mermaid diagram of agent, tools, systems, knowledge and reviewers | 6 Architecture |
| Azure services selected by the design | selected § 11c modules; an existing governance hub from § 11b, consumed rather than created | 8 Components and Azure services |
| Agents, models and grounding | § 7b models, § 7 knowledge sources, scenario count, guardrails | 7 Agents |
| Data and seed facts | § 4 tables verbatim, § 11 retention | 9 Data |
| Tools, integrations and triggers | § 6 tool contracts with side effects, § 5 integrations, § 10 triggers, § 8 review channels | 10 Implementation contract |
| Decisions already taken | Foundry hosting, workflow model and selected modules | Assumptions Specify must not reopen |
| Left to the Agentic Loop | framework, EARS, contract, workstreams, placement, RBAC, deployment | Specify and later phases |
| Assumptions and open questions | § 13 open questions, plus each assumption as "confirm" | 13 Assumptions and open questions |

Anything the SPEC does not state becomes `[NEEDS CLARIFICATION: ...]`; Specify
resolves it or records it as an assumption. The exporter never invents a value.
Framework, protocol and infrastructure choices are left to the loop's SDD
defaults.

## 4. What stays in Threadlight

Both outputs end with a provenance table listing every source section and where it
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

[`returns-triage.brief.md`](../skills/threadlight-design/references/agentic-loop-export/returns-triage.brief.md)
is the SDD brief exported from the public
[returns-triage example SPEC](../examples/returns-triage-governed/specs/SPEC.md):
five business rules and two human gates become seven requirements, nine
evaluation scenarios become the scenario table, and the one write tool keeps its
conditional-write side effects in the tools table.
[`returns-triage.spec.md`](../skills/threadlight-design/references/agentic-loop-export/returns-triage.spec.md)
is the legacy `spec2cloud` export of the same SPEC. Tests regenerate both and
fail if a committed file drifts.

---

## 6. What this does not prove

- The export is checked against the template sections, not by running the Agentic
  Loop SDD workflow, its Specify review or `/spec2cloud` end to end.
- It does not deploy, provision or evaluate anything.
- Governance controls in the SPEC (approvals, signed policy, audit) are carried
  as requirements. The loop's generated code does not inherit Threadlight's
  governed runtime; verify those controls with the Threadlight production path
  before calling the pilot production-ready.
