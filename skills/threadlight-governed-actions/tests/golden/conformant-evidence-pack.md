# Governance Evidence Pack

## Scope and trust model

- Assessor: threadlight-governed-actions v2.0.0 (adapter: maf/v1), phase `pre-deploy`.
- Source: `octo-org/governed-actions-fixtures` @ `0123456789abcdef0123456789abcdef01234567` (dirty: False).
- Agent Hooks is cooperative/alpha and is never treated as this assessment's security boundary.
- Conformance recorded in this pack is never a certification.
- GitHub Copilot coding agent's own internal reasoning/tool-calling loop is never intercepted by this assessment; only the PR/CI/deployment supply chain a change travels through is assessed.
- `executed=True/pass` means the declared local application dispatch was executed under hermetic conformance; it is not deployed production enforcement and requires live deployed version/image/policy evidence.
- Provider-hosted tool side effects without an equivalent, independently verified server-side control are not supported by this assessment's mediation model.
- Tool services are expected to independently re-check authorization, idempotency, and transaction boundaries; this assessment never substitutes for that.

## Architecture and data flow

- 2 declared consequential action(s) across 10 traced mediation path(s).
- 41 application-path probe result(s) recorded.

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

- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-3f5a37daf58d0e9c3554ba271e6debfa54087a51c217a1072ea200ce6f268426, sha256:25526bc0da5d1228c8ea0bbabd91be6e776a862506e098c3da6ea2946cd67bf2
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-5320a1cb731b620f1781eb8812bf0981604adfcd6bd4be5e1f3221af9e229e58, sha256:98fb1c0fb6872e23ee43ecc500c8aca4db964772ef09c28fe410c40802159b4f
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-551e9c1e70fe7c89bee7d7fb736c6f773b48e2d7d030f3e71474eb91ac7da38a, sha256:6b7522585bfa3a7a4c498a495a2513bfb745dd5ddabf8eb4edc758ef5fb3da65
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-5691b597c749a38481bc884ff61a9b116850cec3834c327d4039bd7ea3685e1d, sha256:add167ecaa19bd77a5db330b15fdb28626d27dec491495615b25a3384afd1ad8
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-5cf7f0c4918f4f355a42eb2ffe86da837712cfe5d71530fd2250fd84f1a9bb0c, sha256:c593690c5ca63ca23b76938ec67628137ae8faf0c6017790e184eaeb33bed8c8
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-6fa6ace5627cbb759e265868885204b7210d6cec45de7c617acc411ce869e92b, sha256:890636974f81b6fd3a70ee47419806b268605bffcf50608254ac8ea8889928a5
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-83055e3eb84e6756a3fcebd596ebcd690464ad0e396103c568a35828d9fc2141, sha256:7fcc9bc13f0f9d11ab9a43fa537d7a639da4870436ae47a067c72a9d7aafad97
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-90dbce3dd229d475d6bb1484c42481449dc608fef25a636d792ca6b4d4f075c7, sha256:85e2f35dafc595107b0b0c8de43b6b7e5fc4eae6c9cf27a801d2f57f9162e60b
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-d5b6e60e6f416bf68081a97a5a28b47ecb45916737d495315bd184eb9ed95e24, sha256:535a1397eae8ba9242b4729ecaf7971f51b5dadbb05e7a9a21f36316d035a68e
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-db9ccff3e2563424688935a2c902251dd097fa1118cbe6804a071efc25c2dea1, sha256:4b04eb784efe559c59fe8f0ccb6cd9d35e99119b182c626ca67a288f2becbdd6
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-e326423169bfe8a9837453d18bb1a93a4dc0d6a88f2e999a12a1d6ded54fd24e, sha256:17cd3433faf45558bb834cf1a122bcf46d02147ddc87d0028681ae67c65141b3
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-e5d433ae77d3797b8730a5696d2805a9947539f0700e0e7b250d3039fd163f12, sha256:538fe7f57387468db9554f6de70243d94a49849217fb6b12f01e6a7d1ca96aba
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-eeec48617ed14b24dca7f16abab3c1d0aed37d4631103257c0079fc68a31a52b, sha256:98fb1c0fb6872e23ee43ecc500c8aca4db964772ef09c28fe410c40802159b4f
- `approval-anti-replay` action=`payments.refund` path=`None` status=pass reason=approval-anti-replay-enforced evidence=approval-ledger-records-f6de8371efba654466f5995f140dbf1b2c41b62c8859bbfb5d5fce3ee70ba500, sha256:e6bffa55d782792dc531cf4642e5cf5612dede6164931dc793dbaa7c16e154b9
- `crash` action=`payments.refund` path=`None` status=pass reason=crash-blocked evidence=audit-0001~2bbdaccc2f5e1092
- `deny` action=`payments.refund` path=`None` status=pass reason=deny-enforced evidence=audit-0001~381f4739271102cd
- `malformed-verdict` action=`payments.refund` path=`None` status=pass reason=malformed-verdict-blocked evidence=audit-0001~6037364472d45cf2
- `output-mediation` action=`payments.refund` path=`None` status=pass reason=output-mediation-enforced evidence=output-ledger-records-432eb8c13a8986108d1f5b27e529a83be6017081663ff3f0b4d257eb4f651817
- `path-dispatch-allow` action=`customer.lookup` path=`1e5fd34aca356abe` status=pass reason=path-dispatch-mediated evidence=path-decision-1e5fd34aca356abe~15b83f4eed0f0670, path-invocation-1e5fd34aca356abe, path-resolved-1e5fd34aca356abe, path-routing-target-1e5fd34aca356abe
- `path-dispatch-allow` action=`customer.lookup` path=`347a7d4ba1b2a0fa` status=pass reason=path-dispatch-mediated evidence=path-decision-347a7d4ba1b2a0fa~4f4dc359d0d76064, path-invocation-347a7d4ba1b2a0fa, path-resolved-347a7d4ba1b2a0fa, path-routing-target-347a7d4ba1b2a0fa
- `path-dispatch-allow` action=`customer.lookup` path=`37d1f78e666dd467` status=pass reason=path-dispatch-mediated evidence=path-decision-37d1f78e666dd467~e72d250a4cc07f1d, path-invocation-37d1f78e666dd467, path-resolved-37d1f78e666dd467, path-routing-target-37d1f78e666dd467
- `path-dispatch-allow` action=`customer.lookup` path=`7166e9d0b462e868` status=pass reason=path-dispatch-mediated evidence=path-decision-7166e9d0b462e868~64152c5dac2257e2, path-invocation-7166e9d0b462e868, path-resolved-7166e9d0b462e868, path-routing-target-7166e9d0b462e868
- `path-dispatch-allow` action=`customer.lookup` path=`aea9253c6ec0258e` status=pass reason=path-dispatch-mediated evidence=path-decision-aea9253c6ec0258e~235cda31c998fdd0, path-invocation-aea9253c6ec0258e, path-resolved-aea9253c6ec0258e, path-routing-target-aea9253c6ec0258e
- `path-dispatch-allow` action=`payments.refund` path=`099ebdd0357f3031` status=pass reason=path-dispatch-mediated evidence=path-decision-099ebdd0357f3031~38951f091a6f519f, path-invocation-099ebdd0357f3031, path-resolved-099ebdd0357f3031, path-routing-target-099ebdd0357f3031
- `path-dispatch-allow` action=`payments.refund` path=`272a18e87fab21fc` status=pass reason=path-dispatch-mediated evidence=path-decision-272a18e87fab21fc~9647d10ede992135, path-invocation-272a18e87fab21fc, path-resolved-272a18e87fab21fc, path-routing-target-272a18e87fab21fc
- `path-dispatch-allow` action=`payments.refund` path=`400019bbedc16b75` status=pass reason=path-dispatch-mediated evidence=path-decision-400019bbedc16b75~057126431c70074f, path-invocation-400019bbedc16b75, path-resolved-400019bbedc16b75, path-routing-target-400019bbedc16b75
- `path-dispatch-allow` action=`payments.refund` path=`68852d2b9d611a52` status=pass reason=path-dispatch-mediated evidence=path-decision-68852d2b9d611a52~e28e888cc45316a0, path-invocation-68852d2b9d611a52, path-resolved-68852d2b9d611a52, path-routing-target-68852d2b9d611a52
- `path-dispatch-allow` action=`payments.refund` path=`8fa2a4cde35188b9` status=pass reason=path-dispatch-mediated evidence=path-decision-8fa2a4cde35188b9~12e615d450ed339b, path-invocation-8fa2a4cde35188b9, path-resolved-8fa2a4cde35188b9, path-routing-target-8fa2a4cde35188b9
- `path-dispatch-deny` action=`customer.lookup` path=`1e5fd34aca356abe` status=pass reason=path-dispatch-denied evidence=path-decision-1e5fd34aca356abe~671eba3a6bd58b04, path-resolved-1e5fd34aca356abe, path-routing-target-1e5fd34aca356abe
- `path-dispatch-deny` action=`customer.lookup` path=`347a7d4ba1b2a0fa` status=pass reason=path-dispatch-denied evidence=path-decision-347a7d4ba1b2a0fa~519d6e0590463f52, path-resolved-347a7d4ba1b2a0fa, path-routing-target-347a7d4ba1b2a0fa
- `path-dispatch-deny` action=`customer.lookup` path=`37d1f78e666dd467` status=pass reason=path-dispatch-denied evidence=path-decision-37d1f78e666dd467~95b040ae42f63597, path-resolved-37d1f78e666dd467, path-routing-target-37d1f78e666dd467
- `path-dispatch-deny` action=`customer.lookup` path=`7166e9d0b462e868` status=pass reason=path-dispatch-denied evidence=path-decision-7166e9d0b462e868~848f6155ee90dea6, path-resolved-7166e9d0b462e868, path-routing-target-7166e9d0b462e868
- `path-dispatch-deny` action=`customer.lookup` path=`aea9253c6ec0258e` status=pass reason=path-dispatch-denied evidence=path-decision-aea9253c6ec0258e~88ac5df90a7668f8, path-resolved-aea9253c6ec0258e, path-routing-target-aea9253c6ec0258e
- `path-dispatch-deny` action=`payments.refund` path=`099ebdd0357f3031` status=pass reason=path-dispatch-denied evidence=path-decision-099ebdd0357f3031~20df8c2c566880bc, path-resolved-099ebdd0357f3031, path-routing-target-099ebdd0357f3031
- `path-dispatch-deny` action=`payments.refund` path=`272a18e87fab21fc` status=pass reason=path-dispatch-denied evidence=path-decision-272a18e87fab21fc~b8868527c60d8170, path-resolved-272a18e87fab21fc, path-routing-target-272a18e87fab21fc
- `path-dispatch-deny` action=`payments.refund` path=`400019bbedc16b75` status=pass reason=path-dispatch-denied evidence=path-decision-400019bbedc16b75~88d66a1681bc9589, path-resolved-400019bbedc16b75, path-routing-target-400019bbedc16b75
- `path-dispatch-deny` action=`payments.refund` path=`68852d2b9d611a52` status=pass reason=path-dispatch-denied evidence=path-decision-68852d2b9d611a52~de9215ccb5033c58, path-resolved-68852d2b9d611a52, path-routing-target-68852d2b9d611a52
- `path-dispatch-deny` action=`payments.refund` path=`8fa2a4cde35188b9` status=pass reason=path-dispatch-denied evidence=path-decision-8fa2a4cde35188b9~3f5a128ce7ebb9cf, path-resolved-8fa2a4cde35188b9, path-routing-target-8fa2a4cde35188b9
- `payload-free-audit` action=`payments.refund` path=`None` status=pass reason=payload-free-audit-enforced evidence=audit-approval-audit-probe-nonce, audit-ledger-records-01e347c048b95c0544f4e0e542f1c13465e0bdb5acf4085aeaf43154a166a454
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
| app/agent.py | static-file-hash | app/agent.py | sha256:71915443d00d8fc82744d3c5ddfeb4e79fdb6afc93fa6a84e788b8d0bb283f9f | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-3f5a37daf58d0e9c3554ba271e6debfa54087a51c217a1072ea200ce6f268426 | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:3f5a37daf58d0e9c3554ba271e6debfa54087a51c217a1072ea200ce6f268426 | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-5320a1cb731b620f1781eb8812bf0981604adfcd6bd4be5e1f3221af9e229e58 | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:5320a1cb731b620f1781eb8812bf0981604adfcd6bd4be5e1f3221af9e229e58 | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-551e9c1e70fe7c89bee7d7fb736c6f773b48e2d7d030f3e71474eb91ac7da38a | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:551e9c1e70fe7c89bee7d7fb736c6f773b48e2d7d030f3e71474eb91ac7da38a | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-5691b597c749a38481bc884ff61a9b116850cec3834c327d4039bd7ea3685e1d | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:5691b597c749a38481bc884ff61a9b116850cec3834c327d4039bd7ea3685e1d | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-5cf7f0c4918f4f355a42eb2ffe86da837712cfe5d71530fd2250fd84f1a9bb0c | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:5cf7f0c4918f4f355a42eb2ffe86da837712cfe5d71530fd2250fd84f1a9bb0c | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-6fa6ace5627cbb759e265868885204b7210d6cec45de7c617acc411ce869e92b | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:6fa6ace5627cbb759e265868885204b7210d6cec45de7c617acc411ce869e92b | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-83055e3eb84e6756a3fcebd596ebcd690464ad0e396103c568a35828d9fc2141 | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:83055e3eb84e6756a3fcebd596ebcd690464ad0e396103c568a35828d9fc2141 | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-90dbce3dd229d475d6bb1484c42481449dc608fef25a636d792ca6b4d4f075c7 | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:90dbce3dd229d475d6bb1484c42481449dc608fef25a636d792ca6b4d4f075c7 | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-d5b6e60e6f416bf68081a97a5a28b47ecb45916737d495315bd184eb9ed95e24 | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:d5b6e60e6f416bf68081a97a5a28b47ecb45916737d495315bd184eb9ed95e24 | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-db9ccff3e2563424688935a2c902251dd097fa1118cbe6804a071efc25c2dea1 | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:db9ccff3e2563424688935a2c902251dd097fa1118cbe6804a071efc25c2dea1 | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-e326423169bfe8a9837453d18bb1a93a4dc0d6a88f2e999a12a1d6ded54fd24e | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:e326423169bfe8a9837453d18bb1a93a4dc0d6a88f2e999a12a1d6ded54fd24e | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-e5d433ae77d3797b8730a5696d2805a9947539f0700e0e7b250d3039fd163f12 | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:e5d433ae77d3797b8730a5696d2805a9947539f0700e0e7b250d3039fd163f12 | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-eeec48617ed14b24dca7f16abab3c1d0aed37d4631103257c0079fc68a31a52b | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:eeec48617ed14b24dca7f16abab3c1d0aed37d4631103257c0079fc68a31a52b | 2026-09-01T12:00:00Z | False |
| approval-ledger-records-f6de8371efba654466f5995f140dbf1b2c41b62c8859bbfb5d5fce3ee70ba500 | approval-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:f6de8371efba654466f5995f140dbf1b2c41b62c8859bbfb5d5fce3ee70ba500 | 2026-09-01T12:00:00Z | False |
| audit-0001~2bbdaccc2f5e1092 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:2bbdaccc2f5e1092140a64b0a9da17e059dbcecff004e448e47e3d0fb6ccb8f2 | 2026-09-01T12:00:00Z | False |
| audit-0001~32eb7a5316406d67 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:32eb7a5316406d67d326aec85eec3616fcd2b068e22ff7ee271ff803253b408b | 2026-09-01T12:00:00Z | False |
| audit-0001~381f4739271102cd | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:381f4739271102cd6f2d740cd27fa73e10ea2478a8fee0cc07f35c0063f1a60a | 2026-09-01T12:00:00Z | False |
| audit-0001~6037364472d45cf2 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:6037364472d45cf2d6402547b61d7071f5920343283f0acc72672e7c58cd6c5e | 2026-09-01T12:00:00Z | False |
| audit-0001~f9cf5a5cf02e20f0 | probe-audit-ledger-record | governance/probe-ledger.jsonl | sha256:f9cf5a5cf02e20f0cc808a91b82c374be4d27b15183374c846497c969b4a3f09 | 2026-09-01T12:00:00Z | False |
| audit-approval-audit-probe-nonce | probe-audit-record | governance/nonce-ledger.jsonl#assessment-isolated | sha256:593790675c1fa4169819c010dbe2aa4720a72f23015ab09f9c7f12a8c9966f22 | 2026-09-01T12:00:00Z | False |
| audit-ledger-records-01e347c048b95c0544f4e0e542f1c13465e0bdb5acf4085aeaf43154a166a454 | audit-ledger-records | governance/nonce-ledger.jsonl#assessment-isolated | sha256:01e347c048b95c0544f4e0e542f1c13465e0bdb5acf4085aeaf43154a166a454 | 2026-09-01T12:00:00Z | False |
| ghcp-workflows | file-set | .github/workflows/governed-actions.yml | sha256:16819e03d140b14bd7bf5d4007a8b0755170e7881180bcfab2cef328abac3fc1 | unknown | False |
| output-ledger-records-432eb8c13a8986108d1f5b27e529a83be6017081663ff3f0b4d257eb4f651817 | output-ledger-records | governance/probe-ledger.jsonl#assessment-isolated | sha256:432eb8c13a8986108d1f5b27e529a83be6017081663ff3f0b4d257eb4f651817 | 2026-09-01T12:00:00Z | False |
| path-decision-099ebdd0357f3031~20df8c2c566880bc | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:20df8c2c566880bcf3f978e6bb6e902f66383588867b36ac45328022ed143ce6 | 2026-09-01T12:00:00Z | False |
| path-decision-099ebdd0357f3031~38951f091a6f519f | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:38951f091a6f519f0cbadfa8a7a6729768412083d85fc8a41faef4ad5c6cbc9a | 2026-09-01T12:00:00Z | False |
| path-decision-1e5fd34aca356abe~15b83f4eed0f0670 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:15b83f4eed0f06706c8c8eb0f4f473e4cb2da84dbc9f82c22d623b943d08cb10 | 2026-09-01T12:00:00Z | False |
| path-decision-1e5fd34aca356abe~671eba3a6bd58b04 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:671eba3a6bd58b0402606d90aec7f1704a44b506e5e91e5e05ab4e4f42c618f3 | 2026-09-01T12:00:00Z | False |
| path-decision-272a18e87fab21fc~9647d10ede992135 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:9647d10ede9921353e8b7e23da0a7e5db890b35ea9052fea30fea49a2ab8250e | 2026-09-01T12:00:00Z | False |
| path-decision-272a18e87fab21fc~b8868527c60d8170 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:b8868527c60d8170db3a5a930066a84bb0a7ae2627c59841efba8531e96057b5 | 2026-09-01T12:00:00Z | False |
| path-decision-347a7d4ba1b2a0fa~4f4dc359d0d76064 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:4f4dc359d0d760641fbdf907b97e69c0ff4527748bc8ac57181ff8de67012cb9 | 2026-09-01T12:00:00Z | False |
| path-decision-347a7d4ba1b2a0fa~519d6e0590463f52 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:519d6e0590463f52db618b267fef0d82eb6d525139d001ad50baf971acecb32b | 2026-09-01T12:00:00Z | False |
| path-decision-37d1f78e666dd467~95b040ae42f63597 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:95b040ae42f63597e3a961f3a867bb1001752be45f50a2fb0e42760564b4c37b | 2026-09-01T12:00:00Z | False |
| path-decision-37d1f78e666dd467~e72d250a4cc07f1d | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:e72d250a4cc07f1da3d53a9699c45681c20031fc72d84dcb1851930d69808770 | 2026-09-01T12:00:00Z | False |
| path-decision-400019bbedc16b75~057126431c70074f | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:057126431c70074f0b85646e76a7e7cfcf32af7cef6f2c4505991390d0c7cccc | 2026-09-01T12:00:00Z | False |
| path-decision-400019bbedc16b75~88d66a1681bc9589 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:88d66a1681bc958943aae0af75b4f27986c31cdaeca85e99e5732654065cd6c2 | 2026-09-01T12:00:00Z | False |
| path-decision-68852d2b9d611a52~de9215ccb5033c58 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:de9215ccb5033c58de6fa2d381c9004e2c5fc07bd786171be11c41e600268ae6 | 2026-09-01T12:00:00Z | False |
| path-decision-68852d2b9d611a52~e28e888cc45316a0 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:e28e888cc45316a09db882c574bb3a37601dd902608cf712912643376e2b40c6 | 2026-09-01T12:00:00Z | False |
| path-decision-7166e9d0b462e868~64152c5dac2257e2 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:64152c5dac2257e2fe2e855ef3895bc395491c79dc1356d9ea263b1492039071 | 2026-09-01T12:00:00Z | False |
| path-decision-7166e9d0b462e868~848f6155ee90dea6 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:848f6155ee90dea6cf438098b452dca63c37e20cb29fabaa3609f39b50e82e91 | 2026-09-01T12:00:00Z | False |
| path-decision-8fa2a4cde35188b9~12e615d450ed339b | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:12e615d450ed339b69224c4bfbfe09af4f846fc9adabc1f8a2f74a134bb4d579 | 2026-09-01T12:00:00Z | False |
| path-decision-8fa2a4cde35188b9~3f5a128ce7ebb9cf | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:3f5a128ce7ebb9cf364e799b8af8a47b270f53c595fe5c632cae3d75120757bd | 2026-09-01T12:00:00Z | False |
| path-decision-aea9253c6ec0258e~235cda31c998fdd0 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:235cda31c998fdd08e1864198ba4426cb3f9ecc9255a70eb9a79f698fee298a2 | 2026-09-01T12:00:00Z | False |
| path-decision-aea9253c6ec0258e~88ac5df90a7668f8 | path-pre-action-decision | assessor:execution-path-proof-channel | sha256:88ac5df90a7668f8f925d03988f99efec7a119552f35370f827ad04a2607e124 | 2026-09-01T12:00:00Z | False |
| path-invocation-099ebdd0357f3031 | path-tool-invocation | assessor:execution-path-proof-channel | sha256:b4d93693d664589c4ac5b024a2ab5ea777a15761b1fc19f1bf434cc88b0c02b8 | 2026-09-01T12:00:00Z | False |
| path-invocation-1e5fd34aca356abe | path-tool-invocation | assessor:execution-path-proof-channel | sha256:c22475269c5237f80f181bd8bb142150b00f035a4d8dfe60611615ae8b08f388 | 2026-09-01T12:00:00Z | False |
| path-invocation-272a18e87fab21fc | path-tool-invocation | assessor:execution-path-proof-channel | sha256:5fe120d92b072d80f0e095aad26a3544e1f48640bd96c846b22b2430b3609039 | 2026-09-01T12:00:00Z | False |
| path-invocation-347a7d4ba1b2a0fa | path-tool-invocation | assessor:execution-path-proof-channel | sha256:cbe4170855d1ed1355ec1af0db3a5eba31aa8b2c29a873a0a909c58ab89589b8 | 2026-09-01T12:00:00Z | False |
| path-invocation-37d1f78e666dd467 | path-tool-invocation | assessor:execution-path-proof-channel | sha256:e48fd17028467dc7e9b976d68d4b519d7d3630565f1d4fd6032ea3a13e26f2d8 | 2026-09-01T12:00:00Z | False |
| path-invocation-400019bbedc16b75 | path-tool-invocation | assessor:execution-path-proof-channel | sha256:7130602caf8586ff18dd0e32297b738fb93b67534d629ce0f57e01e4bb4bc668 | 2026-09-01T12:00:00Z | False |
| path-invocation-68852d2b9d611a52 | path-tool-invocation | assessor:execution-path-proof-channel | sha256:3d64b22e2c7438b8bf2bb8670bd4fd566a1e690f8a4eb9b7640f4c0d68274205 | 2026-09-01T12:00:00Z | False |
| path-invocation-7166e9d0b462e868 | path-tool-invocation | assessor:execution-path-proof-channel | sha256:bc5da8cd88173127941951951c27fd51742e0c04121fcada4cc32f8b61d56ac2 | 2026-09-01T12:00:00Z | False |
| path-invocation-8fa2a4cde35188b9 | path-tool-invocation | assessor:execution-path-proof-channel | sha256:612cfc77afae279345bfa3d65fb90b42ff802373220de378bc27ef46681ce312 | 2026-09-01T12:00:00Z | False |
| path-invocation-aea9253c6ec0258e | path-tool-invocation | assessor:execution-path-proof-channel | sha256:9861f9dad91b7611bb3af3040bf03a471f11599c75c7ebcc1af07e45d327c3b2 | 2026-09-01T12:00:00Z | False |
| path-resolved-099ebdd0357f3031 | path-resolved-function | assessor:execution-path-proof-channel | sha256:1bd288fc3c02a915e1a85186e23b2113578995b6c77e95c08bdb2f1e62ed099d | 2026-09-01T12:00:00Z | False |
| path-resolved-1e5fd34aca356abe | path-resolved-function | assessor:execution-path-proof-channel | sha256:c102519ea0f2d2e397b74f7e9ab6624cfbab74326c4b9d3d1d5d3d90cabb860b | 2026-09-01T12:00:00Z | False |
| path-resolved-272a18e87fab21fc | path-resolved-function | assessor:execution-path-proof-channel | sha256:aba05106dfa3ca22c415232deca1a35369b437d8a9923b3f0079da0b1beb62eb | 2026-09-01T12:00:00Z | False |
| path-resolved-347a7d4ba1b2a0fa | path-resolved-function | assessor:execution-path-proof-channel | sha256:514c3504a24b38f062a2cbe58972642f71ec0813f428933d27daa07ddce887ac | 2026-09-01T12:00:00Z | False |
| path-resolved-37d1f78e666dd467 | path-resolved-function | assessor:execution-path-proof-channel | sha256:4ff6c96b34ccd087ebd5e2be42fd5e686d708f86ef90672fa8838bd83e041221 | 2026-09-01T12:00:00Z | False |
| path-resolved-400019bbedc16b75 | path-resolved-function | assessor:execution-path-proof-channel | sha256:5f5b79e4efa7f9a031475d6557307d594f0dadf4130dab719823c5853f1c718b | 2026-09-01T12:00:00Z | False |
| path-resolved-68852d2b9d611a52 | path-resolved-function | assessor:execution-path-proof-channel | sha256:69367b22ca11a3505f3cf3f73294d22df28cdbf0c2169df22c31b4ebc551a60a | 2026-09-01T12:00:00Z | False |
| path-resolved-7166e9d0b462e868 | path-resolved-function | assessor:execution-path-proof-channel | sha256:758e65af2b8888278f30f64072726a14a8913a85be4a8ca9cff58bced8ff9f09 | 2026-09-01T12:00:00Z | False |
| path-resolved-8fa2a4cde35188b9 | path-resolved-function | assessor:execution-path-proof-channel | sha256:f0eda3228add273c2400e78c3d109d06f99f5768abdb7e96306eebea963a919f | 2026-09-01T12:00:00Z | False |
| path-resolved-aea9253c6ec0258e | path-resolved-function | assessor:execution-path-proof-channel | sha256:26270100ea0c9da79cce1cb63f46c90094b5b11c73c585fe6983f60d1d4442ae | 2026-09-01T12:00:00Z | False |
| path-routing-target-099ebdd0357f3031 | path-routing-table-target | assessor:execution-path-proof-channel | sha256:f48462314724a43d12a7e276d534e51b7cf0dce001aef23a5dd2f42c01b41e17 | 2026-09-01T12:00:00Z | False |
| path-routing-target-1e5fd34aca356abe | path-routing-table-target | assessor:execution-path-proof-channel | sha256:38dd4fc7bb6e09acb98abe8a6962a2d2b9d778b32db084c3b265124b055ceb16 | 2026-09-01T12:00:00Z | False |
| path-routing-target-272a18e87fab21fc | path-routing-table-target | assessor:execution-path-proof-channel | sha256:62102606b74df631239ccca8d508eff80e409ed9a4fa9a334e56bd55de361606 | 2026-09-01T12:00:00Z | False |
| path-routing-target-347a7d4ba1b2a0fa | path-routing-table-target | assessor:execution-path-proof-channel | sha256:46deea5b5748f01713d995f02bf4b2c5941f2fbb2a5e0d7bf2d3a7d8d3556668 | 2026-09-01T12:00:00Z | False |
| path-routing-target-37d1f78e666dd467 | path-routing-table-target | assessor:execution-path-proof-channel | sha256:a2c754c6e8ec0f0f7deeca8b68ccc41146fa83124edcc2e914a2bd53e67289db | 2026-09-01T12:00:00Z | False |
| path-routing-target-400019bbedc16b75 | path-routing-table-target | assessor:execution-path-proof-channel | sha256:c4fd06d686a0b73588af6fee500592c32ef6eab93d86e935c48c3591c25550c6 | 2026-09-01T12:00:00Z | False |
| path-routing-target-68852d2b9d611a52 | path-routing-table-target | assessor:execution-path-proof-channel | sha256:c7aabe6a3885e8dee78e1599f5f0a64544694ed56b11c20cb290e045bd05cd69 | 2026-09-01T12:00:00Z | False |
| path-routing-target-7166e9d0b462e868 | path-routing-table-target | assessor:execution-path-proof-channel | sha256:90e32aea7d26a4a7960003f80d2d7db328f47f34d4a8375022971387ed9b3542 | 2026-09-01T12:00:00Z | False |
| path-routing-target-8fa2a4cde35188b9 | path-routing-table-target | assessor:execution-path-proof-channel | sha256:c0196a5f737a410763f340bc8c09064a120a8bc9ad77da5983333e8f4432bf61 | 2026-09-01T12:00:00Z | False |
| path-routing-target-aea9253c6ec0258e | path-routing-table-target | assessor:execution-path-proof-channel | sha256:6965e6b48ca0323f828bc7bfc45415cf2b8c308c3c9fc518ab495d07f1376aa0 | 2026-09-01T12:00:00Z | False |
| sha256:17cd3433faf45558bb834cf1a122bcf46d02147ddc87d0028681ae67c65141b3 | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:17cd3433faf45558bb834cf1a122bcf46d02147ddc87d0028681ae67c65141b3 | 2026-09-01T12:00:00Z | False |
| sha256:25526bc0da5d1228c8ea0bbabd91be6e776a862506e098c3da6ea2946cd67bf2 | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:25526bc0da5d1228c8ea0bbabd91be6e776a862506e098c3da6ea2946cd67bf2 | 2026-09-01T12:00:00Z | False |
| sha256:4b04eb784efe559c59fe8f0ccb6cd9d35e99119b182c626ca67a288f2becbdd6 | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:4b04eb784efe559c59fe8f0ccb6cd9d35e99119b182c626ca67a288f2becbdd6 | 2026-09-01T12:00:00Z | False |
| sha256:535a1397eae8ba9242b4729ecaf7971f51b5dadbb05e7a9a21f36316d035a68e | approval-binding-digest | governance/nonce-ledger.jsonl#assessment-isolated | sha256:535a1397eae8ba9242b4729ecaf7971f51b5dadbb05e7a9a21f36316d035a68e | 2026-09-01T12:00:00Z | False |
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
