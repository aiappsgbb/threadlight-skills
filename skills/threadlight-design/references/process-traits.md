# Process Traits

Use these as **composable building blocks**, not a closed list. Real processes combine
multiple traits. Detect traits from discovery answers and compose dynamically.

## How to Use

1. During discovery, identify which traits apply to the use case
2. Combine traits to form the process profile
3. Use trait-specific branching questions to deepen discovery
4. Map traits to typical tool shapes and skill patterns

A single process usually combines 2-4 traits.

---

## Trait: Data Sourcing

How the process acquires its input data.

| Variant | Description | Typical Tools | Discovery Questions |
|---------|-------------|---------------|---------------------|
| **Web scraping** | Extract data from websites | Browser Automation, Playwright MCP | What sites? Auth needed? Geo-restricted? Languages? |
| **API integration** | Call external APIs | OpenAPI, MCP, Function Calling | Which APIs? OpenAPI specs available? Rate limits? |
| **Document intake** | Process uploaded/stored documents | File Search, SharePoint | What formats (PDF, DOCX, images)? What to extract? Volume? |
| **Database query** | Read from corporate databases | MCP, custom tools | Which DB (SQL, Cosmos, SAP)? Read-only or read-write? Access available or mock? |
| **Search / research** | Find information across sources | Bing Search, AI Search, Web Search | What topics? How broad? Freshness requirements? |
| **User input** | Conversational data gathering | Agent chat | What info from user? Structured or freeform? Validation needed? |
| **Event-driven** | React to webhooks, messages, signals | Azure Functions, Event Grid | What triggers? Frequency? Payload format? |

---

## Trait: Processing Style

What the process does with the data.

| Variant | Description | Typical Skills | Discovery Questions |
|---------|-------------|----------------|---------------------|
| **Extraction** | Pull specific fields/entities from unstructured data | `extract-*` skills | What fields? What accuracy? |
| **Transformation** | Convert, normalize, enrich data | `transform-*` skills | Source → target format? Rules? |
| **Comparison** | Diff, rank, score across items | `compare-*`, `rank-*` skills | What dimensions? Weighting? |
| **Analysis** | Compute metrics, detect patterns, assess risk | `analyze-*`, `assess-*` skills | What KPIs? Thresholds? Business rules? |
| **Synthesis** | Combine multiple sources into new content | `synthesize`, `summarize` skills | What output format? Depth? Citation needs? Multiple skills composing one answer → require a **cross-skill reconciliation** clause (preserve detail, resolve disagreement, surface trade-offs) in AGENTS.md Behavioral guidelines. |
| **Validation** | Check data against rules, policies, schemas | `validate-*`, `check-*` skills | What rules? What happens on failure? |
| **Routing** | Direct items to the right handler based on criteria | `route-*`, `triage-*` skills | What criteria? How many routes? Fallback? |

---

## Trait: Output Mode

What the process produces.

| Variant | Description | Typical Delivery | Discovery Questions |
|---------|-------------|-----------------|---------------------|
| **Report** | Structured document (Markdown, PPTX, PDF) | File output, email, SharePoint | Format? Cadence? Audience? |
| **Structured data** | JSON, CSV, database records | API, storage, downstream system | Schema? Destination? Append or replace? |
| **Notification** | Alert, message, summary | Teams, email, webhook | Who? What threshold triggers it? Channel? |
| **Decision** | Approval, recommendation, score | Agent response, human approval flow | Who decides? What data supports the decision? SLA? |
| **Conversation** | Interactive response to user | Chat, Teams, portal | Tone? Domain constraints? Escalation path? |
| **Action** | Side effect in an external system | API call, DB write, ticket creation | Which system? Idempotent? Retry policy? |

---

## Trait: Interaction Model

How humans participate in the process.

| Variant | Description | Discovery Questions |
|---------|-------------|---------------------|
| **Fully automated** | No human in the loop | What triggers? What monitoring/alerting if it fails? |
| **Human-in-the-loop** | Human approves/reviews at key points | Who approves? What do they see? SLA? Escalation? |
| **Conversational** | Agent and human collaborate in real-time | What channel (Teams, web, API)? Session length? Memory needs? |
| **Supervised** | Agent proposes, human confirms before execution | What's the blast radius? Undo possible? |
| **Periodic review** | Agent runs autonomously, human reviews output periodically | Cadence? What's reviewed? How to flag issues? |

