# Threadlight documentation

Threadlight is a library of skills that GitHub Copilot follows to turn a
business process into a working agent pilot on Microsoft Foundry, then carry it
toward production with selected runtime controls and evidence. Start with the
[visual site](https://aiappsgbb.github.io/threadlight-skills/) for the overview
(L200/L300); the documents below go deeper. Unfamiliar terms are defined in the
[glossary](https://aiappsgbb.github.io/threadlight-skills/glossary.html).

Threadlight is an open-source pattern from Microsoft GBB · AI Apps, not an
official Microsoft product or support offering.

## Which page should I read?

| You are… | Start with | Then |
|---|---|---|
| A business or customer stakeholder | [Basics](https://aiappsgbb.github.io/threadlight-skills/basics.html) and [Industries](https://aiappsgbb.github.io/threadlight-skills/industries.html) | [Case study](https://aiappsgbb.github.io/threadlight-skills/case-study.html) for a captured run and its limits |
| A solution engineer running a pilot | [Workbook](https://aiappsgbb.github.io/threadlight-skills/workbook.html) or the [1-hour workshop](WORKSHOP-1H-QUICKSTART.md) | [Idea-to-production workbook](IDEA-TO-PRODUCTION-WORKBOOK.md) |
| Taking an existing pilot to production | [Production](https://aiappsgbb.github.io/threadlight-skills/production.html) | [Production governance companion](production-governance.md), then the [release guide](https://aiappsgbb.github.io/threadlight-skills/agent-governance.html) |
| Platform, security or operations | [Governance & AgentOps](https://aiappsgbb.github.io/threadlight-skills/governance.html) | [Agent operations](agent-operations.md) and the [governance deep dive](agent-governance-deep-dive.md) |
| An engineer extending the catalog | [Skill-based agents](skill-based-agents.md) | [`THREADLIGHT.md`](../THREADLIGHT.md) and each skill's `SKILL.md` |

## Guides

| Document | What it covers |
|---|---|
| [first-governed-workflow.md](first-governed-workflow.md) | One returns decision in six phases, from local rehearsal to a controlled release |
| [WORKSHOP-1H-QUICKSTART.md](WORKSHOP-1H-QUICKSTART.md) | A one-hour hands-on workshop, prompt by prompt |
| [IDEA-TO-PRODUCTION-WORKBOOK.md](IDEA-TO-PRODUCTION-WORKBOOK.md) | The full credit-memo walkthrough from brief to production evidence |
| [presenter-ready.md](presenter-ready.md) | The opt-in contract for a repeatable demonstration |
| [AGENTIC-LOOP-BRIDGE.md](AGENTIC-LOOP-BRIDGE.md) | Handing a Threadlight design to the GBB Agentic Loop |
| [KRATOS-BRIDGE.md](KRATOS-BRIDGE.md) | Hardening a Kratos export with Threadlight |

## Production and governance reference

| Document | What it covers |
|---|---|
| [production-governance.md](production-governance.md) | Technical companion to the Production page: foundation, release, action authority, owners |
| [production-readiness.md](production-readiness.md) | The readiness skill: postures, the thirteen pillars, status taxonomy and CI inputs |
| [agentops-deep-dive.md](agentops-deep-dive.md) | From a working pilot to a controlled release: terms, responsibilities and sequence |
| [agent-operations.md](agent-operations.md) | Operative guide: configure, generate, validate, deploy, collect and rescore (L400/L500) |
| [agent-governance-deep-dive.md](agent-governance-deep-dive.md) | Runtime governance architecture and trust boundaries |
| [skill-based-agents.md](skill-based-agents.md) | How skills are built, loaded and checked at runtime |
| [runtime-support.md](runtime-support.md) | Runtimes, action enforcement and human review: what each profile supports |
| [citadel-governance.md](citadel-governance.md) | Optional Citadel hub path to a native enforcement point |
| [native-outlook-approval-architecture.md](native-outlook-approval-architecture.md) | Native human approval through Outlook |
| [signed-evidence.md](signed-evidence.md) | Opt-in signed evidence for selected MCP actions |
| [eu-ai-act-evidence.md](eu-ai-act-evidence.md) | An offline inventory mapping pilot artifacts to EU AI Act topics, for human review (not certification or legal compliance) |
| [reference-release.md](reference-release.md) | The offline-tested returns reference release composition (not live release acceptance) |

## Evidence records

Dated records of specific runs. They show what was observed then; they are not
current guidance and do not certify another deployment.

| Document | What it records |
|---|---|
| [governed-returns-validation.md](governed-returns-validation.md) | Hosted validation runs of the governed returns example |
| [ci/router-validation.md](ci/router-validation.md) | The model-router validation matrix |

## Maintainer notes

Kept in the repository for history; not published on the site.

| Document | What it is |
|---|---|
| [solution-review-2026-09-15.md](solution-review-2026-09-15.md) | A dated solution and adversarial review |
| [production-readiness-pages-spec.md](production-readiness-pages-spec.md) | The design specification behind the Pages narrative |
