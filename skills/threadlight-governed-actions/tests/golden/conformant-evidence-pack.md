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
- 39 application-path probe result(s) recorded.

## Runtime action inventory

- `customer.lookup` (customer.lookup): consequence=read, reversible=None, inventory_status=pass
- `payments.refund` (payments.refund): consequence=irreversible, reversible=None, inventory_status=pass

## Runtime mediation graph

- `347a7d4ba1b2a0fa` action=`customer.lookup` mode=background discovered=True executed=True covered=True status=pass
- `aea9253c6ec0258e` action=`customer.lookup` mode=batch discovered=True executed=True covered=True status=pass
- `37d1f78e666dd467` action=`customer.lookup` mode=direct-tool discovered=True executed=True covered=True status=pass
- `7166e9d0b462e868` action=`customer.lookup` mode=interactive discovered=True executed=True covered=True status=pass
- `1e5fd34aca356abe` action=`customer.lookup` mode=subagent discovered=True executed=True covered=True status=pass
- `400019bbedc16b75` action=`payments.refund` mode=background discovered=True executed=True covered=True status=pass
- `099ebdd0357f3031` action=`payments.refund` mode=batch discovered=True executed=True covered=True status=pass
- `68852d2b9d611a52` action=`payments.refund` mode=direct-tool discovered=True executed=True covered=True status=pass
- `272a18e87fab21fc` action=`payments.refund` mode=interactive discovered=True executed=True covered=True status=pass
- `8fa2a4cde35188b9` action=`payments.refund` mode=subagent discovered=True executed=True covered=True status=pass

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
- `output-mediation` action=`payments.refund` path=`None` status=pass reason=output-mediation-enforced evidence=none
- `path-dispatch-allow` action=`customer.lookup` path=`1e5fd34aca356abe` status=pass reason=path-dispatch-mediated evidence=path-decision-1e5fd34aca356abe~15b83f4eed0f0670, path-invocation-1e5fd34aca356abe
- `path-dispatch-allow` action=`customer.lookup` path=`347a7d4ba1b2a0fa` status=pass reason=path-dispatch-mediated evidence=path-decision-347a7d4ba1b2a0fa~4f4dc359d0d76064, path-invocation-347a7d4ba1b2a0fa
- `path-dispatch-allow` action=`customer.lookup` path=`37d1f78e666dd467` status=pass reason=path-dispatch-mediated evidence=path-decision-37d1f78e666dd467~e72d250a4cc07f1d, path-invocation-37d1f78e666dd467
- `path-dispatch-allow` action=`customer.lookup` path=`7166e9d0b462e868` status=pass reason=path-dispatch-mediated evidence=path-decision-7166e9d0b462e868~64152c5dac2257e2, path-invocation-7166e9d0b462e868
- `path-dispatch-allow` action=`customer.lookup` path=`aea9253c6ec0258e` status=pass reason=path-dispatch-mediated evidence=path-decision-aea9253c6ec0258e~235cda31c998fdd0, path-invocation-aea9253c6ec0258e
- `path-dispatch-allow` action=`payments.refund` path=`099ebdd0357f3031` status=pass reason=path-dispatch-mediated evidence=path-decision-099ebdd0357f3031~38951f091a6f519f, path-invocation-099ebdd0357f3031
- `path-dispatch-allow` action=`payments.refund` path=`272a18e87fab21fc` status=pass reason=path-dispatch-mediated evidence=path-decision-272a18e87fab21fc~9647d10ede992135, path-invocation-272a18e87fab21fc
- `path-dispatch-allow` action=`payments.refund` path=`400019bbedc16b75` status=pass reason=path-dispatch-mediated evidence=path-decision-400019bbedc16b75~057126431c70074f, path-invocation-400019bbedc16b75
- `path-dispatch-allow` action=`payments.refund` path=`68852d2b9d611a52` status=pass reason=path-dispatch-mediated evidence=path-decision-68852d2b9d611a52~e28e888cc45316a0, path-invocation-68852d2b9d611a52
- `path-dispatch-allow` action=`payments.refund` path=`8fa2a4cde35188b9` status=pass reason=path-dispatch-mediated evidence=path-decision-8fa2a4cde35188b9~12e615d450ed339b, path-invocation-8fa2a4cde35188b9
- `path-dispatch-deny` action=`customer.lookup` path=`1e5fd34aca356abe` status=pass reason=path-dispatch-denied evidence=path-decision-1e5fd34aca356abe~671eba3a6bd58b04
- `path-dispatch-deny` action=`customer.lookup` path=`347a7d4ba1b2a0fa` status=pass reason=path-dispatch-denied evidence=path-decision-347a7d4ba1b2a0fa~519d6e0590463f52
- `path-dispatch-deny` action=`customer.lookup` path=`37d1f78e666dd467` status=pass reason=path-dispatch-denied evidence=path-decision-37d1f78e666dd467~95b040ae42f63597
- `path-dispatch-deny` action=`customer.lookup` path=`7166e9d0b462e868` status=pass reason=path-dispatch-denied evidence=path-decision-7166e9d0b462e868~848f6155ee90dea6
- `path-dispatch-deny` action=`customer.lookup` path=`aea9253c6ec0258e` status=pass reason=path-dispatch-denied evidence=path-decision-aea9253c6ec0258e~88ac5df90a7668f8
- `path-dispatch-deny` action=`payments.refund` path=`099ebdd0357f3031` status=pass reason=path-dispatch-denied evidence=path-decision-099ebdd0357f3031~20df8c2c566880bc
- `path-dispatch-deny` action=`payments.refund` path=`272a18e87fab21fc` status=pass reason=path-dispatch-denied evidence=path-decision-272a18e87fab21fc~b8868527c60d8170
- `path-dispatch-deny` action=`payments.refund` path=`400019bbedc16b75` status=pass reason=path-dispatch-denied evidence=path-decision-400019bbedc16b75~88d66a1681bc9589
- `path-dispatch-deny` action=`payments.refund` path=`68852d2b9d611a52` status=pass reason=path-dispatch-denied evidence=path-decision-68852d2b9d611a52~de9215ccb5033c58
- `path-dispatch-deny` action=`payments.refund` path=`8fa2a4cde35188b9` status=pass reason=path-dispatch-denied evidence=path-decision-8fa2a4cde35188b9~3f5a128ce7ebb9cf
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
| agent.yaml | static-file-hash | agent.yaml | sha256:4c5f915a6ca38f3b7e0278d05f672e9fb8a396fff05012db3a4dc0ba9439dd11 | 2026-09-01T12:00:00Z | False |
| alert-catalog | file-set | governance/alerts.json | sha256:c5ed00d05bcfa6950957100fa7eaf6fd15d78bfd4c82411807438df7a5bedfeb | unknown | False |
| app/agent.py | static-file-hash | app/agent.py | sha256:4a2a33e05e14248563f0dae63b7524a1419bf65b111773cb71d74cd76a63eaa3 | 2026-09-01T12:00:00Z | False |
| audit-0001~2bbdaccc2f5e1092 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:2bbdaccc2f5e1092140a64b0a9da17e059dbcecff004e448e47e3d0fb6ccb8f2 | 2026-09-01T12:00:00Z | False |
| audit-0001~32eb7a5316406d67 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:32eb7a5316406d67d326aec85eec3616fcd2b068e22ff7ee271ff803253b408b | 2026-09-01T12:00:00Z | False |
| audit-0001~381f4739271102cd | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:381f4739271102cd6f2d740cd27fa73e10ea2478a8fee0cc07f35c0063f1a60a | 2026-09-01T12:00:00Z | False |
| audit-0001~6037364472d45cf2 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:6037364472d45cf2d6402547b61d7071f5920343283f0acc72672e7c58cd6c5e | 2026-09-01T12:00:00Z | False |
| audit-0001~f9cf5a5cf02e20f0 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:f9cf5a5cf02e20f0cc808a91b82c374be4d27b15183374c846497c969b4a3f09 | 2026-09-01T12:00:00Z | False |
| audit-approval-audit-probe-nonce | probe-audit-record | app.agent:AUDIT_EVENTS | sha256:891ad27bce12dd0036cab84b211b9f3d8798656c89d100b07b1da07e3df78a35 | 2026-09-01T12:00:00Z | False |
| ghcp-workflows | file-set | .github/workflows/governed-actions.yml | sha256:16819e03d140b14bd7bf5d4007a8b0755170e7881180bcfab2cef328abac3fc1 | unknown | False |
| path-decision-099ebdd0357f3031~20df8c2c566880bc | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:20df8c2c566880bcf3f978e6bb6e902f66383588867b36ac45328022ed143ce6 | 2026-09-01T12:00:00Z | False |
| path-decision-099ebdd0357f3031~38951f091a6f519f | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:38951f091a6f519f0cbadfa8a7a6729768412083d85fc8a41faef4ad5c6cbc9a | 2026-09-01T12:00:00Z | False |
| path-decision-1e5fd34aca356abe~15b83f4eed0f0670 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:15b83f4eed0f06706c8c8eb0f4f473e4cb2da84dbc9f82c22d623b943d08cb10 | 2026-09-01T12:00:00Z | False |
| path-decision-1e5fd34aca356abe~671eba3a6bd58b04 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:671eba3a6bd58b0402606d90aec7f1704a44b506e5e91e5e05ab4e4f42c618f3 | 2026-09-01T12:00:00Z | False |
| path-decision-272a18e87fab21fc~9647d10ede992135 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:9647d10ede9921353e8b7e23da0a7e5db890b35ea9052fea30fea49a2ab8250e | 2026-09-01T12:00:00Z | False |
| path-decision-272a18e87fab21fc~b8868527c60d8170 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:b8868527c60d8170db3a5a930066a84bb0a7ae2627c59841efba8531e96057b5 | 2026-09-01T12:00:00Z | False |
| path-decision-347a7d4ba1b2a0fa~4f4dc359d0d76064 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:4f4dc359d0d760641fbdf907b97e69c0ff4527748bc8ac57181ff8de67012cb9 | 2026-09-01T12:00:00Z | False |
| path-decision-347a7d4ba1b2a0fa~519d6e0590463f52 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:519d6e0590463f52db618b267fef0d82eb6d525139d001ad50baf971acecb32b | 2026-09-01T12:00:00Z | False |
| path-decision-37d1f78e666dd467~95b040ae42f63597 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:95b040ae42f63597e3a961f3a867bb1001752be45f50a2fb0e42760564b4c37b | 2026-09-01T12:00:00Z | False |
| path-decision-37d1f78e666dd467~e72d250a4cc07f1d | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:e72d250a4cc07f1da3d53a9699c45681c20031fc72d84dcb1851930d69808770 | 2026-09-01T12:00:00Z | False |
| path-decision-400019bbedc16b75~057126431c70074f | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:057126431c70074f0b85646e76a7e7cfcf32af7cef6f2c4505991390d0c7cccc | 2026-09-01T12:00:00Z | False |
| path-decision-400019bbedc16b75~88d66a1681bc9589 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:88d66a1681bc958943aae0af75b4f27986c31cdaeca85e99e5732654065cd6c2 | 2026-09-01T12:00:00Z | False |
| path-decision-68852d2b9d611a52~de9215ccb5033c58 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:de9215ccb5033c58de6fa2d381c9004e2c5fc07bd786171be11c41e600268ae6 | 2026-09-01T12:00:00Z | False |
| path-decision-68852d2b9d611a52~e28e888cc45316a0 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:e28e888cc45316a09db882c574bb3a37601dd902608cf712912643376e2b40c6 | 2026-09-01T12:00:00Z | False |
| path-decision-7166e9d0b462e868~64152c5dac2257e2 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:64152c5dac2257e2fe2e855ef3895bc395491c79dc1356d9ea263b1492039071 | 2026-09-01T12:00:00Z | False |
| path-decision-7166e9d0b462e868~848f6155ee90dea6 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:848f6155ee90dea6cf438098b452dca63c37e20cb29fabaa3609f39b50e82e91 | 2026-09-01T12:00:00Z | False |
| path-decision-8fa2a4cde35188b9~12e615d450ed339b | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:12e615d450ed339b69224c4bfbfe09af4f846fc9adabc1f8a2f74a134bb4d579 | 2026-09-01T12:00:00Z | False |
| path-decision-8fa2a4cde35188b9~3f5a128ce7ebb9cf | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:3f5a128ce7ebb9cf364e799b8af8a47b270f53c595fe5c632cae3d75120757bd | 2026-09-01T12:00:00Z | False |
| path-decision-aea9253c6ec0258e~235cda31c998fdd0 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:235cda31c998fdd08e1864198ba4426cb3f9ecc9255a70eb9a79f698fee298a2 | 2026-09-01T12:00:00Z | False |
| path-decision-aea9253c6ec0258e~88ac5df90a7668f8 | path-pre-action-decision | governance/probe-ledger.jsonl | sha256:88ac5df90a7668f8f925d03988f99efec7a119552f35370f827ad04a2607e124 | 2026-09-01T12:00:00Z | False |
| path-invocation-099ebdd0357f3031 | path-tool-invocation | governance/probe-ledger.jsonl | sha256:4b58e2265412aac813f10321ff1273bd9760588fa47e4fda01f62b9a2c308c3d | 2026-09-01T12:00:00Z | False |
| path-invocation-1e5fd34aca356abe | path-tool-invocation | governance/probe-ledger.jsonl | sha256:3e4611cd041b2ca9ab09d1677969f0c8d9a23d187274737a2102d3189e7bd1c1 | 2026-09-01T12:00:00Z | False |
| path-invocation-272a18e87fab21fc | path-tool-invocation | governance/probe-ledger.jsonl | sha256:f30e7909e2cb155981c060f94f9b724fc2b4564ca86fd03d13e463a5ef89b235 | 2026-09-01T12:00:00Z | False |
| path-invocation-347a7d4ba1b2a0fa | path-tool-invocation | governance/probe-ledger.jsonl | sha256:6fa30b5984ffb5580192d0e2918592d8c5508313e220fc42b9880fa2c5379d89 | 2026-09-01T12:00:00Z | False |
| path-invocation-37d1f78e666dd467 | path-tool-invocation | governance/probe-ledger.jsonl | sha256:915e78af9d638222775bcc08fbc46031571a2a5bbab5a1601821886bc3b4e864 | 2026-09-01T12:00:00Z | False |
| path-invocation-400019bbedc16b75 | path-tool-invocation | governance/probe-ledger.jsonl | sha256:7c4677bef5b241d6357a01f0443a6fc6ba20d45ed412a3e6cd8bc123fae36630 | 2026-09-01T12:00:00Z | False |
| path-invocation-68852d2b9d611a52 | path-tool-invocation | governance/probe-ledger.jsonl | sha256:c749c9cbc454c9bffbe124192db2bd215c00bfc93c53b03a7178d8f3531701df | 2026-09-01T12:00:00Z | False |
| path-invocation-7166e9d0b462e868 | path-tool-invocation | governance/probe-ledger.jsonl | sha256:91ee40b989caf95fa9e29a605df9ffb6698a9c855a7591b5d44ac073043304bc | 2026-09-01T12:00:00Z | False |
| path-invocation-8fa2a4cde35188b9 | path-tool-invocation | governance/probe-ledger.jsonl | sha256:acf62be3585d41de396a5ddcc575ef64fea7d9907554b43f3e18b86d6ef6dbfa | 2026-09-01T12:00:00Z | False |
| path-invocation-aea9253c6ec0258e | path-tool-invocation | governance/probe-ledger.jsonl | sha256:b0976aef119f18d26ce451876466a06f51d423c2f45506f745856ddccd3ee378 | 2026-09-01T12:00:00Z | False |
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
