# Skill & tool supply-chain — governed Foundry artifacts

> **Pillar:** `supply-chain` (9). **Checks:** `SUP-008`, `SUP-009`.
> **Companion skills:** [`foundry-skill-catalog`](https://github.com/aiappsgbb/awesome-gbb), [`foundry-toolbox`](https://github.com/aiappsgbb/awesome-gbb).

The container image, the Bicep modules, and the Python dependencies are not
the only things an agent depends on. The **skills** (reusable capability
packages) and **tools** (functions, MCP servers, toolboxes) it calls are part
of its supply chain too — and they change far more often than the base image.
Treat them the same way you treat images: **publish once, pin by version,
promote deliberately.**

## The lifecycle

```
author in Git  →  publish immutable version  →  reference by pinned version  →  staged promotion  →  download at deploy
   (source)         (Foundry artifact)             (SkillVersion / toolbox)        (default_version)     (no image rebuild)
```

1. **Author in Git.** A skill/tool lives in source control — reviewed,
   diffable, tested. This is the editable copy.
2. **Publish an immutable version.** Promote the reviewed source into a
   **versioned Foundry artifact** (a `SkillVersion`, a toolbox version). Once
   published, that version is immutable — a given version always resolves to
   the same bytes.
3. **Reference by pinned version.** Production agents bind to a **specific
   version**, never to a floating pointer. A pinned version is auditable and
   reproducible: "which capabilities ran on the day of the incident?" has a
   single answer.
4. **Promote `default_version` in a staged rollout.** New versions ship behind
   a staged promotion of the `default_version` pointer — canary first, then
   broad — so a bad capability change is caught before it reaches every agent.
5. **Download at deploy, not at runtime.** The pinned artifact is fetched at
   deploy time (or as an init step), so the running container never has to
   clone source or rebuild an image to pick up a capability.

## Anti-patterns (what the checks catch)

| Anti-pattern | Why it hurts | Check |
|---|---|---|
| **Force-publishing** a skill/tool (`--force` / `--overwrite` on a create/publish command) | Deletes prior immutable versions, so consumers pinned to an old version break silently | `SUP-008` |
| **Floating capability version** in production (no pinned `SkillVersion` / toolbox version) | "Nothing was committed" but behaviour changed — the capability moved under you | `SUP-009` |
| **Vendoring at runtime** (cloning skill/tool source inside the container) | The image and the capability drift apart; the deploy is no longer reproducible | prose (design smell) |

## How `threadlight-production-ready` checks this

* **`SUP-008` — no force-publish in committed automation.** Scans
  `azure.yaml` hooks, `.github/workflows/**`, and shell/PowerShell scripts for
  a skill/tool create/publish command carrying `--force` / `--overwrite`.
  `should-fix` if found. Remediation: publish a **new** version and promote
  `default_version` — never overwrite an existing one.
* **`SUP-009` — pin skill/tool versions for production.** If the repo consumes
  agent skills/tools (references a toolbox, `azd ai skill`, an MCP plugin, or a
  `skills/**/SKILL.md`) but declares no pinned version, it is `should-fix`.
  `not-applicable` when the repo uses no skills/tools. Remediation: declare an
  immutable `SkillVersion` / toolbox version and promote `default_version` in a
  staged rollout.

Both are **soft-advisory, static, tier-0** checks — they never fail a build.

## See also

* Parent skill: [`../../SKILL.md`](../../SKILL.md) — the 13-pillar hand-off flow.
* Pillar 9 reference: [`pillars/09-supply-chain.md`](pillars/09-supply-chain.md).
* Pillar 13 (`model-lifecycle`) applies the same "pin the version, no `latest`"
  discipline to **model deployments**; this doc applies it to **capabilities**.
