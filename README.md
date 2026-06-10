# Threadlight — Pilot Pipeline Skills

> **Nine pipeline skills + one orchestrator (10 total)** that take a customer
> engagement from a one-paragraph brief through to a deployed, evaluated,
> observable, **production-ready** Microsoft Foundry hosted agent — runnable
> on the customer's tenant in a single working session, then handed off to
> the production track without ending up in lab graveyard.

| Skill | What it does |
|-------|-------------|
| [`threadlight-design`](skills/threadlight-design/) | Produces SPEC.md, demo deck, prep guide, experience page from a brief |
| [`threadlight-local-test`](skills/threadlight-local-test/) | Boots the agent locally for rapid iteration (Pattern 0 quickstart) |
| [`threadlight-deploy`](skills/threadlight-deploy/) | 7-phase `azd up` orchestration — ACR, Bicep, hooks, Foundry, Citadel |
| [`threadlight-safe-check`](skills/threadlight-safe-check/) | Pre/post-deploy gate — validates every resource selector before go-live |
| [`threadlight-demo-data-factory`](skills/threadlight-demo-data-factory/) | Generates industry-realistic seed data for demos |
| [`threadlight-event-triggers`](skills/threadlight-event-triggers/) | Wires ACA Jobs, Event Grid, and cron receivers into the deploy lifecycle |
| [`threadlight-hitl-patterns`](skills/threadlight-hitl-patterns/) | Human-in-the-loop gates via Teams Adaptive Cards + audit trail |
| [`threadlight-workspace-ui`](skills/threadlight-workspace-ui/) | Operator dashboard (React workspace) behind Easy Auth |
| [`threadlight-production-ready`](skills/threadlight-production-ready/) | **v0.3.0** — advisory production-readiness scorecard (BicepGraph parser, 13 pillars, Defender / Policy / quota / restore-drill checks, `--gate-preview`, `--diff`, `--remediate`, `--trend-csv`, OIDC CI). Hard dep on `bicep` CLI; no regex fallback. |
| [`threadlight-auto`](skills/threadlight-auto/) | **Orchestrator** — wraps the 9 pipeline skills behind one freeform prompt; resumes from `.threadlight/auto-state.json`; smart-recovers quota/RBAC/ImagePull failures |

## Pipeline flow

```
threadlight-design → threadlight-local-test → threadlight-deploy →
threadlight-safe-check (gate) → foundry-evals + foundry-observability →
threadlight-production-ready (advisory) → customer architecture review
```

The 9-stage pipeline above is the spine. `threadlight-auto` drives the same
chain end-to-end when you want one-prompt automation (demos, resumption,
template-from-scenario kickoffs).

The full technical briefing is in [`THREADLIGHT.md`](THREADLIGHT.md).

## Install

### As a plugin (recommended)

```bash
copilot plugin marketplace add aiappsgbb/threadlight-skills
copilot plugin install threadlight-skills@threadlight-skills
```

### Individual skills

```bash
gh skill install aiappsgbb/threadlight-skills threadlight-design
gh skill install aiappsgbb/threadlight-skills threadlight-deploy
# ... etc
```

### Companion skills (in awesome-gbb)

Threadlight skills cross-reference foundry-*, azd-patterns, citadel-*, and
other skills from [awesome-gbb](https://github.com/aiappsgbb/awesome-gbb).
Install both plugins for the full pipeline:

```bash
copilot plugin marketplace add aiappsgbb/awesome-gbb
copilot plugin install awesome-gbb@awesome-gbb

copilot plugin marketplace add aiappsgbb/threadlight-skills
copilot plugin install threadlight-skills@threadlight-skills
```

## Live experience

The [Threadlight experience page](https://aiappsgbb.github.io/threadlight-skills/)
showcases what the pipeline produces.

## License

[MIT](LICENSE)