---

## Trait: Temporal Pattern

When and how often the process runs.

| Variant | Description | Discovery Questions |
|---------|-------------|---------------------|
| **On-demand** | User triggers explicitly | What's the expected usage pattern? |
| **Scheduled** | Runs on a timer (hourly, daily, weekly) | Exact schedule? Time zone? Dependencies? |
| **Event-driven** | Triggered by external event | What event? Source? Latency requirements? |
| **Continuous** | Always running, monitoring a stream | Volume? Backpressure strategy? |
| **Batch** | Processes a queue of items periodically | Batch size? Ordering? Failure handling per item? |

---

## Trait: State Model

How the process manages state across steps and over time.

| Variant | Description | Discovery Questions |
|---------|-------------|---------------------|
| **Stateless** | Each invocation is independent, no memory | Any caching useful? |
| **Session-based** | State within a conversation/session, discarded after | Session timeout? What's stored? |
| **Case-based** | Long-lived case/ticket with lifecycle (open → in-progress → resolved) | Case lifecycle stages? Who can update? SLA per stage? |
| **Pipeline** | Items flow through ordered stages with checkpointing | What happens if a stage fails? Retry? Skip? Queue? |

---

## Trait: Action Criticality

How consequential the process's actions are — drives approval and safety patterns.

| Variant | Description | Discovery Questions |
|---------|-------------|---------------------|
| **Read-only** | Only reads/analyzes data, no side effects | N/A — lowest risk |
| **Reversible write** | Creates/updates data that can be undone | Undo mechanism? Soft delete? |
| **Irreversible write** | Financial transactions, notifications, external system writes | Human approval needed? Confirmation step? Audit trail? |
| **Mixed** | Some actions are safe, some are critical | Which actions need approval? What's the blast radius? |

---

## Trait: Agent Topology (one agent with skills, or several agents?)

Design-time decision recorded in SPEC § 11e `agent_topology`. A Threadlight pilot is
**one hosted agent with skills** by default. Split only when a hard trigger below
holds, and record the reason so the customer can review it.

Last checked against Microsoft Learn on 2026-11-11. Re-check the Foundry facts
before quoting them to a customer: these features move quickly.

### Microsoft guidance this follows

