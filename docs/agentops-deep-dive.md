# AgentOps: from a working pilot to a controlled release

The technical companion to the [AgentOps area](production.html#operating-controls).
Threadlight generates delivery artifacts and assesses evidence. The customer
owns deployment authority, evidence execution and the decision to enable
business use. **Deployment success is not the same event as go-live.**

For example, a prompt update can deploy successfully while selecting the wrong
tool. A passing evaluation against a mock can also hide a denied production API
call. Configuration review, executed evaluations and deployed-target verification
answer different questions.

## Contents

1. [Choose the delivery model](#1-choose-the-delivery-model)
2. [Keep the shared platform separate](#2-keep-the-shared-platform-separate)
3. [What the generated pipeline actually executes](#3-what-the-generated-pipeline-actually-executes)
4. [Interpret the verdict readers](#4-interpret-the-verdict-readers)
5. [Optional bound evaluation execution](#5-optional-bound-evaluation-execution)
6. [Assemble evidence before go-live](#6-assemble-evidence-before-go-live)
7. [Implementation map and boundaries](#7-implementation-map-and-boundaries)

## 1. Choose the delivery model

| Model | Execution authority | Threadlight's contribution |
|---|---|---|
| Operator-led pilot | Explicitly authorized operator in the agreed target | An `azd` application and deployment guidance through `threadlight-deploy` |
| GitHub Actions | Federated pipeline identity, scoped permissions and configured environment checks | Workflow, identity/RBAC/runner runbooks through `threadlight-cicd` |
| Azure DevOps | Federated service connection, customer environment checks and runner pool | Pipeline and corresponding platform-team runbooks |

These are alternatives, not three consecutive stages. Generating YAML does
not grant permissions or configure reviewers. A private runner setting does not
establish working DNS, network isolation or permitted egress.

The [CI/CD generator](../skills/threadlight-cicd/scripts/generate_pipeline.py)
selects the pipeline with `--platform`. `--private-network` selects suitable
runner settings. `--eval-gate` controls evaluation and red-team handling;
`--mcp-gate` controls the MCP supply-chain reader separately.

## 2. Keep the shared platform separate

The generator resolves standalone, existing-hub onboarding, or a separate
hub-deployment-then-onboarding path. It writes the decision to
`docs/threadlight-cicd/onboarding-path.json` and the ownership boundary to
`docs/threadlight-cicd/central-platform-boundary.md`.

The workload pipeline deploys use-case resources into the target/spoke resource
group. It does not deploy the shared Citadel hub. The platform team owns that
external accelerator and its Access Contracts, identity configuration and
network routes. A workload's deployment identity must not acquire hub-wide
authority just because it consumes shared model services.

See the [external Citadel architecture and implementation](https://github.com/Azure-Samples/ai-hub-gateway-solution-accelerator/tree/citadel-v1)
for that platform, not an alternative implementation maintained by Threadlight.

## 3. What the generated pipeline actually executes

The base templates implement this order:

```text
Pipeline trigger
  -> configured environment checks
  -> federated login
  -> azd provision
  -> azd deploy
  -> evaluation / red-team / MCP verdict readers
  -> CI result
```

The GitHub Actions job explicitly declares `needs: deploy`; Azure DevOps stages
declare `dependsOn: deploy`. The readers therefore run **after deployment**.
The GitHub base supports a main-branch push or manual dispatch; the Azure
DevOps base uses a main-branch trigger.

Provision/deploy commands and verdict readers are executable. However, the base
quality, red-team and MCP producer steps are still `echo` instructions. A job
named "Run quality evals" is not evidence that an evaluation ran. Connect the
actual producers and their artifact delivery before treating the reader result
as evidence about this candidate.

A failed reader **does not roll back** deployed resources, switch traffic or
reverse a business transaction. The base template does not implement an
automatic staging-to-production promotion or merge-protection policy.

## 4. Interpret the verdict readers

| Reader | Artifact | Current decision |
|---|---|---|
| Evaluation | `specs/evals-manifest.json` | Accepts `comprehensive` or `partial` |
| Red-team | `specs/redteam-manifest.json` | Accepts `hardened` or `partial` |
| MCP supply chain | `tests/mcp-sbom.json` | Fails when `summary.must_fix` is nonzero |

These are the actual Python acceptance expressions in the templates, not
proposed stricter rules:

```python
sys.exit(0 if verdict in ("comprehensive", "partial") else 1)
sys.exit(0 if verdict in ("hardened", "partial") else 1)
```

**soft is the default.** A failing verdict step is allowed to continue.
Selecting hard handling makes the reader failure affect CI; it does not change
the accepted verdict vocabulary. In particular, hard still accepts `partial`.

A missing file fails the reader. The base readers do not establish freshness
or deployment binding, and they are not complete schema validators: for
example, the MCP reader defaults a missing `must_fix` field to zero.
Do not infer current coverage merely from a successful workflow.

## 5. Optional bound evaluation execution

The broader AgentOps topic is not the same thing as the optional native
AgentOps integration. That integration requires an agent's explicit
`agentops.yaml` opt-in in the target repository.

For opted-in agents, `_compose_agentops` adds execution through
`agentops_runtime.py --run-eval`, followed by
`evals_check.py --target . --emit`. The assessor consumes the validated batch;
it does not run a second evaluation. This composition does not automatically
replace the red-team or MCP producer placeholders.

Without opt-in, `--agentops auto` leaves the workflow unchanged;
`--agentops off` also disables composition. Composed PR evaluation does not
deploy or promote a baseline. Optional Doctor refresh/scheduling is separate,
off by default, and requires explicit current runtime-owner approval. Do not
mistake a documentation lookup or pipeline generation for that authorization.

## 6. Assemble evidence before go-live

| Evidence | What to establish | What it does not establish |
|---|---|---|
| Offline inspection | Configuration, policy/schema validity, dependencies and declared assets | Live reachability or business execution |
| Executed evaluation | Candidate and baseline results on representative cases, with expected outcomes and thresholds | Authority for a later business transaction |
| Red-team exercise | Observed adversarial behavior and unresolved findings | Elimination of every attack or future failure |
| Deployed-target verification | Actual environment, version, identity, routes and allowed/denied behavior | Coverage of untested tools or a different deployment |

Keep source revisions, configuration, run scope and dates attached to results.
A noop probe is not business-write proof. Endpoint, data-boundary and load
checks need their own agreed scope; live or paid probes require authorization.
Missing access and unexecuted checks remain unverified, not successful.

Choose and protect the validation target explicitly. Accountable reviewers
decide whether the evidence supports business go-live, including any scoped,
time-bounded risk acceptance. An environment approval to deploy is not evidence
that all these checks have passed.

Once the application is in use, [agent action governance](agent-governance-deep-dive.md)
can authorize selected effects on each invocation. A release verdict neither
replaces that runtime control nor reverses already committed business effects.

## 7. Implementation map and boundaries

| Source | Use it for |
|---|---|
| [Pipeline generator](../skills/threadlight-cicd/scripts/generate_pipeline.py) | Onboarding, template selection and optional composition |
| [GitHub Actions template](../skills/threadlight-cicd/references/github-actions/azd-deploy-prod.yml.tmpl) | Executable ordering, producers and readers |
| [Azure DevOps template](../skills/threadlight-cicd/references/azure-devops/azure-pipelines.yml.tmpl) | Equivalent stage/identity/reader behavior |
| [Evaluation assessor](../skills/threadlight-evals/scripts/evals_check.py) | Assessment of assets, results and integration evidence |
| [CI/CD skill and runbooks](../skills/threadlight-cicd/SKILL.md) | Generator inputs and platform-team handoff |
| [Production-readiness reference](production-readiness.md) | Broader assessment, evidence contracts and acceptance boundaries |

Where high-level template comments describe a fully wired workflow, the
executable steps above determine what currently runs. This guide documents
those boundaries; it does not implement the missing producer integrations.
No cloud execution, deployment approval or broader Task15 acceptance is
established by publishing this document.
