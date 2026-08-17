---
schema_version: 1
purpose: non_interactive_cli_contract
applies_to: threadlight-deploy
captured_from:
  azd_version: "1.31.1"
  extension: azure.ai.agents
  extension_version: "1.0.0-beta.10"
  captured_at: 2026-08-16
  method: |
    `--help` output of the pinned toolchain, installed into an isolated
    AZD_CONFIG_DIR. Not transcribed from memory or from upstream docs.
---

# `azd ai agent` — the non-interactive contract

**Read this before running any `azd ai agent` command in an automated or
CI context.** The parent skill describes the *artifacts* to produce
(`agent.yaml`, `azure.yaml`, Bicep). This file pins the *command surface*
that produces them.

## Why this file exists

The 2026-08-16 E2E run showed the agent spending the whole Phase 3 budget on
discovery rather than deployment: 14 web fetches (4 of them 404), 9 `azd up`
invocations, and its own narration saying it was *"pulling the upstream agent
manifest shape so I can match the extension's schema instead of guessing it"*
and *"the first scaffold attempt only failed on the runtime flag; I'm retrying
with a supported Python version."*

Two things caused that:

1. The skill documents the interactive path. `--no-prompt` **fails** if any
   required value cannot be resolved automatically, so automation needs flags
   the interactive path never mentions.
2. The fallback — fetching shapes from `raw.githubusercontent.com` at deploy
   time — now returns 404, because upstream moved. A deploy path that depends
   on live internet lookups is not a deploy path you can ship.

Pinning the shapes here removes both. Keep it in sync with the toolchain pin in
`upstream-pin.md` § 1b; both describe the same pinned versions.

---

## 1. Command surface (verified, not assumed)

`azd ai agent <command>` at extension `1.0.0-beta.10`:

| Command | Purpose |
|---------|---------|
| `init` | Initialize a new AI agent project |
| `doctor` | Diagnose problems with an azd ai agent project |
| `show` | Show the status of a hosted agent |
| `invoke` | Send a message to your agent |
| `run` | Run your agent locally for development |
| `monitor` | Monitor logs from a hosted agent |
| `eval` | Create and run quick evals for an agent |
| `delete` | Delete a hosted agent |
| `code` | Manage agent source code |
| `endpoint` | Manage agent endpoint and card configuration |
| `files` | Manage files in a hosted agent session |
| `sessions` | Manage sessions for a hosted agent endpoint |
| `optimize` | Evaluate and optimize AI agents |
| `sample` | Browse the curated catalog of agent samples |
| `pack` / `publish` | Teams app package / publish (activity agents) |
| `version` | Print the extension version |

### ⚠️ `azd ai agent validate` does not exist

There is no `validate` subcommand. Invoking it fails with
`ERROR: unknown command "validate" for "agent"`.

**Use `azd ai agent doctor` instead.** It runs local and remote checks against
the current project and reports each one:

| Exit code | Meaning |
|-----------|---------|
| `0` | at least one check passed and no checks failed |
| `1` | any check failed |
| `2` | all checks were skipped (preconditions unmet) |

Note `2`: "everything skipped" is **not** success. A CI gate that only tests
`$? -eq 0` is correct here, but a gate written as `!= 1` would silently pass a
run where nothing was actually checked.

```bash
azd ai agent doctor                # full suite
azd ai agent doctor --local-only   # skip network checks — offline/proxy/fast triage
azd ai agent doctor --unredacted   # show raw principal IDs and scopes
```

---

## 2. `init` — the flags automation needs

`--no-prompt` fails rather than guessing, so every value it cannot resolve must
be supplied explicitly. These are the flags that decide the shape of everything
downstream:

