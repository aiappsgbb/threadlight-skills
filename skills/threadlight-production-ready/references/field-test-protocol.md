# Field-test protocol for threadlight-production-ready

## Purpose

v0.4.0 + v0.5.0 ship against fixtures. Before v0.6.0 ships, the maintainer team
should run threadlight against 1-2 real awesome-gbb pilot customer repos and
fold friction points into the next CHANGELOG as "real-world hardening."

This doc is the recipe for a single pilot run. It is **aspirational** — it
exists so the v0.5.0 spec's promise of a field-test phase is kept honestly even
when no customer engagement has happened yet. Real customer execution is
post-release follow-up work and is tracked per pilot as an issue in
`aiappsgbb/threadlight-skills` (see "Output" below).

## Eligibility for pilot customers

Pick customers that meet ALL of:

1. Production-bound work currently underway (not pure prototype).
2. Customer team has signed an awesome-gbb engagement letter.
3. At least one repo with `infra/` + `.github/workflows/` present.
4. Customer security team has approved running an external assessor against
   the repo (read-only — assessor is read-only by the SACRED RULE; see
   `SKILL.md` for the documented `--scaffold-cicd` exception).

## Prerequisites

The assessor requires `tests/postdeploy-manifest.json` (the output of
`threadlight-safe-check --phase post-deploy`). Pilot customers will land in one
of three states:

- **Has a recent post-deploy manifest** — proceed.
- **Has run safe-check but the manifest is older than 24 hours** — pass
  `--accept-stale-safe-check` (every example below already does this; remove
  the flag once the manifest is fresh).
- **Has never run safe-check** — either run `threadlight-safe-check --phase
  post-deploy` first (the supported path), or hand-author a minimal
  `tests/postdeploy-manifest.json` with `phase: post-deploy` and matching
  `subscription_id` / `resource_group`. The hand-authored path is a real
  friction point — capture it in the pilot's friction-point issue.

The script is **flat argparse** (no `assess` / `frame` subcommands). Every
example below maps directly to flags documented in `SKILL.md` and `--help`.

## Procedure

### Phase 1: Read-only assessment

1. Clone the customer repo to a scratch worktree outside this repo.
2. From this repo, run the assessor against the customer worktree with live
   probes disabled (`--static`) so the run is purely repo-graph driven:

   ```bash
   python3 skills/threadlight-production-ready/scripts/production_ready.py \
       --root /path/to/customer/repo \
       --static \
       --in-postdeploy /path/to/customer/repo/tests/postdeploy-manifest.json \
       --out  ~/threadlight-pilot/<customer>/manifest.json \
       --report ~/threadlight-pilot/<customer>/report.md \
       --accept-stale-safe-check
   ```

3. Read `~/threadlight-pilot/<customer>/report.md`. Capture: any findings that
   surprise the customer team, any false positives, any missing categories.
   Diff a second run against the first with `--diff` to confirm the assessor
   is idempotent on the customer's tree.

### Phase 2: Framing wizard run

1. Run the framing wizard against a writable output path:

   ```bash
   python3 skills/threadlight-production-ready/scripts/production_ready.py \
       --onboard \
       --framing-file ~/threadlight-pilot/<customer>/framing.json
   ```

   The wizard walks the 8 questions defined in `FRAMING_QUESTIONS` (see
   `SKILL.md` § "Framing wizard questions"). Re-running with a populated
   `--framing-file` is the headless / CI-friendly path.

2. Walk through the 8 questions with a customer SRE present. Capture: any
   question that confused them, any question they couldn't answer without
   escalation, any answer that was obviously missing a field.

### Phase 3: Apply-plan dispatch (subset)

1. From the apply-plan generated in Phase 2, pick 2-3 low-risk recipes from
   the must-fix set. Good starter candidates are
   `references/remediation-recipes/SUP-101.md` and
   `references/remediation-recipes/SRE-103.md` — both are `kind: repo-edit`
   recipes whose agent-side action is to write a single stub file
   (`SUPPORT.md` at repo root, `docs/sre/runbook.md` respectively) with
   placeholder content for the customer to fill in.
2. Dispatch via the agent loop (the assessor does **not** apply patches
   itself — SACRED RULE: assessor reads, agent dispatches, customer approves
   PRs). Capture: dispatch latency, recipe clarity, whether the agent had to
   ask follow-up questions, whether the staleness check on
   `apply_plan["manifest_sha256"]` fired correctly.

   Stay within the 4 `kind` values used today (`repo-edit`, `sibling-skill`,
   `manual`, `deferred-to-pipeline`). If a pilot need doesn't fit, file an
   issue rather than introducing a new category.

### Phase 4: Customer-overrides dry-run

1. Identify any finding the customer team disputes.
2. Write a `customer-overrides.yaml` flipping it (`pass` → `fail` or
   `fail` → `pass`). The schema is documented in
   `references/customer-overrides-schema.md` and a worked example lives at
   `references/customer-overrides.example.yaml`. Status flips on
   `severity: must-fix` findings are rejected — the script exits 2 if you try
   (that bypass-rejection is the whole point of the feature).
3. Re-run the assessor with the override file applied:

   ```bash
   python3 skills/threadlight-production-ready/scripts/production_ready.py \
       --root /path/to/customer/repo \
       --static \
       --in-postdeploy /path/to/customer/repo/tests/postdeploy-manifest.json \
       --out  ~/threadlight-pilot/<customer>/manifest.override.json \
       --report ~/threadlight-pilot/<customer>/report.override.md \
       --customer-overrides /path/to/customer-overrides.yaml \
       --accept-stale-safe-check
   ```

4. Confirm the override applies and the audit-trail fields
   (`override_customer`, `override_reason`) appear in both the manifest and
   the report.

## Output

For each pilot customer, file a follow-up issue in `aiappsgbb/threadlight-skills`
titled `field-test: <customer-name> friction points`. Body should include:

- **Recipe-level:** which recipes fired falsely, which missed real issues.
- **Wizard-level:** which questions caused confusion or required escalation.
- **Override-level:** how many overrides the customer needed; any pattern
  across the overrides that hints at a missing recipe, missing pillar, or
  policy default the assessor should reconsider.
- **Dispatch-level:** agent loop friction, recipe ambiguity, stale-plan
  behaviour.

Fold the union of those friction points into the v0.6.0 spec's "Motivation"
section.

## What NOT to do

- Do not commit customer repos or customer findings to this repo.
- Do not write `customer-overrides.yaml` files for real customers to this repo.
- Do not bypass the SACRED RULE — assessor reads the customer repo, agent
  dispatches patches, customer team approves PRs. No third path.

## Sign-off

Each pilot run requires:

1. A maintainer of threadlight-skills (driver).
2. A customer SRE or engineer (subject-matter expert).
3. Optional: an awesome-gbb maintainer (skill alignment).

## Follow-up issues

This doc only commits the protocol. Actual customer engagement is tracked
post-release: after each pilot, file a `field-test: <customer-name> friction
points` issue in `aiappsgbb/threadlight-skills` using the structure described
in **Output** above. Maintainer-side follow-up work (sibling-skill flips,
experimental promotion votes, deferred buckets) is tracked separately under
the v0.6.0+ milestone.
