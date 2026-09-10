# Runtime governance and AgentOps: implementation and evidence

For the **L200/L300** visual explanation, start with
[Governance & AgentOps on Pages](https://aiappsgbb.github.io/threadlight-skills/governance.html).
This **L400/L500** operative guide connects implementation contracts, commands,
artifacts and failure recovery. It does not authorize deployment or replace the
linked deep source contracts.

**Foundation first:** [Skill-based agents: construction, runtime and evidence](skill-based-agents.md)
separates the construction catalog from generated business skills, explains
[runtime loading](skill-based-agents.md#runtime-loading), and traces the reviewed
SPEC to tools, host and evidence. This guide continues at selected action
enforcement; skill instructions alone do not implement those controls.

**Operator path:** [Configure](#1-configure) → [Generate](#2-generate) →
[Validate](#3-validate) → [Approved deployment](#4-approved-deployment) →
[Collect](#5-collect) → [Rescore](#6-rescore).
For the separate opt-in lifecycle adapter, see [AgentOps](#agentops-preview-explicit-opt-in-bounded-assessment).

## Availability and source

| Capability | Status of this guide | Source |
|---|---|---|
| Binding-scoped runtime governance | Merged implementation; some pinned runtime dependencies remain preview/alpha | [PR #127](https://github.com/aiappsgbb/threadlight-skills/pull/127), merged source `8153bc2e0a677d99b8414053d7a00cfdab495444` |
| Per-agent AgentOps evidence adapter | **Merged into main at 2026-09-08T21:27:37Z**; preview describes maturity, not an unmerged PR | [PR #128](https://github.com/aiappsgbb/threadlight-skills/pull/128), merge `19610ca8a3b5e3bd9cff16536442cfc2ea69a717`; explanatory snapshot reviewed here at `4f59f7584a5f5d614c3625aa45f92f6a692c2194` |

AgentOps links below deliberately preserve the reviewed explanatory snapshot.
The [merged skill contract](https://github.com/aiappsgbb/threadlight-skills/blob/19610ca8a3b5e3bd9cff16536442cfc2ea69a717/skills/threadlight-agentops/SKILL.md)
is the implementation authority for adoption from that release. Distinguish
checkout contents, plugin distribution and preview maturity; a merge alone is
not live proof or a general-availability guarantee.

**Version scope:** the governance command path below pins the complete catalog
at `8153bc2e0a677d99b8414053d7a00cfdab495444`. A checkout through PR #127 has a
**23-skill inventory** and does not contain the AgentOps adapter. The separate
AgentOps path requires a reviewed complete catalog that includes #128, such as
`19610ca8a3b5e3bd9cff16536442cfc2ea69a717`. Check the installed revision before
running either path; remote merge metadata alone does not establish local
availability. Never update an inventory count without the matching code.

## Two responsibilities, not two competing engines

**Runtime governance controls selected actions before effects.** A binding names
the tool, intervention point and execution path to which the controls apply.
The runtime, not the model, supplies trusted facts and enforces the decision.

**The AgentOps preview validates lifecycle evidence per opted-in agent.** It
normalizes existing native artifacts for existing consumers. It neither
authorizes business actions nor certifies readiness.

| Question | Owning component | Evidence boundary |
|---|---|---|
| What policy and bindings have we declared? | [threadlight-govern](../skills/threadlight-govern/SKILL.md) | Offline inventory and policy authoring do not prove enforcement |
| Have selected runtime paths been exercised locally? | [threadlight-governed-actions](../skills/threadlight-governed-actions/SKILL.md) | Executed LOCAL-14/native evidence remains local |
| What did the deployed collector actually prove? | [Safe-check collector](../skills/threadlight-safe-check/references/governance-probe.md) | Only the reserved `governance_probe_noop` binding |
| Is the AgentOps evidence current and bound to this agent? | [threadlight-agentops preview](https://github.com/aiappsgbb/threadlight-skills/blob/4f59f7584a5f5d614c3625aa45f92f6a692c2194/skills/threadlight-agentops/SKILL.md) | Local process provenance and artifact validation, not Azure attestation |
| Does quality or safety meet the threshold? | [Evals](../skills/threadlight-evals/SKILL.md) and [red-team](../skills/threadlight-redteam/SKILL.md) | Execution success is not quality success |
| Who composes approved operations into delivery? | [CI/CD](../skills/threadlight-cicd/SKILL.md) | Existing pipeline owner; not a second deployment system |
| What remains before production? | [Production-ready](../skills/threadlight-production-ready/SKILL.md) | Current evidence, unresolved gaps and scoped acceptance; not certification |

## Runtime governance: select, enforce, prove

### Prerequisites and supported scope

Read the [runtime lifecycle](production-readiness.md#runtime-governance-lifecycle),
[generator contract](../skills/threadlight-deploy/references/governance/README.md)
and [pinned runtime versions](../skills/_shared/governance-upstream-pin.json)
before implementation. Actual selected hosts/gateways and registered services
are required; a policy document or an assessment-only checklist is insufficient.

SAFE is the method for defining business invariants. ACS/Rego is the policy decision point (PDP)
through local OPA. The native host or governed-tool gateway is the policy
enforcement point (PEP). Agent Hooks is the host/interceptor contract SDK.
AGT is the toolkit; ASSERT is assurance.

Native MAF supports selected tool/lifecycle and buffered-output paths.
Compaction, provider-hosted tools and incremental streaming/custom clients remain
unsupported. GHCP effect closure is limited to registered gateway actions:
arbitrary URL/shell routing and whole-lifecycle/full-output coverage are not
implied.

Unbound reads remain usable without ACS. Consequential unbound acceptance for
readiness must be explicit, current and scoped to owner, risk, tool, commit and
environment. Invalid selected configuration is not equivalent to disabling it.

### Required controls precede effects

For a selected binding, required controls include signed/fresh policy,
authenticated approval scoped to tenant/role/action with one-use consumption,
trusted backend targets/schema/dynamic facts, and central audit acknowledgement.
Reauthorize after credential or transport waits at actual dispatch. A model's
claim about the target is not trusted evidence; local spool durability is not
hosted audit proof.

Report `enforced`, `observed`, `unbound`, `unverified`, `unsupported` and
`bypassable` per binding. Do not collapse them into a whole-agent badge.

### Artifact map

| Artifact in the target project | What it contributes | What it cannot establish alone |
|---|---|---|
| `specs/governance-manifest.json` | Current `threadlight-governance-manifest/v1` per-binding inventory and evidence | Enforcement from a declared policy |
| `tests/governed-actions-manifest.json` | Selected runtime assessment and executed local conformance | Hosted business-action proof |
| `.threadlight/governance-live.json` | Fresh deployment-bound collector evidence | Any binding beyond the collector's reserved noop |

Current consumers reject legacy v2 green verdicts as current readiness evidence.
Preserve old captures as history, not reusable proof. After a new deployment or
scope/configuration/key/image/identity change, collect fresh after-deployment
evidence with matching signed bindings and independent observations.

The [returns example](../examples/returns-triage-governed/README.md) records a
Cosmos decision and audit, **not payment settlement**.
`returns_apply_decision` has local evidence but remains live-unverified.
The dated hosted noop snapshot proves only its own selected noop path, not
business writes, whole-agent governance or current production readiness.
Use the [dated record and caveats](production-readiness.md#private-ghcp-noop-evidence-snapshot-2026-09-08);
this guide intentionally does not copy deployment-specific data.

## Operative path: configuration to current evidence

The following stage commands are taken from
[`runtime_readiness.py`](../scripts/ci/runtime_readiness.py) and the
[protected workflow](../.github/workflows/threadlight-e2e-foundry.yml). They are
not a new all-in-one deployment script. Execute each stage only after the
preceding stage succeeds and its artifacts are reviewed.

### 1. Configure

Use the [pinned complete-catalog setup](production-readiness.md#pinned-catalog-invocation):
`CATALOG` is the reviewed catalog, `PROJECT` the target pilot. The source pilot
must contain the reviewed
[design deployment manifest](../skills/threadlight-design/SKILL.md#5-specsmanifestjson)
and [governance contract](../skills/threadlight-design/references/governance-contract.schema.json).
Select bindings, required interventions and runtime explicitly; do not infer
them from package imports. For Kratos exports, first perform the
[coding-agent manifest adaptation](KRATOS-BRIDGE.md#adapt-before-safe-check).

For the protected resume track, the owner supplies `GOVERNANCE_CI_CONFIG` and
its exact approved `GOVERNANCE_CI_CONFIG_SHA256`, with
`remote_bootstrap.mode: resume-signed-bootstrap/v1`. See the complete
[protected input table](production-readiness.md#protected-readiness-proof-ci-inputs)
for source/tree hashes, signed policy, immutable images, observed identities,
registered fixture, network and content-pinned creation/attempt/publisher inputs.
No placeholder configuration is executable proof.

The existing workflow sets `GOVERNANCE_SAFE_PROBE=true` only after explicit
approval, allocates `GOV_PROJECT` as an isolated generated project, and initializes
`READINESS_EVIDENCE_STATE` using `readiness_evidence.py init`. Later stages require
that workflow-owned journal and real `GITHUB_RUN_ID` / `GITHUB_RUN_ATTEMPT`.
Do not fabricate these values to bypass the workflow. Commands below assume
that initialized runner, pinned Python/native environment and protected inputs.

```bash
python3 "$CATALOG/scripts/ci/runtime_readiness.py" validate-inputs \
  --configuration "$GOVERNANCE_CI_CONFIG" --project "$GOV_PROJECT"
```

**Expected:** stage completion, exit `0`; invalid input/legacy mode exits `1`
(argparse errors exit `2`) before deployment/login.
**Stop:** missing approval, wrong digest, dirty/unapproved source, unknown scope
or missing external prerequisite. **Recovery:** correct the operator-owned
inputs and reapprove changed bytes; never replace observations with selectors.

### 2. Generate

```bash
python3 "$CATALOG/scripts/ci/runtime_readiness.py" prepare \
  --configuration "$GOVERNANCE_CI_CONFIG" --project "$GOV_PROJECT"
```

This local stage builds/validates the bundle, calls real `generate.py foundation`
and `generate`, prepares the immutable agent context and exports standalone local
validation tooling into `GOV_PROJECT`. It does not provision Azure. Expected
artifacts include generated host/service/infra sources,
`.threadlight/governance-package.json`, `.threadlight/ci-input.json` and
`governance/source-provenance.json`. The generated Git snapshot is local
provenance, **not a deployment commit**.

For manual generation outside that driver, the actual entrypoints are:

```bash
python3 "$CATALOG/skills/threadlight-deploy/references/governance/generate.py" foundation \
  --project "$PROJECT" --contract /protected/contract.json --configuration /protected/infrastructure.json
python3 "$CATALOG/skills/threadlight-deploy/references/governance/generate.py" generate \
  --project "$PROJECT" --contract /protected/contract.json --configuration /protected/package.json
```

These paths are operator-supplied JSON, not files shipped at `/protected`.
Follow the [generator contract](../skills/threadlight-deploy/references/governance/README.md)
for signed publication and actual service URLs between stages; do not run these
again over a workflow-prepared project. Signing/provisioning remain separate.
**Stop/Recovery:** preserve generation/export conflicts and fix inputs. Do not
overwrite unrelated project CI or patch frozen generated bytes to force binding.

### 3. Validate

The protected runner uses:

```bash
python3 "$CATALOG/scripts/ci/runtime_readiness.py" predeploy \
  --configuration "$GOVERNANCE_CI_CONFIG" --project "$GOV_PROJECT"
```

It runs the exported native/CTK local-validation gate and the real assessor.
The assessor's direct equivalent (not a substitute for executing native tests) is:

```bash
python3 "$CATALOG/skills/threadlight-governed-actions/scripts/governed_actions.py" \
  --target "$PROJECT" --phase pre-deploy --emit --gate
```

Expected assessor artifacts: `tests/governed-actions-manifest.json`,
`docs/governance/evidence-pack.md`, and `tests/governed-actions-apply-plan.json`.
Assessor exits: `0` gate passed; `1` open must-fix/required not-verified findings;
`2` invalid input/schema; `3` tooling/write failure. The stage driver normalizes
caught failures to `1`. Without `--gate`, exit `0` only means assessment completed.
Read the [exact gate contract](../skills/threadlight-governed-actions/SKILL.md#running-it)
and [native validation contract](../skills/threadlight-deploy/references/governance/README.md#what-this-proves).

**Stop:** absent, skipped or failed required native tests/LOCAL-14. **Recovery:**
correct source/configuration, regenerate and rerun with the same approved pins.
Do not claim native/CTK or Azure proof from these documentation regression tests.

### 4. Approved deployment

This step is **not offline**. Approval must name target, deployer/publisher and
collector identities, network, budget, registered services and cleanup owner.
Use the [create-once operator contract](../skills/threadlight-deploy/references/governance/README.md#signed-remote-bootstrap-operator-contract)
to prepare the existing SDK creation and separate service deployment first.

```bash
python3 "$CATALOG/scripts/ci/runtime_readiness.py" deploy \
  --configuration "$GOVERNANCE_CI_CONFIG" --project "$GOV_PROJECT"
```

Only the explicit resume mode is supported here. It resumes the acknowledged
creation, verifies observations, publishes the signed bootstrap, configures the
endpoint and waits; it does not call azd or create another agent version.
Expected protected outputs include `.threadlight/hosted-bootstrap.json` and
`.threadlight/readiness-attempt.json`, completed in the current run/attempt.
Exit `0` completes the stage, not readiness.

**Stop:** ambiguous create/PATCH acknowledgement, wrong route, changed
image/identity/scope or expired signature. **Recovery:** independently reconcile
the existing version/endpoint; retain failed attempt records, serialize endpoint
writers and use the operator contract's explicit retry rules. Never rerun
`azd deploy` after binding to chase a version number.

### 5. Collect

```bash
python3 "$CATALOG/scripts/ci/runtime_readiness.py" postdeploy \
  --configuration "$GOVERNANCE_CI_CONFIG" --project "$GOV_PROJECT"
```

The driver invokes the installed portable safe-check package, not a copied
script. It requires a completed attempt in this run, independently checked parent
scope and fresh **after-deployment** evidence. It publishes the collector's
actual result as `tests/postdeploy-manifest.json`,
`.threadlight/governance-live.json`, `specs/governance-manifest.json` and
`tests/runtime-readiness.json`, then checks current readiness. For separately
approved collection outside the driver, the real parent CLI from the pilot is:

```bash
threadlight-safe-check --phase post-deploy --manifest specs/manifest.json \
  --subscription <approved-subscription> --rg <approved-resource-group> --out tests
```

Follow [collector prerequisites and permissions](../skills/threadlight-safe-check/references/governance-probe.md)
first. Safe-check exits `0` for empty gaps, `1` for gaps, `2` for prerequisites,
`3` for tooling/auth errors. Noop proof stays scoped to `governance_probe_noop`.
**Stop:** unreachable fixture/producer, incomplete allow/deny pair, drift or
missing business proof. **Recovery:** preserve failure evidence; fix the approved
prerequisite and collect a new pair. Never splice successful halves, reuse old
nonces or rerun the offline inventory emitter over collected proof.

### 6. Rescore

The driver's `postdeploy` stage also emits eval/red-team assessments, runs the
static production-ready scorecard, then `evidence_gate.py --mode readiness-proof`.
A report-only domain result is not a quality pass. For an independent static
rescore against the actual target project:

```bash
python3 "$CATALOG/skills/threadlight-production-ready/scripts/production_ready.py" --root "$PROJECT" \
  --static --no-rights-probe --gate-preview
```

For a driver-generated project, set `PROJECT` to that `GOV_PROJECT`, not the
original source pilot. See [requirements and exits](production-readiness.md#9-cli-cheatsheet)
and the [scoring/gate table](production-readiness.md#5-status-taxonomy):
reports are `docs/production-readiness-report.md` and
`tests/production-readiness-manifest.json` (plus default trend CSV);
exit `2` can be a prerequisite failure or the explicit raw must-fix gate.
**Stop/Recovery:** inspect the report and current binding evidence, fix the
specific gap under its existing owner, then recollect/rescore as needed.
Do not waive missing live proof by relabeling inventory or local success.

## AgentOps preview: explicit opt-in, bounded assessment

### Entry and default behavior

A regular **`agentops.yaml`** opts in an agent root. A `.agentops/` directory
alone does not. No opt-in yields `not-applicable`, an empty agents list and no
penalty; unrelated deployment metadata is not required merely to establish
absence.

The default assessor is bounded, local and read-only unless manifest emission
is explicitly requested. It does not install dependencies, authenticate, call
Azure, run native analysis/eval/Doctor, or provision resources. Do not copy an
execution command from an older snapshot into a different checkout without
checking its installed version. The adapter is merged; its
[pinned skill contract](https://github.com/aiappsgbb/threadlight-skills/blob/4f59f7584a5f5d614c3625aa45f92f6a692c2194/skills/threadlight-agentops/SKILL.md)
owns exact CLI arguments and discovery rules.

For the **merged #128** implementation, use a separate complete reviewed catalog:

```bash
AGENTOPS_CATALOG=/absolute/path/to/reviewed/agentops-catalog
AGENTOPS_REVISION=19610ca8a3b5e3bd9cff16536442cfc2ea69a717
test "$(git -C "$AGENTOPS_CATALOG" rev-parse HEAD)" = "$AGENTOPS_REVISION" || exit 1
git -C "$AGENTOPS_CATALOG" diff --exit-code HEAD -- skills scripts || exit 1
python3 "$AGENTOPS_CATALOG/skills/threadlight-agentops/scripts/agentops_check.py" \
  --target "$PROJECT" --emit --json --gate --freshness-hours 24
```

This command emits metadata only; it does not run native operations. Prerequisites
are Python 3.12+, local Git, existing PyYAML only for `azure.yaml` service discovery
and existing OpenSSL only for signed receipts. Author opt-in/configuration under
the existing lifecycle owner first. Use the merged catalog's canonical consumers
together when adopting #128; the #127 scorer does not acquire `AOPS-001` merely
because a remote guide describes it. Historical links below remain explanatory.

### Native evidence and provenance

The preview consumes the `agentops-accelerator==0.14.0` native contract.
Source/target/environment binding is independent of native result text.
Current committed policy, config/dataset/artifact hashes, observed run identity,
freshness and result/history consistency are checked.

An approved observer records a real bounded operation, rather than relabeling
old outputs or trusting a file named `latest`. Dirty input trees and stale or
unbound artifacts cannot silently become verified evidence.

Receipts establish **local process provenance, not Azure attestation**. They
assume the approved runner and checkout are trusted; a party controlling all
local files can forge local provenance. Hashes alone are not signatures.
Existing signing controls can optionally require signatures; ordinary adoption
does not create a new PKI or signing authority.

### Outputs and interpretation

`specs/agentops-manifest.json` contains normalized metadata, not raw prompts,
responses, native logs or credentials. Keep approved native source artifacts
private until downstream hash checks finish; premature cleanup invalidates
validation.

| Outcome | Meaning | Operational implication |
|---|---|---|
| `not-applicable` | No agent opted in | No penalty to existing pilots |
| `partial` | Evidence missing, stale or unverified | Not a pass; default gate can still exit 0 |
| `operational` | Evidence conforms to the checked contract | A verified quality failure still fails the quality gate |
| `blocked` | Must-fix evidence/operational findings | Default `--gate` exits 2 for these findings |

Invalid invocation, unsafe discovery and failed execution prerequisites exit 1.
These are assessor semantics, not a replacement for native or domain-owner exit
codes. Doctor refresh preserves native exit 2; assessment does not erase it.

Canonical eval/red-team consumers preserve worse valid outcomes and distinguish
execution rates from quality scores. Governance metadata is supplemental: it
cannot satisfy #127's runtime/policy/attestation requirements. Production-ready
adds residual **`AOPS-001`** findings, deduplicating only against actual matching
canonical evidence. Auto plans; it does not acquire permission to execute
operations by discovering a manifest.

### Approval before native operations

Native analysis may inspect remote datasets; do not assume it is offline.
Eval and Doctor may require service access and inference spend. The existing
owner must approve the exact agent root, isolated identity, target/environment,
costs, telemetry destination/capture/retention and private output destinations.
The existing CI/CD owner composes those operations. No automatic baseline
promotion, retries for green, resource creation, RBAC expansion or credential
changes are authorized by the preview assessor.

| Pinned reference | Use it for |
|---|---|
| [Artifact mapping](https://github.com/aiappsgbb/threadlight-skills/blob/4f59f7584a5f5d614c3625aa45f92f6a692c2194/skills/threadlight-agentops/references/artifact-mapping.md) | Native models, domain ownership, deduplication and privacy |
| [Receipt contract](https://github.com/aiappsgbb/threadlight-skills/blob/4f59f7584a5f5d614c3625aa45f92f6a692c2194/skills/threadlight-agentops/references/receipt-contract.md) | Independent binding, observer provenance and optional signatures |
| [Execution approval](https://github.com/aiappsgbb/threadlight-skills/blob/4f59f7584a5f5d614c3625aa45f92f6a692c2194/skills/threadlight-agentops/references/execution-approval.md) | Native operation prerequisites and identity/export scope |
| [CI/CD runtime composition](https://github.com/aiappsgbb/threadlight-skills/blob/4f59f7584a5f5d614c3625aa45f92f6a692c2194/skills/threadlight-cicd/references/agentops-runtime.md) | Reuse of the existing delivery pipeline owner |

## Choosing the next step

For runtime control, follow the selected-binding generator and governance
contracts first. For evidence review, distinguish declared, local and hosted
proof before opening the scorecard. For the AgentOps preview, start with
opt-in and provenance requirements, not a paid run.

Use [Self-improving](https://aiappsgbb.github.io/threadlight-skills/self-improving.html)
for finished-run diagnostics and plan-only upgrades, and
[Production-ready](https://aiappsgbb.github.io/threadlight-skills/production.html)
for the broader architecture and readiness review. Neither remediation nor a
valid evidence envelope guarantees a green result.
