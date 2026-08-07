# Threadlight Lifecycle Canvas - Design Specification

- **Status:** Approved
- **Date:** 2026-08-06
- **Primary persona:** Solution Engineer running a pilot end to end
- **Target host:** GitHub Copilot App with Canvas support
- **Distribution:** Extension bundled with the `threadlight-skills` plugin

## 1. Problem

Threadlight exposes a capable but technically named pipeline of 17 skills. A
Solution Engineer who knows the customer outcome but not the skill catalog must
currently translate that outcome into the right sequence, inspect several files
and reports, and return to chat to decide what to do next.

The first version of the Canvas must let an SE start and steer a pilot without
knowing skill names. Within the GitHub Copilot App, it should answer three
questions quickly:

1. What outcome phase is the pilot in?
2. What is blocking progress?
3. What safe action should happen next?

The Canvas is an optional UX layer. It must not replace the skills, their
canonical artifacts, permission gates, or chat-based fallback.

## 2. Goals

- Provide one outcome-oriented entry point for all 17 Threadlight skills.
- Let an SE start a pilot from a brief without typing or selecting skill names.
- Organize the lifecycle as Design, Build/Deploy, Discover, Protect/Govern,
  Improve, and Handoff.
- Project state from existing workspace artifacts rather than create a second
  orchestration state.
- Route user choices back to the agent as typed intents. The agent remains
  responsible for explaining and executing actions through normal permission
  and confirmation flows.
- Preserve current chat and file behavior in hosts without Canvas support.

## 3. Non-goals

- A second Threadlight orchestrator or scheduler.
- Direct file mutation, process execution, Azure calls, deployment, retry, or
  remediation from the web UI.
- Replacing `.threadlight/auto-state.json`, skill manifests, or reports with a
  Canvas-owned database.
- Building 17 bespoke skill UIs in the first release.
- Requiring Canvas for GitHub Copilot CLI, Coding Agent, Cowork, or other
  supported runtimes.
- Reworking the operator workspace produced by `threadlight-workspace-ui`.
  That workspace serves the customer's business operator; this Canvas serves
  the SE building and governing the pilot.

## 4. Product model

### 4.1 Single Lifecycle Cockpit

The plugin contributes one canvas, `threadlight-lifecycle`. It is available
after plugin installation but remains dormant until:

- the agent recognizes a Threadlight kickoff or lifecycle-management intent; or
- the user explicitly opens the Threadlight Canvas and chooses **Start a
  pilot**.

It does not open on every session.

The home surface shows six outcome phases. Each phase displays:

- normalized status;
- a short outcome statement;
- current blocker, if any;
- strongest evidence;
- one primary next action;
- optional secondary inspection actions.

Skill names remain available under **Technical details**, but the primary
language is goal-oriented: "Design the pilot", "Run in a sandbox", "Assess
quality and safety", and "Prepare the handoff".

### 4.2 Specialized and generic views

Four specialized views cover the highest-value moments:

| View | Responsibilities |
|---|---|
| **Design** | Brief, unresolved decisions, Full vs Fast-PoC mode, SPEC progress, and expected artifacts |
| **Run** | Pipeline progress, gates, failures, resume eligibility, and the next safe stage |
| **Assurance** | Safe-check, consumption, eval, red-team, and governance evidence in one view |
| **Handoff** | Production readiness, CI/CD, customer customization, and handoff artifacts |

All other skill surfaces use a generic panel with the same contract: purpose,
prerequisites, detected inputs, expected outputs, status, evidence, blocker, and
next action.

### 4.3 Lifecycle mapping

Every skill is mapped exactly once in the registry. `threadlight-auto` is the
cross-phase driver but is displayed in Build/Deploy with the role
`orchestrator`.

