# Governance Evidence Pack

## Scope and trust model

- Assessor: threadlight-governed-actions v0.1.0 (adapter: maf/v1), phase `pre-deploy`.
- Source: `octo-org/governed-actions-fixtures` @ `0123456789abcdef0123456789abcdef01234567` (dirty: False).
- Agent Hooks is cooperative/alpha and is never treated as this assessment's security boundary.
- Conformance recorded in this pack is never a certification.
- GitHub Copilot coding agent's own internal reasoning/tool-calling loop is never intercepted by this assessment; only the PR/CI/deployment supply chain a change travels through is assessed.
- Provider-hosted tool side effects without an equivalent, independently verified server-side control are not supported by this assessment's mediation model.
- Tool services are expected to independently re-check authorization, idempotency, and transaction boundaries; this assessment never substitutes for that.

## Architecture and data flow

- 2 declared consequential action(s) across 10 traced mediation path(s).
- 8 application-path probe result(s) recorded.

## Runtime action inventory

- `customer.lookup` (customer.lookup): consequence=read, reversible=None, inventory_status=pass
- `payments.refund` (payments.refund): consequence=irreversible, reversible=None, inventory_status=pass

## Runtime mediation graph

- `347a7d4ba1b2a0fa` action=`customer.lookup` mode=background covered=True status=pass
- `aea9253c6ec0258e` action=`customer.lookup` mode=batch covered=True status=pass
- `37d1f78e666dd467` action=`customer.lookup` mode=direct-tool covered=True status=pass
- `7166e9d0b462e868` action=`customer.lookup` mode=interactive covered=True status=pass
- `1e5fd34aca356abe` action=`customer.lookup` mode=subagent covered=True status=pass
- `400019bbedc16b75` action=`payments.refund` mode=background covered=True status=pass
- `099ebdd0357f3031` action=`payments.refund` mode=batch covered=True status=pass
- `68852d2b9d611a52` action=`payments.refund` mode=direct-tool covered=True status=pass
- `272a18e87fab21fc` action=`payments.refund` mode=interactive covered=True status=pass
- `8fa2a4cde35188b9` action=`payments.refund` mode=subagent covered=True status=pass

## Application-path probe evidence

- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:98fb1c0fb6872e23ee43ecc500c8aca4db964772ef09c28fe410c40802159b4f
- `crash` action=`payments.refund` path=`None` status=pass reason=crash-blocked evidence=audit-0001
- `deny` action=`payments.refund` path=`None` status=pass reason=deny-enforced evidence=audit-0001
- `malformed-verdict` action=`payments.refund` path=`None` status=pass reason=malformed-verdict-blocked evidence=audit-0001
- `output-mediation` action=`None` path=`None` status=pass reason=output-mediation-enforced evidence=none
- `payload-free-audit` action=`None` path=`None` status=pass reason=payload-free-audit-enforced evidence=audit-approval-audit-probe-nonce
- `timeout` action=`payments.refund` path=`None` status=pass reason=timeout-blocked evidence=audit-0001
- `transform` action=`payments.refund` path=`None` status=pass reason=transform-enforced evidence=audit-0001, sha256:7e84cbf0f7a7c92c037058665d66152f8eb8580ab2534e52c877bccceb9cc7bf, sha256:fb6632bd6651ff35747457d7d6aab3f5d91ff8521f5a79f7382c412da5ef081b

## GitHub Copilot change plane

- Repository: `octo-org/governed-actions-fixtures`.
- 1 required workflow file(s) evidenced, 0 identity/identities recorded.

## Evidence index

