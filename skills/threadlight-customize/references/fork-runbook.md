# Fork runbook — fork, pin, overlay

> Fork Threadlight **once**, up front, in a way that survives upstream updates.
> The goal: keep this customer's customizations isolated so a later
> `git merge` of upstream Threadlight does not clobber them, and so the next SE
> can read exactly what you changed and why.

## 1. Fork (or overlay alongside the plugin)

Two supported shapes:

- **Repo fork (recommended for SE-led + customer-self-serve).** Fork
  `aiappsgbb/threadlight-skills` into the customer org or the engagement repo.
  All overrides live in this fork.
- **Plugin + overlay (lighter touch).** Install the published plugin and keep a
  separate `overlay/` directory in the engagement repo that layers over it. Use
  this when the customer does not want a full fork.

Pick one and record it in the customer profile.

## 2. Pin upstream

Record the exact upstream commit you forked from so drift is visible. Mirror
the existing convention used by `threadlight-deploy` and `threadlight-local-test`
(`references/upstream-pin.md`):

```
# overlay/upstream-pin.md
upstream: aiappsgbb/threadlight-skills
commit:   <full sha you forked from>
date:     <date>
plugin:   <plugin version, e.g. 1.4.0>
notes:    <what, if anything, was already diverged at fork time>
```

Re-pin whenever you intentionally pull upstream. The pin is what makes a future
merge reviewable.

## 3. Overlay, don't fork-edit

**Never edit a forked skill in place to customize it.** In-place edits collide
with every upstream change. Instead:

- Keep customer selectors, `azd env` values, pillar-threshold overrides, and
  mandated-IaC wiring in a dedicated **overlay** directory.
- Reference the overlay from your customization map (Move 2) so each override
  has a home and a rationale.
- Layer the overlay over the pinned upstream at build/deploy time rather than
  mutating upstream files.

```
engagement-repo/
├── threadlight-skills/        # pinned upstream fork (left unmodified)
├── overlay/
│   ├── upstream-pin.md
│   ├── deploy/                # customer selectors, env hooks
│   ├── production-ready/      # customer_overrides, thresholds
│   └── cicd/                  # customer identity / RBAC / runner inputs
└── docs/threadlight-customize/
    ├── customer-profile.md
    ├── customization-map.md
    └── non-coverage.md
```

## 4. Pulling upstream later

1. Fetch upstream, review the diff against your pin.
2. Merge upstream into the fork (overlay is untouched because you never edited
   upstream files).
3. Re-apply / re-validate the overlay against the new upstream.
4. Re-pin (`upstream-pin.md`) and note anything that needed re-work.

If a merge is painful, that is a signal an override leaked out of the overlay —
move it back in.
