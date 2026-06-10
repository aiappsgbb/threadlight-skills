# Handoff checklist — pre-go-live

> **What this is.** The minimum checklist the customer's owner and the
> SRE team should complete and sign before the pilot accepts production
> traffic.

This list is intentionally shorter than the report — it is the
"before we open the floodgate, are we all aligned?" checkpoint. Each
item is a yes/no question.

## A — The artefacts exist

| ☐ | Item | Owner |
|---|---|---|
| ☐ | `specs/SPEC.md` includes a filled-in `§ 12 Production Readiness` block (no `TODO` markers) | Product |
| ☐ | `tests/postdeploy-manifest.json` from `threadlight-safe-check` is green | SE |
| ☐ | `tests/production-readiness-manifest.json` from this skill is generated within 24h of cutover | SE |
| ☐ | `docs/production-readiness-report.md` from this skill has been shared with CISO + SRE | SE |
| ☐ | `docs/runbook.md` (or equivalent) exists and is current | SRE |
| ☐ | `docs/handoff-acceptance.md` is signed by the incident owner | Incident owner |
| ☐ | `tests/restore-drill-*.md` (if RTO < 24h) is dated within last 90 days | SRE |
| ☐ | `docs/sbom.json` (or equivalent SBOM) is current | Product |

## B — The accounts and routes work

| ☐ | Item | Owner |
|---|---|---|
| ☐ | The incident owner email is monitored 24/7 (not a personal inbox) | Incident owner |
| ☐ | The L1 escalation rota is on a pager (not Slack-only) | SRE |
| ☐ | The alert action group has at least one verified destination — test alert fired & received | SRE |
| ☐ | The Teams / Slack channel referenced from § 12 is real and the bot can post to it | Product |
| ☐ | KV secret rotation policy is documented and the owner is named | Security |
| ☐ | Model retirement-notice owner is identified and subscribed to Foundry deprecation announcements | Product |

## C — Cost and capacity are sized

| ☐ | Item | Owner |
|---|---|---|
| ☐ | Budget exists in Cost Management with anomaly alert wired | Finance partner |
| ☐ | If PTU: capacity per region+model is reserved and matches § 12 | Product |
| ☐ | If PTU: PAYG fallback enabled if § 12 says so | Product |
| ☐ | Cost forecast at 5x and 20x usage has been shared with finance | Product |

## D — The customer agreed in writing

| ☐ | Item | Owner |
|---|---|---|
| ☐ | Customer CISO / risk team has signed off on the report's residual risk register | Customer |
| ☐ | Every active waiver has an owner, expiry, and compensating control | Customer |
| ☐ | The cutover window is agreed and communicated | Both |
| ☐ | Rollback criteria and rollback owner are agreed | Both |

## E — The pilot itself is ready to take traffic

| ☐ | Item | Owner |
|---|---|---|
| ☐ | Latest `foundry-evals` run is within freshness window and passing | Product |
| ☐ | One warm-up invocation done in the 5 minutes before traffic | SE |
| ☐ | If SRE Agent recipe adopted: daily health task is scheduled | SRE |
| ☐ | If AGT middleware in use: verifier run within last 30 days, results green | Product |

## F — Governance + capacity surface (v0.3.0)

These rows match new finding IDs added in production-ready v0.3.0. They
are advisory but every "no" here was the cause of a production incident
in at least one prior GBB engagement, so push hard against waiving them.

| ☐ | Item | Owner | Finding ID |
|---|---|---|---|
| ☐ | Restore-drill artefact (`tests/restore-drill-*.md`) exists and is dated within 90 days | SRE | `REL-007` |
| ☐ | Recovery Services Vault has at least one restore point in the live env | SRE | `REL-008` |
| ☐ | **Defender for AI Services** plan enabled on the subscription | Security | `GOV-101` |
| ☐ | **Defender for Key Vault** plan enabled on the subscription | Security | `GOV-102` |
| ☐ | **Defender for Servers / Containers** plan enabled (Standard tier) | Security | `GOV-103` |
| ☐ | Defender Secure Score above the agreed floor (default 60%; see `--secure-score-floor`) | Security | `GOV-104` |
| ☐ | Required Azure Policy assignments present at sub/RG scope | Security | `GOV-201` |
| ☐ | No non-compliant resources under required policies | Security | `GOV-202` |
| ☐ | Sane-default initiative assigned (ASB v3 or customer equivalent) | Security | `GOV-203` |
| ☐ | TPM headroom available for planned model load (no `--quota-utilization > 80%`) | Product | `MDL-110` |
| ☐ | Foundry account capacity available in the target region | Product | `MDL-111` |
| ☐ | Declared `target_posture` matches detected live evidence (no `POS-001`) | SE | `POS-001` |

---

> **If any box is unchecked at cutover time, the answer to "are we
> ready?" is no, regardless of how many boxes are checked.**

If the customer pushes to ship anyway, the report's residual risk
register is what you point to in the post-mortem when the unchecked
box was the one that bit.
