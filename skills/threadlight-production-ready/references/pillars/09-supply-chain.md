# Pillar 9 — `supply-chain`

> **v0.3.0:** Adds `GOV-103` (Defender for Servers / Containers plan
> enabled on the subscription — surfaces unmonitored image-pull or
> runtime drift in ACA / AKS workloads). Configurable via SPEC § 12
> `defender_plans_required`.

> **What this pillar answers.** Are container images pinned **by
> digest** (not `latest`)? Are Bicep modules pinned? Is dependency
> scanning on? Is an SBOM emitted?

Pinning prevents "the deploy worked yesterday and the image silently
changed under us today".

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `SUP-001` | No `FROM <image>:latest` in any `Dockerfile` in the repo (must be `FROM <image>:<tag>@<digest>` or pinned tag) | `must-fix` if found |
| `SUP-002` | No `image: <ref>:latest` in `azure.yaml` or Bicep | `must-fix` if found |
| `SUP-003` | Container images in `infra/` reference a registry name and tag (or digest); no public images without pinning | `should-fix` if unpinned public image |
| `SUP-004` | Bicep AVM modules pinned to a version (`br/public:avm/...:<x.y.z>`) — no floating tags | `should-fix` if floating |
| `SUP-005` | Dependency manifest pinned (`pyproject.toml` with `==` or hash; `requirements.txt` with `==`; `package.json` with lock file present) | `should-fix` if unpinned |
| `SUP-006` | SBOM emitted somewhere (`docs/sbom.json`, `sbom/*.json`, or CI step) | `should-fix` if absent |
| `SUP-007` | Dependabot / GH Advanced Security / equivalent scanning enabled (`.github/dependabot.yml` or CodeQL workflow present) | `should-fix` if absent |
| `SUP-008` | No skill/tool **force-publish** (`--force` / `--overwrite` on an `azd ai skill` / `az … skill` / `foundry … skill\|tool` create command) in `azure.yaml` hooks, `.github/workflows/**`, or shell/PowerShell scripts | `should-fix` if found |
| `SUP-009` | If the repo consumes agent skills/tools (a toolbox, `azd ai skill`, an MCP plugin, or a `skills/**/SKILL.md`), it declares a **pinned** `SkillVersion` / toolbox version | `should-fix` if used but unpinned; `not-applicable` if no skills/tools |
| `SUP-010` | If the repo declares MCP servers, each is **pinned** to an exact version (npx/uvx/pip `==`) or image **digest** (`@sha256:...`) — no floating tags | `must-fix` if unpinned; `not-applicable` if no MCP servers |
| `SUP-011` | Each MCP server resolves from a **known registry or source** (npm, PyPI, a named container registry, or an explicit remote URL) | `should-fix` if unresolvable |
| `SUP-012` | An **`mcp-lock.json`** is committed and matches the current MCP server/tool surface (versions, digests, tool descriptor + input-schema hashes) | `must-fix` on pinned-server drift; `should-fix` if absent or unpinned drift |
| `SUP-013` | No MCP server config commits **inline credentials** (api keys / tokens / connection strings in `env` or `headers`) — use injected secrets | `must-fix` if found |
| `SUP-014` | **Aggregate** roll-up of the `threadlight-governed-actions` change-plane verdict — see below | `not-verified` if no trustworthy manifest |

### `SUP-014` — governed-actions change-plane evidence (aggregate)

The change plane is the second way a consequential action reaches production:
not through the running agent, but through an automated coding agent that opens
pull requests against this repository. `threadlight-governed-actions` owns that
assessment in full. production-ready never re-runs any of its probes and never
imports the assessor; it reads that skill's
`tests/governed-actions-manifest.json` and rolls the **change** domain up into
this single finding.

`SUP-014` owns seven child findings — `GHCP-001` … `GHCP-006` (GitHub Copilot
coding-agent governance: allow-listing, review requirements, firewall/network
egress, secret exposure, workflow permissions, and branch protection) plus
`OPS-001` (operational alerting). `OPS-001` is a `both`-plane finding, so it is
the one child shared with the runtime domain: it also feeds `AGT-007` in
pillar 2. The `GHCP-*` children map here and nowhere else.

The aggregate takes the **worst** status among those children —
`must-fix` > `not-verified` > `should-fix` > `pass` > `not-applicable` — and
names which are open. The child findings are deliberately **not** restated as
production-ready findings: their IDs never enter this skill's catalog, and the
governed-actions manifest stays the single source of truth for the detail.

**Trust limits.** The manifest is untrusted repository content — which matters
especially here, since a compromised change plane is exactly the thing that
could edit the manifest that claims the change plane is fine. So none of its
claims are taken on faith. Before any child status is believed, production-ready
re-derives what it can for itself: the `threadlight-governed-actions-manifest/v1`
schema and exact top-level shape, a supported assessor name and version (no
forward trust for an unreviewed future assessor), a `pre-deploy`/`post-deploy`
phase, a clean (`dirty: false`) source whose repository and commit match the
repository and commit under assessment, **recomputed** policy hashes and a
re-derived policy-set digest matched against what every relied-upon evidence
entry binds itself to, a single target environment consistent with the selected
azd environment, an intact freshness window (`expires_at` must equal
`oldest_source_at + valid_for_hours`, and both capture and this run must fall
inside it), resolvable evidence references collected no later than capture and
phase-consistent with the findings citing them, and a `summary` that agrees
exactly with the findings it summarises.

