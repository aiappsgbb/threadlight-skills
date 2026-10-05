# Hosted-agent references (Threadlight-owned)

**Authority.** For `azd` deployment of Foundry hosted agents, the official
[microsoft-foundry deploy guidance](https://github.com/microsoft/azure-skills/blob/v1.2.77/.github/plugins/azure-skills/skills/microsoft-foundry/foundry-agent/deploy/deploy.md)
in `azure@azure-skills` (pinned in `skills/_shared/official-skills-lock.json`)
is the default. Load `microsoft-foundry` for it.

These Threadlight-owned references cover only what that guidance does not
(recorded as a Q4 gap in `skills/_shared/skill-dependencies.json`):

| Reference | Covers |
|---|---|
| [maf/](maf/README.md) | Microsoft Agent Framework runtime: native-SDK create-definition, env-var contract, `create_version` dedup trap, capability-host gating, ResponsesHostServer container, preflight, operation recovery, rollout |
| [ghcp/](ghcp/README.md) | GitHub Copilot SDK Invocations container template (the official `azure-hosted-copilot-sdk` skill was retired) |

Both were ported from `aiappsgbb/awesome-gbb@7f1de882` (MIT); see each
`PROVENANCE.md`.
