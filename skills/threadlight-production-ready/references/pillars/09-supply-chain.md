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