Anything missing, malformed, stale, dirty, or mismatched on source, repository,
commit, policy set, environment, or binding makes `SUP-014` `not-verified` with
the reason attached — never `pass`, and never a `must-fix` manufactured from
evidence we could not stand behind. `summary.verdict` alone is never believed.

**Conformance is not certification.** A trusted, all-`pass` manifest means the
assessor ran against this exact repository state and raised nothing about the
change plane. It is a scoped, expiring conformance record — not a certification,
not a supply-chain attestation, and advisory like every other finding here.

### Live (tier 1)

| ID | Check | Default status |
|---|---|---|
| `SUP-101` | Deployed ACA container images reference a digest (`@sha256:...`) — not just a tag | `should-fix` if not digest-pinned |
| `SUP-102` | ACR (if used) has private endpoint or firewall for `target_posture` ∈ `{citadel-spoke, hybrid, vnet}` | `must-fix` if private posture & ACR public |
| `SUP-103` | ACR `adminUserEnabled: false` (use AAD) | `should-fix` if true |

## Common gaps

- All Dockerfiles use `FROM python:3.11` (no version pin). Python 3.11
  ships a security fix, image rebuilds, dependencies break.
- `azure.yaml` references `nginx:latest` for an ingress sidecar.
- AVM module floating `:latest` → AVM releases new validation rule,
  next deploy starts failing.
- No SBOM, so the customer's risk team can't answer "what's in this
  container?".
- ACR has `adminUserEnabled: true` from a scaffold default.
- A postprovision hook runs `azd ai skill create … --force`, silently
  deleting the skill versions that production agents are pinned to.
- The agent binds to a floating skill/tool default, so a capability
  change reaches every agent at once with no canary.

## Skill & tool artifacts (SUP-008 / SUP-009)

The skills and tools an agent calls are supply chain too — and they
change more often than the base image. Govern them as **versioned
Foundry artifacts**: author in Git, publish an immutable version,
reference it by a **pinned** `SkillVersion` / toolbox version, and
promote `default_version` in a **staged** rollout — never force-publish
over an existing version and never clone capability source at runtime.
Full lifecycle and remediation: [`../skill-tool-supply-chain.md`](../skill-tool-supply-chain.md).
This mirrors, for capabilities, the "pin the version, no `latest`"
discipline pillar 13 (`model-lifecycle`) applies to model deployments.

## MCP servers & tools (SUP-010 / SUP-011 / SUP-012 / SUP-013)

Model Context Protocol servers are executable supply chain that an agent calls
at runtime, and their **tool descriptors** (name, description, input schema) are
part of the prompt the model acts on — a silent change to a tool's description
is a supply-chain event, not a cosmetic one. Threadlight discovers MCP servers
from `.mcp.json`, any `mcpServers` / `servers` map, and remote MCP URLs in
source, then emits an **`mcp-sbom.json`**: kind, pinned version/digest, resolved
registry, and a SHA-256 of every tool's description and input schema.

Pin every server to an exact version or image digest (SUP-010) from a known
registry (SUP-011); commit an **`mcp-lock.json`** so any drift in a server
version, image digest, or **tool descriptor** is reviewed like a dependency bump
(SUP-012); and never inline credentials — inject them (SUP-013). Regenerate the
lock with `python3 scripts/mcp_sbom.py --root . --update-lock`. This is the same
"pin it, lock it, review the diff" discipline SUP-001..009 apply to images and
skills, extended to the MCP surface.

## Remediation

| Finding | Skill |
|---|---|
| Pin Dockerfile / image references | `azd-patterns` |
| Pin AVM modules | `azd-patterns`, `bicepschema` |
| Enable dependency scanning | (manual — `dependabot.yml`) |
| Emit SBOM | (manual — `syft` / GH action) |
| Publish skills/tools as pinned versions (no `--force`) | `foundry-skill-catalog`, `foundry-toolbox` |
| Pin MCP servers + commit `mcp-lock.json` | `foundry-toolbox`, `azd-patterns` |
| Inject MCP credentials (no inline secrets) | `foundry-toolbox`, Key Vault |

## Why this pillar matters

"It worked yesterday" → "what changed?" → "nothing was committed". The
silent change is the image base, the module version, or a transitive
dependency. Pinning makes the dependency surface auditable and the
"nothing changed" answer falsifiable.

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.

## Live-leg gap evidence (Task 7)

These findings are **advisory, tier-0** evidence propagated from the executable
threadlight-connect / threadlight-upgrade leg(s). production-ready reads each same-named finding from the leg's
shared-envelope manifest under `specs/`. Absent a fresh, **complete** leg
manifest they stay `not-verified` (verification debt) — an incomplete, stale,
or `aborted` leg never inflates this pillar's score or readiness, and a
`must-fix` in the leg's evidence dominates regardless of envelope freshness.

| ID | Verified when the leg reports it (fresh + complete) | Severity |
|---|---|---|
| `INT-001` | `threadlight-connect` proved the real integration tool conforms to the mock's data contract | `must-fix` |
| `INT-002` | `threadlight-connect` advanced the binding to `real-verified` (runtime endpoint no longer mock) | `must-fix` |
| `UPG-003` | `threadlight-upgrade` verified upgrade candidates against the official version source | `should-fix` |
