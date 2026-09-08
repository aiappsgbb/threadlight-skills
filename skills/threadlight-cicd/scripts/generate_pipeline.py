#!/usr/bin/env python3
"""threadlight-cicd — generate a prod-deploy CI/CD pipeline + environment-setup
runbooks for a Threadlight pilot's path to production.

Design rules (enforced by tests under ../tests/):

  * Deterministic + offline. No Azure calls, no network, no model tokens.
  * Secret-free. OIDC (GitHub) / Workload Identity Federation (Azure DevOps)
    only — never an AZURE_CREDENTIALS blob, client secret, or PAT.
  * Never assumes deploy rights. Environment setup is emitted as runbooks +
    ready-to-run `az` scripts the customer's central platform team executes.
  * Parallel-track safe. A pilot pipeline deploys ONLY use-case resources into
    its spoke/target resource group — never the Citadel hub, which is owned by
    the central platform team via citadel-hub-deploy in a separate repo.

The renderer uses the standard library for `{{TOKEN}}` markers from a framing
dict; optional AgentOps discovery uses the shared contract. Unknown markers are
left visible so gaps are obvious; the test-suite
asserts a fully-populated framing leaves zero surviving markers.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

VERSION = "0.4.0"

REF = Path(__file__).resolve().parent.parent / "references"

SUPPORTED_PLATFORMS = ("github-actions", "azure-devops")


def _eprint(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------------------
# region: onboarding-path decision gate
# ---------------------------------------------------------------------------
#
# Runs FIRST, before any artifact is generated. Asks whether a central platform
# environment (Citadel hub / shared AI gateway / shared networking / platform
# Key Vault) is required, then resolves one of three paths.

def resolve_onboarding_path(framing: dict) -> dict:
    """Resolve the onboarding path + posture + RBAC scope from framing.

    Branches:
      central_env_required & NOT exists -> hub-deploy-then-spoke
      central_env_required & exists     -> spoke-onboard
      not required                      -> standalone (validate first)
    """
    required = bool(framing.get("central_env_required"))
    exists = framing.get("central_env_exists")
    posture = framing.get("target_posture")
    warnings: list[str] = []

    if required:
        posture = "citadel-spoke"
        rbac_scope = "spoke-rg"  # invariant: a spoke pilot never gets hub scope
        needs_validation = False
        if exists:
            path = "spoke-onboard"
            next_actions = [
                "Confirm hub coordinates (hub_subscription_id, hub_apim_resource_id).",
                "Onboard this pilot as a spoke via citadel-spoke-onboarding (Access Contract).",
                "Scope the pilot deploy identity's RBAC to the spoke resource group ONLY.",
            ]
        else:
            path = "hub-deploy-then-spoke"
            warnings.append(
                "Central platform env is required but does not yet exist. The hub is "
                "stood up on the SEPARATE central-platform track — this pilot pipeline "
                "must not deploy it."
            )
            next_actions = [
                "Stand up the central platform via citadel-hub-deploy in the SEPARATE "
                "central repo/pipeline (platform team owns this).",
                "Then onboard this pilot as a spoke via citadel-spoke-onboarding (Access Contract).",
                "Scope the pilot deploy identity's RBAC to the spoke resource group ONLY.",
            ]
    else:
        path = "standalone"
        posture = posture or "standard-ai-gateway"
        rbac_scope = "target-rg"
        needs_validation = True
        next_actions = [
            "Validate the target subscription + resource group are correct.",
            "Confirm the pilot consumes no shared/central resources (if it does, set "
            "central_env_required=yes and re-run the gate).",
            "Confirm network exposure (public vs private endpoints) to pick the runner.",
        ]

    return {
        "path": path,
        "posture": posture,
        "central_env_required": required,
        "central_env_exists": exists,
        "rbac_scope": rbac_scope,
        "needs_validation": needs_validation,
        "next_actions": next_actions,
        "warnings": warnings,
    }

# endregion


# ---------------------------------------------------------------------------
# region: template rendering + context
# ---------------------------------------------------------------------------

def _render(text: str, context: dict) -> str:
    for k, v in context.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def _render_file(path: Path, context: dict) -> str:
    return _render(Path(path).read_text(encoding="utf-8"), context)


def build_context(framing: dict, resolved: dict) -> dict:
    platform = framing.get("platform", "github-actions")
    sub = framing.get("target_subscription_id", "")
    rg = framing.get("target_resource_group", "")
    loc = framing.get("target_location", "eastus2")
    tenant = framing.get("tenant_id", "")
    env_name = framing.get("env_name", "prod")
    private = bool(framing.get("private_network"))

    repo = framing.get("repo_full_name", "")
    repo_slug = repo.replace("/", "-") if repo else ""
    ado_org = framing.get("ado_org", "")
    ado_project = framing.get("ado_project", "")
    ado_sc = framing.get("ado_service_connection") or f"sc-{(repo_slug or 'pilot')}-{env_name}"

    slug = (repo_slug or "-".join(p for p in (ado_org, ado_project) if p) or "pilot").lower()
    uami_name = (framing.get("uami_name") or f"uami-{slug}-{env_name}-deploy").lower()
    uami_rg = framing.get("uami_resource_group", rg)
    uami_sub = framing.get("uami_subscription_id", sub)

    rbac_scope_id = f"/subscriptions/{sub}/resourceGroups/{rg}"
    rbac_role = framing.get("rbac_role", "Contributor")
    # Keyless Foundry azd templates assign data-plane roles to the app identity
    # during `azd provision` (Microsoft.Authorization/roleAssignments/write), which
    # Contributor cannot do. The deploy identity therefore also needs RBAC admin,
    # scoped to the SAME resource group (least privilege preserved).
    rbac_ra_admin_role = "Role Based Access Control Administrator"
    rbac_ra_admin_role_id = "f58310d9-a9f6-439a-9e8d-f62e7b41a168"

    path = resolved.get("path", "")
    hub_sub = framing.get("hub_subscription_id", "<hub-subscription-id>")
    hub_apim = framing.get("hub_apim_resource_id", "<hub-apim-resource-id>")
    access_contract = framing.get("access_contract_product", "<access-contract-product>")
    if path == "spoke-onboard":
        boundary_guidance = (
            "## Your onboarding path: the hub already exists\n\n"
            "Onboard this pilot as a **spoke** via `citadel-spoke-onboarding` — consume the "
            "hub through an Access Contract. **Do not run citadel-hub-deploy**: the central "
            "hub already exists and is owned by the platform team.\n\n"
            f"- Access Contract product: `{access_contract}`\n"
            f"- Hub APIM resource id: `{hub_apim}`\n"
            f"- Hub subscription: `{hub_sub}`\n"
        )
    elif path == "hub-deploy-then-spoke":
        boundary_guidance = (
            "## Your onboarding path: the hub is required but not yet deployed\n\n"
            "The central hub does **not yet** exist. The platform team stands it up via "
            "`citadel-hub-deploy` on the **separate** central-platform track first; then "
            "onboard this pilot via `citadel-spoke-onboarding`. This pilot pipeline never "
            "deploys the hub.\n"
        )
    else:  # standalone
        boundary_guidance = (
            "## Your onboarding path: standalone\n\n"
            "This pilot is **standalone** — there is no central hub to consume. Before "
            "go-live, validate it uses no shared/central resources; if it does, set "
            "`central_env_required=yes` and re-run the gate.\n"
        )

    # Runner wiring — public hosted vs private self-hosted / managed pool.
    runner_runs_on = "[ self-hosted, threadlight-prod ]" if private else "ubuntu-latest"
    ado_pool_name = framing.get("ado_pool_name") or "threadlight-prod-pool"
    ado_pool_spec = f"name: {ado_pool_name}" if private else "vmImage: ubuntu-latest"

    next_actions_md = "\n".join(f"- {a}" for a in resolved.get("next_actions", []))

    # CI/CD eval + red-team gate mode (CAF: standardized evaluation + AI red
    # teaming integrated into CI/CD). soft = warn-only; hard = block on a
    # non-pass verdict. Soft is the default so a first onboarding doesn't wedge
    # the pipeline before the legs have a baseline manifest.
    eval_gate_mode = str(framing.get("eval_gate", "soft")).lower()
    if eval_gate_mode not in ("soft", "hard"):
        eval_gate_mode = "soft"
    eval_gate_soft = "true" if eval_gate_mode == "soft" else "false"

    mcp_gate_mode = str(framing.get("mcp_gate", "soft")).lower()
    if mcp_gate_mode not in ("soft", "hard"):
        mcp_gate_mode = "soft"
    mcp_gate_soft = "true" if mcp_gate_mode == "soft" else "false"

    return {
        "GENERATOR_VERSION": VERSION,
        "ISO_TIMESTAMP": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "PLATFORM": platform,
        "TARGET_SUBSCRIPTION_ID": sub,
        "TARGET_RESOURCE_GROUP": rg,
        "TARGET_LOCATION": loc,
        "TARGET_POSTURE": resolved.get("posture", ""),
        "POSTURE": resolved.get("posture", ""),
        "ONBOARDING_PATH": resolved.get("path", ""),
        "TENANT_ID": tenant,
        "ENV_NAME": env_name,
        "REPO_FULL_NAME": repo,
        "REPO_SLUG": repo_slug,
        "ADO_ORG": ado_org,
        "ADO_PROJECT": ado_project,
        "ADO_SERVICE_CONNECTION": ado_sc,
        "UAMI_NAME": uami_name,
        "UAMI_RESOURCE_GROUP": uami_rg,
        "UAMI_SUBSCRIPTION_ID": uami_sub,
        # Public identifier (NOT a secret). Filled from env-setup step 1 output.
        "AZURE_CLIENT_ID": framing.get("azure_client_id", "REPLACE_WITH_UAMI_CLIENT_ID"),
        "RBAC_SCOPE": resolved.get("rbac_scope", ""),
        "RBAC_SCOPE_ID": rbac_scope_id,
        "RBAC_ROLE": rbac_role,
        "RBAC_RA_ADMIN_ROLE": rbac_ra_admin_role,
        "RBAC_RA_ADMIN_ROLE_ID": rbac_ra_admin_role_id,
        "BOUNDARY_PATH_GUIDANCE": boundary_guidance,
        "RUNNER_RUNS_ON": runner_runs_on,
        "ADO_POOL_SPEC": ado_pool_spec,
        "ADO_POOL_NAME": ado_pool_name,
        "PRIVATE_NETWORK": "yes" if private else "no",
        "HUB_SUBSCRIPTION_ID": framing.get("hub_subscription_id", "<hub-subscription-id>"),
        "HUB_APIM_RESOURCE_ID": framing.get("hub_apim_resource_id", "<hub-apim-resource-id>"),
        "ACCESS_CONTRACT_PRODUCT": framing.get("access_contract_product", "<access-contract-product>"),
        "FED_SUBJECT_ENV": f"repo:{repo}:environment:{env_name}",
        "FED_SUBJECT_MAIN": f"repo:{repo}:ref:refs/heads/main",
        "FED_SUBJECT_ADO": f"sc://{ado_org}/{ado_project}/{ado_sc}",
        "NEXT_ACTIONS": next_actions_md,
        "EVAL_GATE_MODE": eval_gate_mode,
        "EVAL_GATE_SOFT": eval_gate_soft,
        "MCP_GATE_MODE": mcp_gate_mode,
        "MCP_GATE_SOFT": mcp_gate_soft,
    }

# endregion


# ---------------------------------------------------------------------------
# region: generation
# ---------------------------------------------------------------------------

def _write(dest: Path, content: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest


def _agentops_options(framing: dict, out_root: Path) -> tuple[bool, bool, str | None]:
    mode = framing.get("agentops", "auto")
    if mode not in ("auto", "off"):
        raise ValueError("agentops must be auto or off")
    refresh = framing.get("agentops_refresh_doctor", False)
    if type(refresh) is not bool:
        raise ValueError("agentops_refresh_doctor must be an explicit boolean")
    schedule = framing.get("agentops_doctor_schedule")
    if schedule and (not refresh or mode == "off"):
        raise ValueError("agentops doctor schedule requires explicit doctor refresh opt-in")
    if schedule and (
        not isinstance(schedule, str)
        or len(schedule.split()) != 5
        or not re.fullmatch(r"[0-9*/,\- ]{9,100}", schedule)
    ):
        raise ValueError("agentops doctor schedule must be a five-field numeric cron")
    if mode == "off":
        return False, False, None
    if not out_root.exists():
        return False, False, None
    repo = Path(__file__).resolve().parents[3]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from skills._shared.agentops import discover_opted_in_agents
    return bool(discover_opted_in_agents(out_root)), refresh, schedule


def _agentops_command(platform: str, refresh: bool) -> str:
    runtime = "python3 .threadlight/skills/threadlight-cicd/scripts/agentops_runtime.py --repo ."
    event = '${GITHUB_EVENT_NAME:-}' if platform == "github-actions" else '${BUILD_REASON:-}'
    scheduled = "schedule" if platform == "github-actions" else "Schedule"
    pull_request = "pull_request" if platform == "github-actions" else "PullRequest"
    lines = ["set -euo pipefail", "set +x", "unset GITHUB_STEP_SUMMARY", "umask 077",
             "native_eval_status=0", "native_doctor_status=0"]
    lines += [f'if [ "{event}" != "{scheduled}" ]; then',
              f"  {runtime} --run-eval || native_eval_status=$?",
              '  if [ "$native_eval_status" -ne 0 ] && [ "$native_eval_status" -ne 2 ]; then',
              '    exit "$native_eval_status"', "  fi",
              "fi"]
    if refresh:
        lines += [f'if [ "{event}" != "{pull_request}" ]; then',
                  f"  {runtime} --refresh-doctor || native_doctor_status=$?",
                  '  if [ "$native_doctor_status" -ne 0 ] && [ "$native_doctor_status" -ne 2 ]; then',
                  '    exit "$native_doctor_status"', "  fi", "fi"]
    lines += [f'if [ "{event}" != "{scheduled}" ]; then',
              "  python3 .threadlight/skills/threadlight-evals/scripts/evals_check.py --target . --emit > /dev/null",
              "fi",
              'if [ "$native_doctor_status" -ne 0 ]; then exit "$native_doctor_status"; fi',
              'if [ "$native_eval_status" -eq 2 ]; then',
              '  echo "Native threshold gate returned 2; preserved in observed evidence and enforced by the canonical eval gate."',
              "fi"]
    return "\n".join(lines)


def _compose_agentops(text: str, platform: str, ctx: dict,
                      refresh: bool, schedule: str | None) -> str:
    command = _agentops_command(platform, refresh)
    if platform == "github-actions":
        events = "  pull_request:\n    branches: [ main ]\n"
        if schedule:
            events += f"  schedule:\n    - cron: '{schedule}'\n"
        text = text.replace("on:\n", "on:\n" + events, 1)
        text = text.replace("  deploy:\n", "  deploy:\n"
                            "    if: github.event_name != 'pull_request' && github.event_name != 'schedule'\n", 1)
        text = text.replace("  eval-gate:\n    needs: deploy\n",
                            "  eval-gate:\n    needs: deploy\n"
                            "    if: >-\n"
                            "      always() && !cancelled() &&\n"
                            "      (needs.deploy.result == 'success' ||\n"
                            "       (github.event_name == 'pull_request' &&\n"
                            "        github.event.pull_request.head.repo.full_name == github.repository) ||\n"
                            "       github.event_name == 'schedule')\n", 1)
        replacement = (
            "      - name: AgentOps bound eval / approved Doctor (existing application scope)\n"
            "        shell: bash\n"
            "        run: |\n" +
            "\n".join("          " + line for line in command.splitlines()) + "\n\n"
        )
        text, count = re.subn(
            r"      - name: Run quality evals \(threadlight-evals Discover leg\)\n.*?(?=      - name: Enforce eval verdict)",
            lambda _: replacement, text, count=1, flags=re.S,
        )
        text = text.replace(
            "      - name: Enforce eval verdict (mode=",
            "      - if: github.event_name != 'schedule'\n"
            "        name: Enforce eval verdict (mode=", 1,
        )
    else:
        events = "pr:\n  branches:\n    include: [ main ]\n\n"
        if schedule:
            events += (f"schedules:\n  - cron: '{schedule}'\n"
                       "    displayName: Owner-approved AgentOps Doctor\n"
                       "    branches:\n      include: [ main ]\n    always: true\n\n")
        text = text.replace("pool:\n", events + "pool:\n", 1)
        text = text.replace("  - stage: deploy\n", "  - stage: deploy\n"
                            "    condition: and(succeeded(), ne(variables['Build.Reason'], 'PullRequest'), "
                            "ne(variables['Build.Reason'], 'Schedule'))\n", 1)
        text = text.replace("  - stage: eval_gate\n", "  - stage: eval_gate\n"
                            "    condition: and(not(canceled()), or(eq(dependencies.deploy.result, 'Succeeded'), "
                            "eq(variables['Build.Reason'], 'PullRequest'), eq(variables['Build.Reason'], 'Schedule')))\n", 1)
        # Deployment jobs, unlike ordinary jobs, honor the existing environment's
        # approval checks before the federated task can invoke application APIs.
        text = text.replace("      - job: quality_evals\n        steps:\n",
                            f"      - deployment: quality_evals\n        environment: {ctx['ENV_NAME']}\n"
                            "        strategy:\n          runOnce:\n            deploy:\n              steps:\n", 1)
        start = text.index("      - deployment: quality_evals")
        end = text.index("\n  - stage: red_team_gate", start)
        section = text[start:end]
        lines = section.splitlines()
        steps_index = next(i for i, line in enumerate(lines) if line.strip() == "steps:")
        lines[steps_index + 1:] = ["      " + line if line else line for line in lines[steps_index + 1:]]
        section = "\n".join(lines) + "\n"
        replacement = (
            "                - task: AzureCLI@2\n"
            "                  displayName: AgentOps bound eval / approved Doctor (existing application scope)\n"
            "                  inputs:\n"
            f"                    azureSubscription: {ctx['ADO_SERVICE_CONNECTION']}\n"
            "                    scriptType: bash\n"
            "                    scriptLocation: inlineScript\n"
            "                    visibleAzLogin: false\n"
            "                    inlineScript: |\n" +
            "\n".join("                      " + line for line in command.splitlines()) + "\n"
        )
        section, count = re.subn(
            r"                - task: AzureCLI@2\n                  displayName: Run quality evals.*?"
            r"(?=                - task: AzureCLI@2\n                  displayName: Enforce eval verdict)",
            lambda _: replacement, section, count=1, flags=re.S,
        )
        section = section.replace(
            "                  displayName: Enforce eval verdict (mode=",
            "                  condition: and(succeeded(), ne(variables['Build.Reason'], 'Schedule'))\n"
            "                  displayName: Enforce eval verdict (mode=", 1,
        )
        text = text[:start] + section + text[end:]
    if count != 1:
        raise ValueError("AgentOps composition requires the existing canonical eval gate")
    return text


def _agentops_tooling() -> dict[Path, str]:
    """Copyable consumers retain the catalog's relative import layout."""
    skills = Path(__file__).resolve().parents[2]
    paths = [
        skills / "_shared/agentops.py",
        skills / "_shared/manifest.py",
        skills / "threadlight-agentops/scripts/agentops_check.py",
        skills / "threadlight-agentops/scripts/native_observer.py",
        skills / "threadlight-cicd/scripts/agentops_runtime.py",
        skills / "threadlight-evals/scripts/evals_check.py",
    ]
    result = {}
    for path in paths:
        if not path.is_file():
            raise ValueError(f"AgentOps runtime prerequisite missing from this installation: {path.name}")
        result[Path(".threadlight/skills") / path.relative_to(skills)] = path.read_text(encoding="utf-8")
    for path in (skills / "threadlight-agentops/references").glob("*.json"):
        result[Path(".threadlight/skills") / path.relative_to(skills)] = path.read_text(encoding="utf-8")
    return result