| Evidence ID | Kind | Source | SHA-256 | Collected At | Live Verified |
| --- | --- | --- | --- | --- | --- |
| EVID-spec-section-8 | static-file-hash | specs/SPEC.md#section-8 | sha256:f1879370f7552799dc50d0e04ed0e4d1f85553e4f739ef4f2c7e259c087ff67d | 2026-09-01T12:00:00Z | False |
| alert-catalog | file-set | governance/alerts.json | sha256:c5ed00d05bcfa6950957100fa7eaf6fd15d78bfd4c82411807438df7a5bedfeb | unknown | False |
| audit-0001 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:381f4739271102cd6f2d740cd27fa73e10ea2478a8fee0cc07f35c0063f1a60a | 2026-09-01T12:00:00Z | False |
| audit-approval-audit-probe-nonce | probe-audit-record | app.agent:AUDIT_EVENTS | sha256:891ad27bce12dd0036cab84b211b9f3d8798656c89d100b07b1da07e3df78a35 | 2026-09-01T12:00:00Z | False |
| ghcp-workflows | file-set | .github/workflows/governed-actions.yml | sha256:16819e03d140b14bd7bf5d4007a8b0755170e7881180bcfab2cef328abac3fc1 | unknown | False |
| sha256:7e84cbf0f7a7c92c037058665d66152f8eb8580ab2534e52c877bccceb9cc7bf | probe-argument-hash | governance/probe-ledger.jsonl | sha256:7e84cbf0f7a7c92c037058665d66152f8eb8580ab2534e52c877bccceb9cc7bf | 2026-09-01T12:00:00Z | False |
| sha256:98fb1c0fb6872e23ee43ecc500c8aca4db964772ef09c28fe410c40802159b4f | approval-binding-digest | governance/nonce-ledger.jsonl | sha256:98fb1c0fb6872e23ee43ecc500c8aca4db964772ef09c28fe410c40802159b4f | 2026-09-01T12:00:00Z | False |
| sha256:fb6632bd6651ff35747457d7d6aab3f5d91ff8521f5a79f7382c412da5ef081b | probe-argument-hash | governance/probe-ledger.jsonl | sha256:fb6632bd6651ff35747457d7d6aab3f5d91ff8521f5a79f7382c412da5ef081b | 2026-09-01T12:00:00Z | False |

## Pass/fail matrix

| ID | Plane | Control | Status | Reason | Evidence | Remediation |
| --- | --- | --- | --- | --- | --- | --- |
| OPS-001 | both | Every required governance alert class is enabled and declares a stable reason/correlation identifier. | pass | alert-catalog-complete | none | none |

## Residual-risk register

- `RISK-AUDIT-RETENTION-UNEVIDENCED` (ref: AUD-001): This assessor observes only that a payload-free audit record is emitted per consequential-action decision; audit retention, deletion, data-residency, and legal-hold policy are the target deployment's own operational responsibility and are never evidenced or inferred here.
- `RISK-BOUNDED-ASSESSMENT-SCOPE` (ref: ACT-001): This finding reflects only this assessor's own bounded, read-only observation of the assessed repository/deployment at the recorded source commit; it does not extend to scope, time windows, or systems this assessment never observed.
- `RISK-COOPERATIVE-HOST-TRUST` (ref: ENF-002): Agent Hooks is a cooperative, alpha-stage interception seam and is never treated as a security boundary by this assessor: a caller that skips the hook is only caught when an independent, verified compensating control exists for that bypass surface.
- `RISK-LIVE-EVIDENCE-FRESHNESS` (ref: GHCP-002): Live evidence \(branch protection, required checks, identity separation\) is only as current as its own trustworthy collection timestamp, and freshness is judged only against the evidence a finding or application-path probe actually requires -- never unrelated evidence this manifest merely happens to carry. An evidence entry whose collected_at is missing or does not parse as a real instant is rendered with collected_at null and live_verified false \(never the input's own claimed live_verified/freshness_seconds\), and is excluded from this manifest's freshness computation entirely. captured_at is this assessment's own trusted capture instant -- never derived from evidence, and never the newest evidence timestamp this manifest happens to carry. When no required evidence carries a trustworthy timestamp, or captured_at itself cannot be trusted, freshness.status is reported stale; when captured_at exceeds the oldest trustworthy required-evidence timestamp \(oldest_source_at\) plus this manifest's valid_for_hours, freshness.status is reported expired; and when a finding or application-path probe cites its own required evidence entry that cannot be trusted, freshness.status is reported stale even if other, unrelated evidence in this manifest is itself fresh -- none of these cases is ever assumed to still be fresh.
- `RISK-NON-CERTIFICATION` (ref: PIN-001): Conformance claims, conformance reports, and pinned probe-suite/CTK results recorded in this manifest are this assessor's own read-only observations -- they are never a certification of the assessed system by threadlight or any third party.
- `RISK-PROVIDER-HOSTED-LIMITATIONS` (ref: MED-003): Provider-hosted tool side effects without an equivalent, independently verified server-side control are outside what this assessor's mediation model can prove; a passing result here never implies the provider itself was audited.
- `RISK-UPSTREAM-ALPHA-DRIFT` (ref: PIN-001): Agent Hooks, its SDK, and the Model/Agent Framework integration this manifest pins are alpha/experimental upstream artifacts; drift from the pinned tuple without rerunning the Conformance Test Kit and application probes invalidates every finding that depended on it.

## Remediation plan

- No outstanding remediation is required.
