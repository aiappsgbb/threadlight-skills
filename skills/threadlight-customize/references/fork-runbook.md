# Fork runbook — fork, pin, overlay, merge

> Fork Threadlight **once**, up front, in a way that survives upstream updates.
> The goal: keep this customer's customizations isolated so a later `git merge`
> of upstream Threadlight does not clobber them, and so the next SE can read
> exactly what you changed and why.
>
> This runbook is **copy-paste executable** — every step is a command you can
> run. Paths assume the repo-fork shape (recommended); for the overlay-only
> shape, point the `skills/...` paths at the installed plugin instead.

## Before you start

- `git` and the GitHub CLI (`gh`) authenticated to the customer org (or your
  engagement org): `gh auth status`.
- The customer profile (Move 1) at least started — you need to know whether the
  customer wants a **full fork** in their org or a **lighter overlay** in the
  engagement repo.
- ~10 minutes. The fork is small; the real thinking lives in the customization
  map (Move 2).

## Decision — fork vs overlay-only

| You are… | Use | Why |
|---|---|---|
| SE-led, or the customer will self-serve and own the repo | **Repo fork** | Every override lives in one repo the customer controls. |
| Light touch — the customer does not want a full fork | **Plugin + overlay** | Install the published plugin; keep only an `overlay/` in the engagement repo. |

Both shapes use the **same overlay layout** below — the only difference is
whether `threadlight-skills/` is a fork you own or a pinned plugin you
installed. Record the choice in the customer profile.

## Step 1 — Fork and clone

Repo fork (recommended). `gh` sets `origin` → your fork and `upstream` →
`aiappsgbb/threadlight-skills` in one shot:

```bash
gh repo fork aiappsgbb/threadlight-skills \
  --org <customer-or-engagement-org> --clone --remote
cd threadlight-skills
git remote -v          # origin = your fork, upstream = aiappsgbb/threadlight-skills
```

If you cloned directly instead of using `gh fork`, wire `upstream` by hand:

```bash
git remote add upstream https://github.com/aiappsgbb/threadlight-skills.git
git fetch upstream
```

Overlay-only (no fork): install the plugin per the repo README, then create an
engagement repo that contains just the `overlay/` and
`docs/threadlight-customize/` trees from Step 3.

## Step 2 — Pin upstream

Record the exact upstream commit you forked from, so drift is visible and a
future merge is reviewable. Capture the SHA and write the pin:

```bash
mkdir -p overlay
UPSTREAM_SHA="$(git rev-parse upstream/main)"
cat > overlay/upstream-pin.md <<PIN
# overlay/upstream-pin.md
upstream: aiappsgbb/threadlight-skills
commit:   ${UPSTREAM_SHA}
date:     $(date +%F)
plugin:   <plugin version, e.g. 1.4.0>
notes:    <what, if anything, was already diverged at fork time>
PIN
git add overlay/upstream-pin.md
git commit -m "chore: pin upstream threadlight"
```

This is analogous in spirit to the `references/upstream-pin.md` freshness
records `threadlight-deploy` and `threadlight-local-test` keep for the sources
they vendor — except here you pin the Threadlight fork's own upstream. Re-pin
whenever you intentionally pull upstream (Step 6).

## Step 3 — Scaffold the overlay

**Never edit a forked skill in place.** In-place edits collide with every
upstream change. Instead create one overlay directory that holds *customer
inputs the skills already consume* — selectors, `azd env` values, pillar
overrides, identity/RBAC constraints — plus the four Move artifacts:

```bash
mkdir -p \
  overlay/deploy \
  overlay/production-ready \
  overlay/cicd \
  overlay/safe-check \
  docs/threadlight-customize
```

Result:

```
engagement-repo/
├── threadlight-skills/         # pinned upstream fork — left UNMODIFIED
├── overlay/
│   ├── upstream-pin.md
│   ├── deploy/                 # landing-zone targeting, azd env, manifest selectors
│   ├── production-ready/       # customer-overrides.yaml, pillar thresholds
│   ├── cicd/                   # identity model, scoped RBAC, runner topology inputs
│   └── safe-check/             # customer resource selectors + naming standard
└── docs/threadlight-customize/
    ├── customer-profile.md     # Move 1
    ├── customization-map.md    # Move 2
    └── non-coverage.md         # Move 4
```

## Step 4 — Put real overrides in the overlay (the actual work)

This is where "customize" becomes concrete. For every skill the customization
map (Move 2) marked **override**, drop the customer input into its overlay
folder. Three worked examples — the priority-#1 skills:

### `threadlight-production-ready` — the customer's policy baseline

