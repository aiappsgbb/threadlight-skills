# Native ACS bundles (local validity, not deployed enforcement)

## Exact upstream identity

The shared pin is `skills/_shared/governance-upstream-pin.json`.
AGT 5's runtime distribution is **`agent-governance-toolkit-core==5.0.0`**.
The old `agent-governance-toolkit` installer remains at 4.1.0; asking that
distribution for version 5 was a package-identity error, not a missing AGT release.
The separate **`agent-governance-toolkit-compliance==5.0.0`** supplies the CLI.
Do not attribute `agt verify` to core or install the CLI for this runtime test.

Verified public sources:

- [AGT v5.0.0 tag](https://api.github.com/repos/microsoft/agent-governance-toolkit/git/ref/tags/v5.0.0):
  `c57d9d9a4849556a3c5347d359012d7a85bc3dfb`.
- [Core 5.0.0 wheel metadata](https://pypi.org/pypi/agent-governance-toolkit-core/5.0.0/json):
  `agent_governance_toolkit_core-5.0.0-py3-none-any.whl`,
  SHA256 `6f8494c5ada3d5824f90e56d5e450f8ca6fba9759c107f077bdc0c87b7c496d2`.
- [Compliance 5.0.0](https://pypi.org/pypi/agent-governance-toolkit-compliance/5.0.0/json).
- [ACS 0.3.1b0](https://pypi.org/pypi/agent-control-specification/0.3.1b0/json).
  Its published native wheel is Linux amd64. The tag's policy-engine SDK
  version is independently versioned (b1); this implementation uses the
  **installed b0 API**, not latest main or an implicit upgrade.

The contract tests call **`AgentControl.from_path(str(manifest_path))`** and
**`evaluate_intervention_point(point, snapshot)`** with the default native
dispatcher and OPA **1.18.2**. ACS b0 manifest Rego `data` / `data_paths`
are flattened policy fields, not a nested `adapter_config` YAML property.

## Builder API

Import `policy_bundle` from this skill's `scripts` directory, then call:

```python
from pathlib import Path
from policy_bundle import build_bundle, verify_bundle

bundle = build_bundle(
    source=Path("skills/threadlight-govern/references/policy-templates"),
    destination=Path("policies"),
    policy_id="returns-safe",
    version="1.0.0",
)
verify_bundle(bundle.root, expected_digest=bundle.bundle_digest)
```

The destination parent must exist; the destination itself must not exist.
Every regular source file is included with a canonical relative POSIX path,
byte length and exact-byte SHA256. File contents are **not rewritten**.
The bundle digest hashes sorted, compact UTF-8 JSON containing `schema`,
`policy_id`, `version`, and the file entries. `bundle-metadata.json` contains
that content, `bundle_digest`, and `signature: null`; its own bytes are excluded
from the digest to avoid circular hashing. Verification checks canonical
metadata, the complete file set, and optionally a separately trusted digest.

Symlinks, lexical traversal, remote/escaping references, external annotators,
approval backends, and non-Rego engines are rejected. Local manifest `extends`,
Rego `bundle`, `data`, and `data_paths` are confined to the captured tree.
Paths use ASCII letters/digits/dot/underscore/hyphen components. The actual
native loader validates the staged manifest. A single OS no-replace rename
publishes the fully written, validated directory on Linux/macOS; an existing
destination, race, or validation error never publishes partial content.

**No signing key or signature is generated.** `signature: null` reserves the
metadata slot; a future Key Vault verifier can introduce an externally signed
envelope with algorithm, versioned key ID, signed bundle digest, and signature.
The current verifier rejects non-null signatures rather than pretending to
verify them. Integrity alone establishes neither publisher trust nor expiry.

## Business-specific SAFE example

The Rego example binds `pre_tool_call`, `post_tool_call`, and `output`.
For `returns_apply_decision`, it requires host-verified receipt evidence,
rejects missing/negative/non-numeric refund amounts, and escalates refunds
over 500 without host approval. Other tools are not blanket-denied.
Post-tool/output examples remove only the structured `customer_email` field;
they are **not general PII detection**, prose filtering, or rollback.

Inputs use `input.tool.name`, `input.policy_target.value`, and **host-owned**
`input.snapshot.safe.evidence` / `.escalations`. Hosts must populate and verify
these records independently of model/tool arguments. Tests deliberately show
that argument-level `receipt_verified` / `refund_approved` cannot authorize.
These are synthetic invariant fixtures, not installed host hooks or evidence
of invisible interception of provider-owned tools.

## Offline reporting and proof

`govern_check.py --target PILOT --emit` reads the explicit governance contract
and an optional native bundle at `PILOT/policies` (`--bundle` overrides the
relative path). It reports all declared tool/lifecycle subjects, including
unbound tools in selective/off mode. Missing/invalid engines and local
artifacts never become enforced or observed deployment bindings.

The shared manifest's `offline_evidence` variant has null deployment metadata,
no live probes, and only unverified/unbound/unsupported statuses. It cannot
be combined with the strict live-proof variant. The legacy report path is
preserved; old verdict-based consumers receive no synthetic pass.

From the repository root:

```bash
python3 -m pytest skills/threadlight-govern/tests -q
python3 scripts/ci/run-governance-pin-tests.py
python3 -m pytest skills/_shared/tests -q
```

Ordinary pytest explicitly skips the marked native suite: **skips are not
proof**. The pin runner uses an isolated venv with exact AGT core, ACS, Agent
Hooks and MAF pins, plus checksum-verified OPA. On macOS it uses Docker
`linux/amd64` and published wheels (never a locally compiled ACS wheel).
The configured package mirror may transport wheels; their pinned-package
hashes are checked against public PyPI metadata and installed Python/native
files against wheel contents. Scratch and reports stay in
`.governance-validation/`. The Linux OPA build hash is verified even when the
host is macOS; no Linux executable is launched directly on macOS.

`GOVERNANCE_RUNTIME_CONTRACT=PASS` requires successful real loader/evaluator
tests, including deny/allow/escalate/transform, and a JUnit report with no
skipped/failed/errored cases. This proves **local bundle validity, decisions and native MAF hook enforcement**
with synthetic model/tools, not deployed enforcement. See
[the runtime adapter and corrected CTK](runtime/README.md). Service-side oracles,
approval-service persistence, signed deployment identity and production
assurance remain separate work.