The Cloud Adoption Framework article
[Single agent or multiple agents](https://learn.microsoft.com/azure/cloud-adoption-framework/ai-agents/single-agent-multiple-agents)
gives the decision framework Threadlight applies:

- **Start with multiple agents** only when the use case crosses security and
  compliance boundaries, multiple teams own separate knowledge areas, or planned
  growth spans many distinct functions (the article names "more than three to five").
- **Otherwise test a single agent first.** The article warns that distinct roles do
  not by themselves justify several agents, that multi-agent systems "multiply
  expenses through redundant context processing and inter-agent communication", and
  that coordination "introduces latency at each handoff point".

### Criteria recorded in the SPEC

Each criterion is a short note in `agent_topology.criteria`. Fill it from the process
walk-through, not from a preference.

| Key | Question | Leans to one agent when | Leans to a split when |
| --- | --- | --- | --- |
| `latency_hops` | How many model round trips does one user turn need? | Each extra agent adds at least one model call and a network hop to a turn the user waits on | The work is asynchronous or batch, so extra hops do not reach a waiting user |
| `sessions` | Where does conversation state live? | One session holds the whole case; skills share context | Parts of the case must not see each other's context (data isolation) |
| `quota` | Which model deployments serve each agent? | All calls share one deployment's TPM, sized once | Agents need different models or separate quota so one cannot starve the other |
| `failure_points` | What breaks a turn? | One agent, one trace, one retry policy | A failure in one domain must not stop the others, and the contract between them is explicit |
| `cost` | What does a turn cost? | Context is processed once | Measured: a smaller model on a narrow agent costs less than one large agent, with prototype evidence |
| `ownership_boundary` | Who owns and releases each part? | One team owns the process | Different teams own and release their parts on their own cadence |
| `governance_boundary` | Do parts carry different permissions or regulation? | One identity and one policy bundle cover every tool | Separation of duties, different data classification or a regulator requires separate identities and audit trails |
| `eval_complexity` | How is quality proved? | One eval dataset and one red-team run cover the agent | Each agent needs its own eval suite, plus an end-to-end suite for the hand-offs |

Model quota is assigned per subscription, region, model and deployment type in
tokens per minute, and every deployment draws from that pool
([Manage Azure OpenAI quota](https://learn.microsoft.com/azure/foundry/openai/how-to/quota)).
Several agents on the same model therefore share the same quota pool unless they use
different regions, models or deployment types.

### Split triggers

`agent_topology.split_triggers` stays an empty list for the default single agent. A
split needs at least one of these, with evidence in `rationale`:

- `security-compliance-boundary`: data isolation or separation of duties that one
  agent identity cannot satisfy.
- `separate-owning-team`: another team owns, versions and supports that part.
- `independent-release-cadence`: a part must ship or roll back without the rest.
- `measured-single-agent-limit`: a single-agent prototype failed a defined success
  metric that prompting, retrieval or policy changes could not fix.

Roles such as planner, reviewer or executor are not a trigger on their own: model
them as skills in one agent first.

### What Foundry supports for multi-agent today

| Option | Status on Learn | Notes for Threadlight |
| --- | --- | --- |
| Connected Agents (classic Agents API) | Not available in the new Foundry Agent Service | Do not design on it. See [Connect to an A2A agent endpoint](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/agent-to-agent), which names A2A or workflows as the replacements |
| `a2a` tool (call a remote agent) | Generally available, A2A protocol 1.0; `a2a_preview` (0.3) stays preview | Documented for prompt agents directly, and for hosted agents through a toolbox with Microsoft Agent Framework ([agent-to-agent](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/agent-to-agent)) |
| Incoming A2A (expose an agent) | A2A 1.0 GA, 0.3 preview; requires the responses protocol; prerequisites name a deployed prompt agent; text only, no streaming, JSON-RPC only for 1.0 ([Enable incoming A2A](https://learn.microsoft.com/azure/foundry/agents/how-to/enable-agent-to-agent-endpoint)) | Exposing a **hosted** agent as an incoming A2A endpoint is not documented on that page today: treat it as an open item to verify, not a capability |
| Foundry workflows (visual designer) | Preview; Foundry is retiring workflows on December 1, 2026 ([Build a workflow](https://learn.microsoft.com/azure/foundry/agents/concepts/workflow)) | "Hosted agents aren't supported in the workflow designer." Do not start new designs on it |
| Microsoft Agent Framework workflows inside a hosted agent | The migration path the workflow article recommends; the result runs as a hosted agent | Orchestration lives in one hosted agent's code: one deployment, several internal executors |

### How Threadlight carries the decision

- **Single agent with skills** (default): `threadlight-deploy` generates one hosted
  agent with its skills. Nothing else changes.
- **Multi-agent**: each agent gets its own SPEC, pilot, identity, eval suite and
  governance contract, so every agent stays a reviewable Threadlight pilot. List them
  in `agent_topology.agents` with owner, identity and data boundary. Threadlight
  does not generate the inter-agent wiring (the `a2a` connection, toolbox or Agent
  Framework workflow): record it as an open item in SPEC § 13 with the owning team.
- **Workflow inside one agent** (`inter_agent_protocol: maf-workflow-in-hosted-agent`)
  is a single deployment. Use `workflow_model: workflow` in § 11e; it is not a
  multi-agent split.

Governance applies per agent. A policy bundle or a passing eval on one agent says
nothing about the others or about the hand-off between them.

## Example: Combining Traits

### Insurance Claims Processing
- **Data Sourcing**: Document intake (claim forms) + Database query (policy DB) + API integration (fraud detection)
- **Processing**: Extraction → Validation → Analysis → Routing
- **Output**: Decision + Action (create claim record) + Notification (adjuster assignment)
- **Interaction**: Human-in-the-loop (adjuster reviews complex claims)
- **Temporal**: Event-driven (new claim submitted)

### Competitive Intelligence Dashboard
- **Data Sourcing**: Web scraping + Search/research
- **Processing**: Extraction → Comparison → Synthesis
- **Output**: Report + Structured data (dashboard)
- **Interaction**: Periodic review
- **Temporal**: Scheduled (weekly)

### Customer Service Front Desk
- **Data Sourcing**: User input + Database query (CRM, KB) + API integration (order system)
- **Processing**: Routing → Analysis → Synthesis
- **Output**: Conversation + Action (create ticket, update CRM)
- **Interaction**: Conversational
- **Temporal**: On-demand
