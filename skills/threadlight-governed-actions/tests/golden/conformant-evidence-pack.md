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
- 19 application-path probe result(s) recorded.

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

- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:25526bc0da5d1228c8ea0bbabd91be6e776a862506e098c3da6ea2946cd67bf2
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:4b04eb784efe559c59fe8f0ccb6cd9d35e99119b182c626ca67a288f2becbdd6
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:538fe7f57387468db9554f6de70243d94a49849217fb6b12f01e6a7d1ca96aba
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:6b7522585bfa3a7a4c498a495a2513bfb745dd5ddabf8eb4edc758ef5fb3da65
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:7fcc9bc13f0f9d11ab9a43fa537d7a639da4870436ae47a067c72a9d7aafad97
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:85e2f35dafc595107b0b0c8de43b6b7e5fc4eae6c9cf27a801d2f57f9162e60b
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:890636974f81b6fd3a70ee47419806b268605bffcf50608254ac8ea8889928a5
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:98fb1c0fb6872e23ee43ecc500c8aca4db964772ef09c28fe410c40802159b4f
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:98fb1c0fb6872e23ee43ecc500c8aca4db964772ef09c28fe410c40802159b4f
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:add167ecaa19bd77a5db330b15fdb28626d27dec491495615b25a3384afd1ad8
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:c593690c5ca63ca23b76938ec67628137ae8faf0c6017790e184eaeb33bed8c8
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=sha256:e6bffa55d782792dc531cf4642e5cf5612dede6164931dc793dbaa7c16e154b9
- `crash` action=`payments.refund` path=`None` status=pass reason=crash-blocked evidence=audit-0001~2bbdaccc2f5e1092
- `deny` action=`payments.refund` path=`None` status=pass reason=deny-enforced evidence=audit-0001~381f4739271102cd
- `malformed-verdict` action=`payments.refund` path=`None` status=pass reason=malformed-verdict-blocked evidence=audit-0001~6037364472d45cf2
- `output-mediation` action=`None` path=`None` status=pass reason=output-mediation-enforced evidence=none
- `payload-free-audit` action=`payments.refund` path=`None` status=pass reason=payload-free-audit-enforced evidence=audit-approval-audit-probe-nonce
- `timeout` action=`payments.refund` path=`None` status=pass reason=timeout-blocked evidence=audit-0001~32eb7a5316406d67
- `transform` action=`payments.refund` path=`None` status=pass reason=transform-enforced evidence=audit-0001~f9cf5a5cf02e20f0, sha256:7e84cbf0f7a7c92c037058665d66152f8eb8580ab2534e52c877bccceb9cc7bf, sha256:fb6632bd6651ff35747457d7d6aab3f5d91ff8521f5a79f7382c412da5ef081b

## GitHub Copilot change plane

- Repository: `octo-org/governed-actions-fixtures`.
- 1 required workflow file(s) evidenced, 0 identity/identities recorded.

## Evidence index