def generate(framing: dict, out_root) -> list:
    """Render the pipeline + env-setup runbooks into out_root. Returns paths."""
    out_root = Path(out_root)
    platform = framing.get("platform", "github-actions")
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported platform {platform!r}; choose one of {SUPPORTED_PLATFORMS}")

    resolved = resolve_onboarding_path(framing)
    ctx = build_context(framing, resolved)
    agentops, refresh_doctor, doctor_schedule = _agentops_options(framing, out_root)
    tooling = _agentops_tooling() if agentops else {}
    env_dir = out_root / "docs" / "threadlight-cicd" / "env-setup"
    written: list[Path] = []

    # 1. Pipeline (platform-specific)
    template = "github-actions/azd-deploy-prod.yml.tmpl" if platform == "github-actions" else "azure-devops/azure-pipelines.yml.tmpl"
    pipeline = _render_file(REF / template, ctx)
    if agentops:
        pipeline = _compose_agentops(pipeline, platform, ctx, refresh_doctor, doctor_schedule)
    if platform == "github-actions":
        written.append(_write(
            out_root / ".github" / "workflows" / "azd-deploy-prod.yml",
            pipeline,
        ))
    else:
        written.append(_write(
            out_root / "azure-pipelines.yml",
            pipeline,
        ))

    # 2. Env-setup step 1 — UAMI + federated credentials (platform-specific)
    written.append(_write(
        env_dir / "01-uami-federated-credentials.md",
        _render_file(REF / "env-setup" / f"01-uami-federated-credentials.{platform}.md.tmpl", ctx),
    ))
    written.append(_write(
        env_dir / "01-uami-federated-credentials.sh",
        _render_file(REF / "env-setup" / f"01-uami-federated-credentials.{platform}.sh.tmpl", ctx),
    ))

    # 3. Env-setup steps 2 + 3 — RBAC + runners (shared)
    for stem in ("02-rbac-role-assignments", "03-runners-private-vnet"):
        written.append(_write(env_dir / f"{stem}.md", _render_file(REF / "env-setup" / f"{stem}.md.tmpl", ctx)))
        written.append(_write(env_dir / f"{stem}.sh", _render_file(REF / "env-setup" / f"{stem}.sh.tmpl", ctx)))

    # 4. Env-setup README (index of what to hand to whom)
    written.append(_write(env_dir / "README.md", _render_file(REF / "env-setup" / "README.md.tmpl", ctx)))

    # 5. Central-platform boundary (the must-tell)
    written.append(_write(
        out_root / "docs" / "threadlight-cicd" / "central-platform-boundary.md",
        _render_file(REF / "central-platform-boundary.md.tmpl", ctx),
    ))

    # 6. Auditable onboarding decision record
    record = dict(resolved)
    record["generator_version"] = VERSION
    record["platform"] = platform
    written.append(_write(
        out_root / "docs" / "threadlight-cicd" / "onboarding-path.json",
        json.dumps(record, indent=2) + "\n",
    ))

    for relative, content in tooling.items():
        written.append(_write(out_root / relative, content))
    if agentops:
        written.append(_write(
            out_root / "docs/threadlight-cicd/agentops-runtime.md",
            (REF / "agentops-runtime.md").read_text(encoding="utf-8"),
        ))
    return written

