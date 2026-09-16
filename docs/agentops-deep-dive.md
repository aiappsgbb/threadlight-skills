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
runner settings. Required eval, red-team and MCP checks are always blocking.
Legacy `--eval-gate hard` / `--mcp-gate hard` are accepted; soft modes are not.

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

Both platforms implement a verified release, not deploy-first report reading:

```text
Protected source and reviewed release policy
  -> validation environment + separate federated identity
  -> preflight required application-owned adapters and inputs
  -> prepare and independently observe a preproduction candidate
  -> execute evaluation / red-team / MCP producers
  -> strict canonical acceptance + metadata-only candidate receipt
  -> production environment approval + separate production identity
  -> verify receipt checksum, source, policy, inputs and current attempt
  -> promote the same immutable image; observe production
```

The production job declares `needs: validation` on GitHub and
`dependsOn: validation` on Azure DevOps. `release_runner.py` runs the reviewed
application-owned adapters using explicit argv, not shell snippets or report
substitutes. Missing configuration fails before preparation; the generated
`specs/release-policy.example.json` must be deliberately configured and committed
as the actual release policy. MCP uses the vendored `mcp_sbom.py --check`.

The validation job supplies `--receipt-sha256` through a separate CI output,
not only inside the uploaded artifact. The runner checks candidate/source,
tooling and dataset hashes, policy, CI repository/run/attempt and freshness,
including after identity waits and immediately before dispatch. An expired
environment approval wait requires fresh validation; it cannot revive authority.
Each declared target includes its deployment `client_id`, tenant and subscription.

The customer must configure environment approvals, branch checks and federation:
YAML alone does not enforce those external settings. Production adapters own
durable idempotency, unknown-outcome reconciliation and keeping business traffic
closed until required checks pass. A failed observation **does not roll back**
resources or business effects. See the
[release contract](../skills/threadlight-cicd/references/release-contract.md).

## 4. Interpret the verdict readers

| Domain | Canonical artifact | Release acceptance |
|---|---|---|
| Evaluation | `specs/evals-manifest.json` | Executed current run, datasets/scenarios, declared and met quality thresholds, required capabilities passed |
| Red-team | `specs/redteam-manifest.json` | Current scan, sufficient attacks, every required attack category and ASR within policy |
| MCP supply chain | `tests/mcp-sbom.json` | Actual discovery/check, complete consistent counters and no unresolved findings or lock drift |

An aggregate verdict, empty object or missing counter is not acceptance.
Contradictory findings and any `must_fix` block release. All capabilities are
required by default; an explicit reviewed evaluation capability list may exclude
optional adoption controls such as A/B comparison, but cannot omit core dataset,
execution, freshness or quality checks. This is the only scoped use of `partial`,
not a blanket waiver or a rewrite of the broader assessment.

Evidence must come from this execution against the independently observed
candidate and identify its actual completion time. Reissuing an old report with
a new file timestamp does not satisfy the release reader.

## 5. Optional bound evaluation execution

The broader AgentOps topic is not the same thing as the optional native
AgentOps integration. That integration requires an agent's explicit
`agentops.yaml` opt-in in the target repository.

For opted-in agents, the release producer is `release_agentops.py`, which binds
every agent's native target to the candidate observer, invokes the existing
`agentops_runtime.py --run-eval` once, verifies real native receipts and calls
`evals_check.py --target . --emit`. Native thresholds remain quality evidence;
execution pass rate is not relabeled a quality score. Red-team execution remains
the application's selected scanner, not a second native evaluator.

Without opt-in, `--agentops auto` adds no native jobs;
`--agentops off` also disables composition. Composed PR evaluation does not
deploy or promote a baseline. Optional Doctor refresh/scheduling is separate,
off by default, and requires explicit current runtime-owner approval. Do not
mistake a documentation lookup or pipeline generation for that authorization.

Native jobs require `THREADLIGHT_AGENTOPS_CONTEXT`: a private context file
supplied through existing approved runner preparation, never inherited deployment
login. Validation and production context paths are selected separately. The
generator creates no credentials, grants, approvals or dependency installation
that authorizes a run. The pinned native package, clean committed inputs and
fresh scoped one-use approval must already be available.

Production Doctor also needs a verified eval receipt for its own target and
binding; the validation candidate's receipt cannot authorize it. See the
[native runtime prerequisites](../skills/threadlight-cicd/references/agentops-runtime.md).
The actual native CLI has been exercised against a loopback-only HTTP target with
a deterministic F1 evaluator through the release bridge and canonical reader.
That is local native evidence, **not cloud acceptance**, a paid-model evaluation
or production Doctor proof.

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

Generated tooling enforces the release contract; application adapters, protected
CI settings and approved runtime preparation are still explicit prerequisites.
Local fixtures exercise ordering and rejection paths, not customer deployment.
No cloud execution, deployment approval or broader Task15 acceptance is
established by publishing this document.
