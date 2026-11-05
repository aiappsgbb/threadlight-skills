# Threadlight Lifecycle Canvas

Threadlight Lifecycle Canvas is an optional GitHub Copilot App enhancement for the Threadlight skills plugin. Threadlight turns a business process into a working agent pilot on Microsoft Foundry, then carries it toward production with selected runtime controls and the evidence for each. The Canvas shows the process owner where that pilot stands, what is in the way, and the one next step, and sends that step to chat for confirmation.

## Requirements

- Minimum tested GitHub Copilot App version: **1.0.78-2**.
- Canvas/extensions are experimental and must be enabled in the Copilot App.
- Installing the plugin exposes this extension wherever the plugin is enabled.
- The next step comes from `threadlight-auto`'s plan. Commit it with
  `python <threadlight-skills>/skills/threadlight-auto/references/orchestrator.py --workspace . --commit`,
  which writes `.threadlight/auto-next.json`. Without a current plan the Canvas offers **Refresh the plan** instead of guessing.
- Non-Canvas hosts retain the normal skills and artifacts UX.

## What it shows

1. **Now**: the pilot name, its state in plain words, the one thing in the way, and one action named for what it does (for example "Run the post-deploy safety check"). Feedback appears next to the button; the state stays visible.
2. **The process**: from `specs/SPEC.md`: description, steps, business rules with the proof that can be derived (test scenarios that cite the rule, a governance manifest or contract that cites it, a human review point), human decisions and KPIs. A rule with none of these says "Not yet proven".
3. **Proof path**: the planner's stages in its own order, grouped into Design, Controls, Publish, Verify, Evaluate and Go-live. The root blocker is marked "You are here"; later stages say they are waiting on it once. States read as words with an icon: Done, Prepared offline, Proven live, Out of date, Blocked, Not needed, Not started.
4. **Go-live and value**: the production-readiness verdict (only "Ready to go live" when the report and its proof chain agree), the monthly cost estimate and cost per interaction from `specs/cost-manifest.json`, and when each was checked.
5. **Technical details** (collapsed, remembered for the session): the planner's reasons per stage, evidence files with their verdict, age and offline/live kind, optional checks, skills and any evidence problems.

An empty workspace shows **Describe your process** with an example brief.

## Security model

- The loopback server binds only to `127.0.0.1` and uses a per-instance capability token.
- Artifact reads use an allowlist that excludes `.env` and `.azure/**/.env`. `.threadlight/auto-next.json` is read, never written.
- To tell whether the plan is out of date, the Canvas compares `decided_at` with the modification times (metadata only, never contents) of the planner's fingerprint inputs: `src/`, `policies/`, `infra/`, `config/`, `azure.yaml`, `agent.yaml`, `tool-registry.json`, `pyproject.toml`, `requirements.txt`, `Dockerfile` and `.threadlight/governance-*.json`. Symlinks are not followed, `.env` files are skipped, tool caches and environments (`.venv`, `venv`, `node_modules`, `.terraform` and similar) are pruned, and `.azure/` is not scanned. A plan older than 24 hours is also out of date. If the bounded walk cannot see every input (too many files, or unreadable folders), the Canvas keeps the plan's next step but says the plan can't be confirmed as current and shows no "proven live" or "Ready to go live".
- The Canvas never spawns processes. Its actions send validated intents to chat only: `start_pilot`, `advance_plan` (a known `threadlight-auto` stage id), `refresh_plan` (asks chat to run the planner command above), `invoke_skill` (a registered skill id), `inspect_evidence`, `resume_phase` and `prepare_handoff`. Chat explains the step and runs nothing until the user confirms.
- Evidence is distrusted by default: malformed or forged manifests render as Blocked, offline governance inventory is never shown as proven live, and the legacy `specs/govern-manifest.json` is never read as governance proof.

## Troubleshooting

- If the panel does not appear, confirm Canvas/extensions are enabled and the plugin is installed.
- Inspect startup issues with `/extensions manage` or `extensions_manage inspect`.
- If the Canvas says the plan is out of date, use **Refresh the plan** after files change.

## Support

Use the repository issue tracker with the Copilot App version, plugin version, host OS, and output from `extensions_manage inspect` when available.

## Reporting security issues

Do not include secrets or customer data in reports. Share only sanitized artifact paths, error messages, and reproduction steps.