The scorecard already accepts a `customer-overrides.yaml` (real schema in
`skills/threadlight-production-ready/references/customer-overrides-schema.md`).
Overrides are **status flips only** (`pass` ↔ `fail`) on `should-fix` findings,
each with a written reason for the audit trail. **`must-fix` findings cannot be
flipped** — the scorecard exits 2 and names the one you tried. Put the
customer's version in the overlay:

```yaml
# overlay/production-ready/customer-overrides.yaml
customer: <customer-name>
overrides:
  - recipe_id: SEC-105            # should-fix: KV access via RBAC, not legacy policies
    status: pass                  # customer uses an approved equivalent control
    reason: "Access policies are governed by the customer's audited PIM workflow; equivalent control, reviewed by security 2026-Q2."
  - recipe_id: NET-103            # should-fix: NSG flow logs on spoke subnets
    status: fail                  # customer is STRICTER than the default
    reason: "Customer audit policy mandates NSG flow logs on every spoke subnet."
```

> Pick **real, `should-fix`** recipe IDs from the pillar catalogs
> (`skills/threadlight-production-ready/references/pillars/*.md`). Hard
> requirements such as private endpoints (`NET-102`) or Key Vault public-access
> (`SEC-103`) are `must-fix` and will be rejected — remediate those, don't flip
> them.

Run the scorecard against the customer's pilot project (where its
`specs/manifest.json` lives — **not** the fork root):

```bash
python3 skills/threadlight-production-ready/scripts/production_ready.py \
  --root <path-to-customer-pilot> \
  --customer-overrides overlay/production-ready/customer-overrides.yaml
```

### `threadlight-deploy` — landing zone + private networking

Deploy reads selectors from `specs/manifest.json` and targets via `azd env`.
Keep the customer's targeting in the overlay and *apply* it — never by editing
the skill:

```bash
# overlay/deploy/env.sh — the customer's landing-zone targeting
azd env set AZURE_SUBSCRIPTION_ID  <customer-sub-id>
azd env set AZURE_LOCATION         <customer-allowed-region>
azd env set AZURE_RESOURCE_GROUP   <customer-spoke-rg>
```

Private networking is **not** an `azd` flag — it is driven by the SPEC
`target_posture` (`citadel-spoke` / `hybrid` / `vnet`) and the Bicep module
selectors in `specs/manifest.json`. Record the customer's posture, region
allow-list, private-DNS wiring, and mandated IaC modules (profile Block D)
under `overlay/deploy/` and reference them from the customization map.

### `threadlight-cicd` — identity, RBAC, runners

cicd *generates* the pipeline; you feed it constraints, you do not re-implement
it here. Capture the customer's identity model (OIDC / workload identity
federation — **no stored long-lived secret**), the RBAC scope (spoke RG only),
and runner topology in `overlay/cicd/inputs.md`, then run the skill so it emits
the pipeline + env-setup runbooks the platform team executes.

## Step 5 — Run the loop pointed at the overlay

Run the normal Threadlight loop, but with overlay inputs — and, for production
onboarding, **from inside the customer boundary** (Move 3):

```bash
source overlay/deploy/env.sh    # customer targeting
# then run threadlight local-test / deploy / safe-check / production-ready as
# usual, each consuming its overlay input. Upstream skill files stay untouched.
```

If a skill needs a customer value you have not put in the overlay, that is a
**blocked field** — go back to the customer profile (Move 1). Do not hard-code
it into a forked skill.

## Step 6 — Pull upstream later

Because you never edited upstream files, pulling updates is a clean merge:

```bash
git fetch upstream
PIN_SHA="$(sed -n 's/^commit: *//p' overlay/upstream-pin.md)"
git log --oneline "${PIN_SHA}..upstream/main"   # what changed since your pin
git merge upstream/main                          # only skill files merge; overlay untouched
```

Then re-validate and re-pin:

```bash
# confirm the overlay still passes against the new upstream
python3 skills/threadlight-production-ready/scripts/production_ready.py \
  --root <path-to-customer-pilot> \
  --customer-overrides overlay/production-ready/customer-overrides.yaml

git rev-parse upstream/main   # update overlay/upstream-pin.md 'commit:' to this value
```

**If the merge is painful, an override leaked out of the overlay.** Find the
conflicting upstream skill file, move your change into `overlay/`, and re-merge.
A clean merge is the proof your customizations are isolated.

## Done-when checklist

- [ ] Fork (or plugin install) recorded in the customer profile
- [ ] `overlay/upstream-pin.md` written with the real fork SHA
- [ ] Every Move-2 "override" has a file under `overlay/<skill>/`
- [ ] No upstream skill edited in place — `git diff upstream/main -- skills/` is empty
- [ ] Priority skills run green against the overlay, **inside the customer boundary**
