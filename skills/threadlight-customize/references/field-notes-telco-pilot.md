# Field notes — a large telco AI pilot

> Anonymized learnings from a large European telco AI engagement that shaped
> this leg. Names, tenants, and identifying details are intentionally omitted —
> this is a public repository. The point is the *pattern*, not the customer.

## Context

A pilot built with the Threadlight pipeline proved out on an SE sandbox, then
had to be stood up inside the customer's own, heavily-governed, **fully-private
(private-VNet, no public egress)** environment. The pilot code was the easy
part. The onboarding was not — and almost none of the hard parts could have
been pre-automated, which is exactly why this skill ships instructions instead
of a generator.

## What actually consumed the time

1. **Intake was the long pole.** The single biggest cost was *getting the
   inputs*: customer architecture and security documents, the environment setup
   (landing zones, identity model, allowed regions, private DNS, egress rules),
   the requirements, and the **template/starter code the customer mandated we
   build on**. Every blank in that picture became a blocked deploy later. →
   Move 1 (the intake gate) exists because of this. Start it early.

2. **It had to be tested on *their* environment.** A green run on the SE sandbox
   convinced no one. The onboarding only became real once the dev/test loop ran
   **inside the customer's boundary**, against their private endpoints, with
   their DNS and egress. → Move 3 exists because of this.

3. **Reaching a fully-private env was the sticking point.** Two things helped:
   - **Azure ML compute instance + VS Code** — an in-VNet box with no public IP
     that natively reached the private endpoints. This was the more reliable
     path for the no-egress target.
   - **GitHub Codespaces** — helped *to some extent*: great as a standardized
     dev box and for the non-private parts, but a vanilla Codespace does not
     join the customer VNet, so it needed a sanctioned connectivity path for the
     private resources. → Both are written up in `private-env-test/`, with the
     Azure ML pattern recommended for fully-private targets.

4. **Production onboarding did not generalize.** The landing-zone topology,
   identity/RBAC model, mandated IaC, and change-management gates were specific
   to this customer. Any attempt to encode them would have been wrong for the
   next engagement. → Move 4 (non-coverage) and the overlay-not-fork-edit rule
   come from this.

## What we would tell the next SE

- Run the **intake gate first**, in parallel with the pilot. Treat unfilled
  fields as blockers, not notes.
- Stand up the **in-VNet test box early** (Azure ML compute instance) so you are
  not discovering private-DNS gaps on deploy day. Run the pre-flight checklist.
- Keep every customer change in an **overlay** over a pinned upstream. You *will*
  want upstream fixes later.
- Write the **non-coverage statement** before the architecture review, not after
  someone asks "so what did Threadlight actually do here?"