# endregion


# ---------------------------------------------------------------------------
# region: CLI
# ---------------------------------------------------------------------------

def _detect_repo_full_name(cwd: str):
    try:
        r = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", r.stdout.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _prompt(text, default=None):
    suffix = f" [{default}]" if default is not None else ""
    val = input(f"{text}{suffix}: ").strip()
    return val or (default or "")


def _onboard(framing: dict) -> dict:
    """Interactive onboarding-path gate. Mutates + returns framing."""
    print("\n=== threadlight-cicd onboarding-path gate ===\n")
    plat = _prompt("CI/CD platform (github-actions|azure-devops)", framing.get("platform", "github-actions"))
    framing["platform"] = plat

    req = _prompt("Is a central platform environment required? (Citadel hub / shared "
                  "AI gateway / shared networking / platform Key Vault) (yes|no)",
                  "no").lower().startswith("y")
    framing["central_env_required"] = req
    if req:
        framing["central_env_exists"] = _prompt(
            "Does that central platform environment already exist? (yes|no)", "no"
        ).lower().startswith("y")
        if not framing["central_env_exists"]:
            print("\n>> The central platform must be stood up on the SEPARATE central-platform "
                  "track (citadel-hub-deploy), not by this pilot pipeline. This generator will "
                  "still scaffold the spoke pipeline; coordinate hub deploy with the platform team.\n")
        framing["hub_subscription_id"] = _prompt("Hub subscription id (optional now)",
                                                 framing.get("hub_subscription_id", ""))
        framing["access_contract_product"] = _prompt("Citadel Access Contract product (optional now)",
                                                      framing.get("access_contract_product", ""))
    else:
        print("\n>> Standalone path — double-checking onboarding details before generating.\n")
        framing.setdefault("target_posture", _prompt(
            "Posture (standard-ai-gateway|agt|direct)", "standard-ai-gateway"))

    framing["target_subscription_id"] = _prompt("Target subscription id",
                                                framing.get("target_subscription_id", ""))
    framing["target_resource_group"] = _prompt("Target resource group",
                                               framing.get("target_resource_group", ""))
    framing["target_location"] = _prompt("Target location", framing.get("target_location", "eastus2"))
    framing["tenant_id"] = _prompt("Azure tenant id", framing.get("tenant_id", ""))
    framing["env_name"] = _prompt("azd environment name", framing.get("env_name", "prod"))
    priv = _prompt("Is the deployment target on a private VNet (private endpoints)? (yes|no)",
                   "no").lower().startswith("y")
    framing["private_network"] = priv

    if plat == "github-actions":
        framing["repo_full_name"] = _prompt("GitHub repo (owner/repo)",
                                            framing.get("repo_full_name", "") or
                                            (_detect_repo_full_name(os.getcwd()) or ""))
    else:
        framing["ado_org"] = _prompt("Azure DevOps organization", framing.get("ado_org", ""))
        framing["ado_project"] = _prompt("Azure DevOps project", framing.get("ado_project", ""))
        framing["ado_service_connection"] = _prompt(
            "Azure DevOps service connection name (WIF)",
            framing.get("ado_service_connection", ""))
    return framing


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="generate_pipeline.py",
        description="Generate a prod-deploy CI/CD pipeline + env-setup runbooks for a Threadlight pilot.",
    )
    p.add_argument("--onboard", action="store_true",
                   help="Run the interactive onboarding-path gate.")
    p.add_argument("--framing-file", help="JSON framing file (skips interactive prompts).")
    p.add_argument("--platform", choices=SUPPORTED_PLATFORMS, default="github-actions")
    p.add_argument("--central-env-required", choices=["yes", "no"])
    p.add_argument("--central-env-exists", choices=["yes", "no"])
    p.add_argument("--target-posture", choices=["citadel-spoke", "standard-ai-gateway", "agt", "hybrid", "direct"])
    p.add_argument("--private-network", action="store_true")
    p.add_argument("--repo-full-name")
    p.add_argument("--ado-org")
    p.add_argument("--ado-project")
    p.add_argument("--ado-service-connection")
    p.add_argument("--target-sub", dest="target_subscription_id")
    p.add_argument("--target-rg", dest="target_resource_group")
    p.add_argument("--target-location")
    p.add_argument("--tenant-id")
    p.add_argument("--hub-sub", dest="hub_subscription_id")
    p.add_argument("--hub-apim-id", dest="hub_apim_resource_id")
    p.add_argument("--access-contract-product", dest="access_contract_product",
                   help="Citadel Access Contract product the spoke consumes (e.g. unified-ai).")
    p.add_argument("--ado-pool-name", dest="ado_pool_name",
                   help="Managed DevOps Pool / self-hosted pool name for private-network runs.")
    p.add_argument("--env-name", default="prod")
    p.add_argument("--eval-gate", choices=["soft", "hard"], default=None,
                   help="CI/CD eval + red-team gate mode: soft (warn-only, default) "
                        "or hard (block the pipeline on a non-pass verdict).")
    p.add_argument("--mcp-gate", choices=["soft", "hard"], default=None,
                   help="CI/CD MCP supply-chain gate mode: soft (warn-only, "
                        "default) or hard (block the pipeline on any must-fix "
                        "MCP finding).")
    p.add_argument("--agentops", choices=["auto", "off"], default=None,
                   help="Compose AgentOps for discovered opt-in roots (auto, default), or leave pipelines unchanged (off).")
    p.add_argument("--agentops-refresh-doctor", action="store_true", default=None,
                   help="Opt in to post-deploy Doctor; runtime still requires scoped owner approval.")
    p.add_argument("--agentops-doctor-schedule", default=None,
                   help="Optional five-field cron for Doctor only; requires --agentops-refresh-doctor and runtime approval.")
    p.add_argument("--out", default=os.getcwd(), help="Output root (default: cwd).")
    return p.parse_args(argv)