| Flag | Why it matters |
|------|----------------|
| `--deploy-mode` | `code` = ZIP upload, **no ACR, no AcrPull**. `container` = Docker image, needs ACR **and** an AcrPull grant. Defaults to `code` for Python/.NET under `--no-prompt`. |
| `--runtime` | Required with `--deploy-mode code --no-prompt`. e.g. `python_3_13`, `python_3_14`, `dotnet_10`. **Guessing this is the single most common first failure** — an unsupported value fails the scaffold outright. |
| `--entry-point` | Required with `--deploy-mode code --no-prompt`. e.g. `app.py`, `MyAgent.dll`. |
| `--project-id` | Existing Foundry project to bind the azd environment to. |
| `--agent-name` | Foundry agent identity written to `agent.yaml`. **Reusing a name creates a new version of that agent**, not a separate agent. |
| `--dep-resolution` | `remote_build` (default) or `bundled`. |
| `--image` | Pre-built image. Skips template/language selection, Dockerfile generation **and ACR setup**. Incompatible with `--deploy-mode code`. |
| `--model` / `--model-deployment` | `--model-deployment` takes precedence when both are given. |
| `--infra` | Ejects IaC from `azure.yaml`. Bare `--infra` ejects Bicep; `--infra=terraform` ejects Terraform. |
| `--force` | Required together with `--no-prompt` when init would otherwise need confirmation. |

### The deploy-mode decision

This is the highest-consequence flag and the parent skill does not currently
name it. The two paths have materially different failure surfaces:

```
--deploy-mode code       →  ZIP upload      →  no registry  →  no AcrPull, no propagation race
--deploy-mode container  →  Docker image    →  ACR required →  AcrPull grant + F-05/F-06 race
```

The `azure.yaml` example in the parent skill (`language: docker`,
`docker.remoteBuild: true`) mandates the **container** path. If you take it,
budget for the RBAC propagation wait documented as F-05/F-06 (60–300s); an
image pull attempted within ~60s of the grant fails until it propagates.

Recorded as the open issue `deploy-mode-not-specified` in `upstream-pin.md`.
Do not silently switch the documented path — it changes what customers deploy.

---

## 3. `invoke` — shapes that matter for smoke tests

```bash
azd ai agent invoke "message"                  # agent name auto-detected from azure.yaml
azd ai agent invoke <agent-name> "message"     # two args: name, then message
azd ai agent invoke -f payload.json            # send file contents as the body
```

| Flag | Note |
|------|------|
| `-p, --protocol` | `responses` (default), `invocations`, or `a2a`. **`a2a` is remote-only** and cannot be combined with `--local`. |
| `-t, --timeout` | Seconds; default **1800**, `0` disables. The default is long — a hung invoke will not fail fast on its own. |
| `-l, --local` | Targets a local agent from `azd ai agent run` instead of Foundry. |
| `--new-session` / `--new-conversation` | Sessions persist per-agent and are reused across invokes; pass these to reset. **Relevant to evals** — a stale session silently carries context between what you think are independent runs. |
| `--agent-endpoint` | Invoke a deployed agent without an azd project; protocol inferred from the URL. |
| `--user-identity` | `x-agent-user-id` locally, `x-ms-user-identity` remotely. |

`azd ai agent show` supports `-o json` (also `table`, the default), which is
the reliable way to read the endpoint for scripted checks.

---

## 4. Refreshing this file

It is pinned to a beta extension that ships often. When bumping the pin in
`upstream-pin.md` § 1b, re-capture rather than hand-edit:

```bash
azd ai agent --help                # command surface — check for added/removed subcommands
azd ai agent init --help           # the flag table in § 2
azd ai agent invoke --help         # the flag table in § 3
azd ai agent doctor --help         # exit codes in § 1
```

Then cross-check that the repo does not reference a command that no longer
exists:

```bash
grep -rhoE "azd ai agent [a-z-]+" --include=*.md --include=*.yml --include=*.sh . \
  | sort -u
```

That check is what caught `validate` being gone from three places, including a
mandatory validation gate in the parent skill. Treat a mismatch as a must-fix:
the failure surfaces at deploy time, in front of a customer, as an unknown
command.