| Outcome phase | Skills |
|---|---|
| **Design** | `threadlight-design`, `threadlight-demo-data-factory`, `threadlight-event-triggers`, `threadlight-hitl-patterns`, `threadlight-workspace-ui` |
| **Build/Deploy** | `threadlight-auto`, `threadlight-local-test`, `threadlight-deploy`, `threadlight-safe-check` |
| **Discover** | `threadlight-consumption-iq`, `threadlight-evals`, `threadlight-redteam` |
| **Protect/Govern** | `threadlight-govern` |
| **Improve** | `threadlight-router-bench` |
| **Handoff** | `threadlight-production-ready`, `threadlight-cicd`, `threadlight-customize` |

## 5. Interaction model

The Canvas is a cockpit, not a control plane.

1. The user selects an outcome action such as `start_pilot`, `resume_phase`,
   `inspect_evidence`, or `prepare_handoff`.
2. The extension validates the action against a JSON Schema and attaches only
   the minimum structured context needed by the active session.
3. The action is surfaced in chat as a user-visible intent.
4. The agent explains the action, resolves it to the correct skill or command,
   and uses existing permission and confirmation mechanisms.
5. Workspace changes produced by the agent are observed by the projector and
   reflected back in the Canvas.

The first release must not expose an action handler that writes workspace files,
starts a process, retries a deployment, or calls Azure directly.

## 6. Architecture

### 6.1 Components

1. **Canvas provider**
   - Declares `threadlight-lifecycle` with `createCanvas`.
   - Implements open and close lifecycle callbacks.
   - Exposes agent/host actions for refresh, phase focus, evidence inspection,
     and typed intent preparation.
   - Returns a local web URL, title, and normalized status.

2. **Lifecycle registry**
   - Contains the six phases and exact 17-skill mapping.
   - Declares each skill's prerequisites, canonical artifact probes, optional
     read-only checker, and available intent types.
   - Keeps user-facing outcome language separate from technical skill names.

3. **Workspace projector**
   - Reads only allowlisted Threadlight artifacts under the active session
     working directory.
   - Runs read-only checkers where they already own status semantics.
   - Converts findings into one normalized view model.
   - Refreshes on open, explicit refresh, and debounced workspace changes.

4. **Adapters**
   - A generic adapter covers all registry entries.
   - Specialized adapters enrich Design, Run, Assurance, and Handoff.
   - Adapters may interpret existing manifests but must not duplicate business
     gate logic already implemented by a skill.

5. **Local web client**
   - Renders the lifecycle, specialized views, and generic panels.
   - Uses bundled static assets only.
   - Posts validated, side-effect-free intents to the extension bridge.

6. **Intent broker**
   - Accepts only declared intent schemas.
   - Associates each request with the active canvas and session.
   - Uses the extension session API to surface the intent in chat.
   - Never invokes a skill, shell command, or Azure API itself.

### 6.2 Normalized view model

The projector returns a model equivalent to:

```text
LifecycleView
  workspace
  generatedAt
  phases[]
    id
    label
    status
    summary
    blockers[]
    evidence[]
    nextActions[]
    skills[]
      id
      role
      status
      prerequisites[]
      evidence[]
      blocker
      nextActions[]
```

Allowed statuses are:

- `not-started`
- `not-applicable`
- `ready`
- `running`
- `blocked`
- `stale`
- `failed`
- `complete`

Missing, stale, malformed, and failed are distinct states. Absence is never
converted into success.

### 6.3 Canonical sources

The initial allowlist includes:

- `specs/SPEC.md`, `specs/foundation.md`, and `specs/manifest.json`;
- `specs/sample-data/` summary metadata;
- `.threadlight/auto-state.json` and
  `.threadlight/preflight-passed.json`;
- `AGENTS.md` and expected output roots such as `src/agent/`,
  `src/workspace/`, and `src/triggers/`;
- `azure.yaml` and structural presence under `infra/`;
- safe-check, cost, eval, red-team, govern, and production-readiness manifests
  and reports;
- CI/CD and customization handoff artifacts;
- non-sensitive Git metadata used only for freshness and changed-file signals.

The projector must not read `.env`, `.azure/**/.env`, credentials, tokens,
secret-bearing command output, or arbitrary workspace files. It displays
summaries and paths rather than raw environment or deployment output.

## 7. Security and reliability