def _framing_from_args(args) -> dict:
    framing: dict = {}
    if args.framing_file:
        framing.update(json.loads(Path(args.framing_file).read_text()))
    cli = {
        "platform": args.platform,
        "target_posture": args.target_posture,
        "repo_full_name": args.repo_full_name,
        "ado_org": args.ado_org,
        "ado_project": args.ado_project,
        "ado_service_connection": args.ado_service_connection,
        "target_subscription_id": args.target_subscription_id,
        "target_resource_group": args.target_resource_group,
        "target_location": args.target_location,
        "tenant_id": args.tenant_id,
        "hub_subscription_id": args.hub_subscription_id,
        "hub_apim_resource_id": args.hub_apim_resource_id,
        "access_contract_product": args.access_contract_product,
        "ado_pool_name": args.ado_pool_name,
        "env_name": args.env_name,
        "eval_gate": args.eval_gate,
        "mcp_gate": args.mcp_gate,
        "agentops": args.agentops,
        "agentops_refresh_doctor": args.agentops_refresh_doctor,
        "agentops_doctor_schedule": args.agentops_doctor_schedule,
    }
    for k, v in cli.items():
        if v is not None:
            framing[k] = v
    if args.private_network:
        framing["private_network"] = True
    if args.central_env_required is not None:
        framing["central_env_required"] = args.central_env_required == "yes"
    if args.central_env_exists is not None:
        framing["central_env_exists"] = args.central_env_exists == "yes"
    return framing


def main(argv=None):
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    framing = _framing_from_args(args)

    if args.onboard:
        framing = _onboard(framing)

    if not framing.get("repo_full_name") and framing.get("platform", "github-actions") == "github-actions":
        detected = _detect_repo_full_name(os.getcwd())
        if detected:
            framing["repo_full_name"] = detected

    resolved = resolve_onboarding_path(framing)
    if resolved["needs_validation"]:
        _eprint("[standalone] Validate target sub/RG, shared-resource usage, and network "
                "exposure before this pipeline goes live. See central-platform-boundary.md.")
    for w in resolved.get("warnings", []):
        _eprint(f"[warn] {w}")

    written = generate(framing, out_root=args.out)
    _eprint(f"\nResolved onboarding path: {resolved['path']} (posture={resolved['posture']}, "
            f"rbac_scope={resolved['rbac_scope']})")
    for a in resolved["next_actions"]:
        _eprint(f"  next: {a}")
    _eprint(f"\nWrote {len(written)} file(s):")
    for p in written:
        _eprint(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