| Evidence ID | Kind | Source | SHA-256 | Collected At | Live Verified |
| --- | --- | --- | --- | --- | --- |
| EVID-spec-section-8 | static-file-hash | specs/SPEC.md#section-8 | sha256:f1879370f7552799dc50d0e04ed0e4d1f85553e4f739ef4f2c7e259c087ff67d | 2026-09-01T12:00:00Z | False |
| alert-catalog | file-set | governance/alerts.json | sha256:c5ed00d05bcfa6950957100fa7eaf6fd15d78bfd4c82411807438df7a5bedfeb | unknown | False |
| audit-0001~2bbdaccc2f5e1092 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:2bbdaccc2f5e1092140a64b0a9da17e059dbcecff004e448e47e3d0fb6ccb8f2 | 2026-09-01T12:00:00Z | False |
| audit-0001~32eb7a5316406d67 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:32eb7a5316406d67d326aec85eec3616fcd2b068e22ff7ee271ff803253b408b | 2026-09-01T12:00:00Z | False |
| audit-0001~381f4739271102cd | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:381f4739271102cd6f2d740cd27fa73e10ea2478a8fee0cc07f35c0063f1a60a | 2026-09-01T12:00:00Z | False |
| audit-0001~6037364472d45cf2 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:6037364472d45cf2d6402547b61d7071f5920343283f0acc72672e7c58cd6c5e | 2026-09-01T12:00:00Z | False |
| audit-0001~f9cf5a5cf02e20f0 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:f9cf5a5cf02e20f0cc808a91b82c374be4d27b15183374c846497c969b4a3f09 | 2026-09-01T12:00:00Z | False |
| audit-approval-audit-probe-nonce | probe-audit-record | app.agent:AUDIT_EVENTS | sha256:891ad27bce12dd0036cab84b211b9f3d8798656c89d100b07b1da07e3df78a35 | 2026-09-01T12:00:00Z | False |
| ghcp-workflows | file-set | .github/workflows/governed-actions.yml | sha256:16819e03d140b14bd7bf5d4007a8b0755170e7881180bcfab2cef328abac3fc1 | unknown | False |
| sha256:25526bc0da5d1228c8ea0bbabd91be6e776a862506e098c3da6ea2946cd67bf2 | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:25526bc0da5d1228c8ea0bbabd91be6e776a862506e098c3da6ea2946cd67bf2 | 2026-09-01T12:00:00Z | False |
| sha256:4b04eb784efe559c59fe8f0ccb6cd9d35e99119b182c626ca67a288f2becbdd6 | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:4b04eb784efe559c59fe8f0ccb6cd9d35e99119b182c626ca67a288f2becbdd6 | 2026-09-01T12:00:00Z | False |
| sha256:538fe7f57387468db9554f6de70243d94a49849217fb6b12f01e6a7d1ca96aba | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:538fe7f57387468db9554f6de70243d94a49849217fb6b12f01e6a7d1ca96aba | 2026-09-01T12:00:00Z | False |
| sha256:6b7522585bfa3a7a4c498a495a2513bfb745dd5ddabf8eb4edc758ef5fb3da65 | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:6b7522585bfa3a7a4c498a495a2513bfb745dd5ddabf8eb4edc758ef5fb3da65 | 2026-09-01T12:00:00Z | False |
| sha256:7e84cbf0f7a7c92c037058665d66152f8eb8580ab2534e52c877bccceb9cc7bf | probe-argument-hash | governance/probe-ledger.jsonl | sha256:7e84cbf0f7a7c92c037058665d66152f8eb8580ab2534e52c877bccceb9cc7bf | 2026-09-01T12:00:00Z | False |
| sha256:7fcc9bc13f0f9d11ab9a43fa537d7a639da4870436ae47a067c72a9d7aafad97 | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:7fcc9bc13f0f9d11ab9a43fa537d7a639da4870436ae47a067c72a9d7aafad97 | 2026-09-01T12:00:00Z | False |
| sha256:85e2f35dafc595107b0b0c8de43b6b7e5fc4eae6c9cf27a801d2f57f9162e60b | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:85e2f35dafc595107b0b0c8de43b6b7e5fc4eae6c9cf27a801d2f57f9162e60b | 2026-09-01T12:00:00Z | False |
| sha256:890636974f81b6fd3a70ee47419806b268605bffcf50608254ac8ea8889928a5 | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:890636974f81b6fd3a70ee47419806b268605bffcf50608254ac8ea8889928a5 | 2026-09-01T12:00:00Z | False |
| sha256:98fb1c0fb6872e23ee43ecc500c8aca4db964772ef09c28fe410c40802159b4f | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:98fb1c0fb6872e23ee43ecc500c8aca4db964772ef09c28fe410c40802159b4f | 2026-09-01T12:00:00Z | False |
| sha256:add167ecaa19bd77a5db330b15fdb28626d27dec491495615b25a3384afd1ad8 | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:add167ecaa19bd77a5db330b15fdb28626d27dec491495615b25a3384afd1ad8 | 2026-09-01T12:00:00Z | False |
| sha256:c593690c5ca63ca23b76938ec67628137ae8faf0c6017790e184eaeb33bed8c8 | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:c593690c5ca63ca23b76938ec67628137ae8faf0c6017790e184eaeb33bed8c8 | 2026-09-01T12:00:00Z | False |
| sha256:e6bffa55d782792dc531cf4642e5cf5612dede6164931dc793dbaa7c16e154b9 | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:e6bffa55d782792dc531cf4642e5cf5612dede6164931dc793dbaa7c16e154b9 | 2026-09-01T12:00:00Z | False |
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