### 7.1 Local serving boundary

- Bind the Canvas server to `127.0.0.1` only.
- Use a random per-instance capability token in the URL or request headers.
- Apply a restrictive Content Security Policy.
- Load no remote scripts, fonts, analytics, or other resources.
- Stop instance resources on close and extension shutdown.

### 7.2 Honest failures

- A malformed artifact identifies the file and parser error.
- An unavailable checker reports `unavailable`; it does not imply pass.
- Conflicting artifacts report `stale` or `blocked` according to the owning
  skill's freshness contract.
- Projector failures remain visible in the Canvas and can be inspected in chat.
- No broad catch may return a success-shaped fallback.

### 7.3 Experimental SDK isolation

Canvas is an experimental SDK surface. All SDK-specific declarations, provider
callbacks, and host capability checks belong behind one small adapter module.
The rest of the lifecycle registry, projector, adapters, and tests remain
host-independent.

When Canvas rendering is unsupported, the extension does not open the web
surface. Existing skills, files, and chat behavior remain unchanged.

## 8. Packaging and compatibility

The extension ships with the installed `threadlight-skills` plugin so it is
available in arbitrary pilot repositories. Loading the extension must not
modify a target repository.

The current skill contracts remain authoritative. A UX requirement that cannot
be derived from an existing artifact must be added explicitly and
backward-compatibly to the owning manifest before the Canvas consumes it.

The implementation must pin and document the minimum supported GitHub Copilot
App/SDK version. A compatibility probe runs before the full UX work so SDK or
plugin-packaging changes fail early.

## 9. Delivery plan

### 9.1 Technical spike

The first implementation increment proves:

1. plugin-shipped extension discovery;
2. Canvas open and close;
3. local web rendering;
4. projection refresh after a fixture file change;
5. a click-to-chat typed intent round trip;
6. graceful behavior when Canvas rendering is unavailable.

The spike does not implement the complete cockpit. If any of these fundamentals
cannot be made reliable with the supported SDK, implementation pauses before
building the UI.

### 9.2 Version 1

After the spike passes, version 1 adds:

- the six-phase shell and exact 17-skill registry;
- generic panels for every skill;
- Design, Run, Assurance, and Handoff specialized views;
- safe typed intents;
- fixtures, accessibility, integration, and fallback coverage.

## 10. Validation

### 10.1 Automated tests

- Unit tests for lifecycle mapping, artifact probes, normalization, freshness,
  redaction, and intent schema validation.
- Contract test proving every one of the 17 skills is mapped exactly once.
- Contract test proving all artifact reads stay inside the allowlist.
- Contract test proving Canvas actions have no direct side-effect handlers.
- Fixtures for empty, design-only, deploy-blocked, partial-assurance, and
  complete-pilot workspaces.
- Provider tests for open, close, refresh, action dispatch, capability checks,
  and server cleanup.
- Playwright tests for navigation, keyboard access, status semantics, and
  GitHub Copilot App panel-sized viewports.
- Integration coverage against `examples/returns-triage-governed`.
- Explicit fallback coverage with Canvas capability disabled.

### 10.2 Acceptance criteria

1. From an empty session, an SE can choose **Start a pilot**, provide a brief,
   and reach the correct chat intent without seeing or entering a skill name.
2. All 17 skills are reachable through the six outcome phases.
3. Status, blockers, and evidence are derived from canonical artifacts and do
   not produce false success.
4. Every operational click is handed to chat before file, process, or
   infrastructure side effects.
5. Installing `threadlight-skills` makes the Canvas available in a compatible
   GitHub Copilot App session.
6. Unsupported hosts preserve existing Threadlight chat and file behavior.

## 11. Success measure

The primary version 1 measure is task completion, not dashboard engagement:

> A Solution Engineer who does not know the Threadlight skill catalog can start
> a correctly routed pilot from the Lifecycle Cockpit without typing or
> selecting a skill name.

Secondary usability checks measure whether the same SE can identify the current
phase, blocker, and next safe action in under 30 seconds.
