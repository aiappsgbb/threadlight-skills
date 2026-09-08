---
name: threadlight-agentops
description: >-
  Optional per-agent AgentOps lifecycle evidence. Discovers agentops.yaml opt-in,
  validates pinned native artifacts, integrity, independently scoped run binding,
  freshness, Doctor and release consistency, then emits specs/agentops-manifest.json.
  USE FOR: AgentOps evidence, AOPS-001, Doctor evidence, release evidence, AgentOps
  lifecycle conformance. DO NOT USE FOR: installing or authoring AgentOps config
  (use foundry-agentops), running paid evaluations, Citadel, runtime governance,
  deployment completeness or final production readiness.
metadata:
  version: "0.1.0"
---

# Threadlight AgentOps

An opt-in evidence adapter, **not a readiness certification or another domain
engine**. `threadlight-evals`, `threadlight-redteam`, `threadlight-govern` and
`threadlight-cicd` retain their responsibilities. Citadel is unchanged.

## Default: bounded, local, read-only

```bash
python3 skills/threadlight-agentops/scripts/agentops_check.py \
  --target . --emit --json --gate --freshness-hours 24
```

Run with Python 3.12+. The assessor uses the standard library, local Git,
existing PyYAML only when root `azure.yaml` needs service discovery, and
(only for signed receipts) existing OpenSSL. It does not install anything,
load `agentops.yaml` as YAML, import the native SDK, authenticate, query Azure,
execute commands suggested by native analysis, or run eval/Doctor.

- `--target`: repository root; every discovered opt-in is assessed.
- `--emit [relative-path]`: explicitly write a metadata-only manifest; defaults to
  `specs/agentops-manifest.json` when supplied without a value. Destinations must
  be JSON files below `specs/`, never native input/output paths.
- `--json`: print only the normalized allowlist, never native output.
- `--gate`: exit **2 only for must-fix evidence/operational findings**. Domain
  quality gates remain the canonical owners' responsibility. Unknown/stale
  evidence exits 0 with `partial`, which is **not a pass**. Invalid invocation,
  unsafe discovery and execution prerequisites exit 1.
- `--freshness-hours`: integer 1–8760, default 24.
- `--agentops-bin`: explicit executable selector forwarded to the approved runtime
  when refreshing; default assessment never invokes it.
- `--refresh-doctor`: delegates to the packaged `threadlight-cicd` runtime's
  existing-owner approved operation. Missing approval, identity isolation,
  destination/capture/retention scope or required runtime support fails closed.
  It never implies permission to run eval or provision anything. Native exit 2
  is preserved. There is no preflight-bypass flag.

## Opt-in and independent binding

Only a regular `agentops.yaml` opts a root in; `.agentops/` alone does not.
Before parsing deployment metadata, a bounded metadata-only marker-presence
check returns no opt-in when no non-cache marker exists. Such repositories need
no PyYAML and do not validate unrelated `azure.yaml` content.
Authoritative candidates are root `azure.yaml` services with
`host: azure.ai.agent` (their `project`, default `.`), directories adjacent to
`.foundry/agent-metadata*.yaml`, and the explicit repository-root opt-in.
Foundry metadata contents and all AgentOps YAML remain opaque.

If any azd/Foundry roots are declared, other arbitrary marker roots are not
adopted. Only when those declarations are absent does bounded recursive fallback
discover generic marker roots. That fallback excludes every dot-directory and
`venv`, `node_modules`, `__pycache__`, `dist`, `build`, `coverage`,
`test`, `tests`, `fixture`, `fixtures`, `example`, `examples`, `doc`, `docs`,
`sample`, `samples`, `skills`, `catalog` (case-insensitive named exclusions).
The `.foundry` metadata-marker check is the sole hidden-directory inspection.
An explicitly declared azd service project can select a deployed agent under
`examples/` or another fallback-excluded folder; unsafe paths and symlinks
remain prohibited. This avoids adopting catalog examples and test fixtures.

Agent keys hash the full repository-relative root, avoiding basename collisions.
An unambiguous azd service adds optional `service` metadata; roots remain unique.
Symlink markers and unsafe artifact paths are rejected, never followed.
No opt-in yields `not-applicable`, an empty agents list and no penalty.

Native `results.version == 1` and `evidence.version == 1` belong to
`agentops-accelerator==0.14.0`, upstream commit
`fb5c93eee489c71ef4084fa209adae24f762e3d7`. The unversioned red-team dataclass is a
different contract. Future versions are unverified, not silently accepted.

Native evidence does **not** independently bind repository/commit/environment.
`evidence.target` is derived from eval and is not an identity probe. Accordingly,
unsigned native files alone remain partial. Ordinary adoption requires no PKI:

1. A previously committed, independently approved binding policy.
2. An actual new run observed by the packaged runtime in the same process,
   recording current commit, clean source state, config/dataset/artifact hashes,
   approved target/environment hashes, run identity and execution-approval hash.
3. The original bounded native artifacts, including distinct run and latest
   result copies with identical bytes.

The runtime calls `begin_observation` before its real bounded command and
`finish_observation` afterward. The opaque in-process token is one-use; unchanged
old outputs and timestamps outside the invocation cannot be relabeled as a new
run. Doctor refresh requires an already validated eval binding and preserves it.
Evaluation-only capture can verify eval evidence while absent Doctor remains
unverified; it does not implicitly run Doctor.

This is **local process provenance, not Azure authentication or attestation**.
It assumes the existing approved runner/local host and reviewed checkout are
trusted. A user controlling all local files can forge local provenance; hashes
are consistency checks, not signatures. Required authenticated provenance may
opt into existing-owner signatures with `require_signature: true` and a committed
`trust_key`. No new signing authority, keys or infrastructure are created.
Dirty input trees are unverified; a changed config or artifact hash is invalid.

## Approved native operation and optional signed import

The available `foundry-agentops` skill was merged in awesome-gbb at
`2db28d1f52bf288f2d0fd40b7c8beb913ceeee09` (reviewed source `96b30384`).
Use its pinned runbook for operations. **Before** any native analysis/eval/Doctor,
the existing owner must approve the exact root, identity selector and isolated
credential directories, target/environment, inference costs, telemetry
destination/capture/retention (including native exporter/auto-discovery paths),
and private output destinations. `AZURE_CONFIG_DIR`, credential-empty
`AZD_CONFIG_DIR`, and the approved `AZURE_TOKEN_CREDENTIALS` remain unchanged
through that operation. No login, resources, RBAC or scope expansion is performed
by this skill. Native analysis can inspect remote datasets; it is not assumed
offline.

The trusted observer records real raw JSON from
`agentops eval analyze --dir . --format json`; its version-1 `config_status`,
`dataset_status`, target kind, directory and adaptation status are checked.
There is no native `readiness` field substitution. The packaged runtime records
the actual invocation and independent approved scope rather than trusting
`latest` or an observer-authored assertion. Retain native payloads privately under the existing approval;
unset `GITHUB_STEP_SUMMARY` for native eval and do not upload raw reports.

The CI composition owner supplies `threadlight-cicd/scripts/agentops_runtime.py`
for explicitly approved eval/Doctor operations. For Doctor refresh from this skill:

```bash
python3 skills/threadlight-agentops/scripts/agentops_check.py \
  --target . --refresh-doctor --emit --json --gate
```

All authorization checks precede the native operation. Keep private source
artifacts until canonical eval/red-team/governance/readiness consumers have
completed their hash checks; raw cleanup before then invalidates validation.

**Optional signed import only:** if existing release controls already produced
a signed observed record and detached signature, they can be imported offline:

```bash
python3 skills/threadlight-agentops/scripts/bundle_receipt.py \
  --target . --agent-root . \
  --record .agentops/operations/approved-run.json \
  --signature .agentops/operations/approved-run.sig
```

The helper verifies signature, current committed policy/key, scope and all
artifact hashes before installing `.agentops/threadlight/receipt.{json,sig}`.
It refuses existing destinations: archive them under existing retention controls
before a later import. This optional importer creates no keys, signs nothing and
executes no native operation. A successful import can contain stale or blocked native evidence;
run the assessor next. Do not retroactively sign old unbound results as a new run.

## Interpretation

`operational`, `partial`, `blocked` and `not-applicable` concern evidence
conformance. A valid eval score of 4 against a threshold of 5 remains a verified
**quality failure**. A correctly captured critical Doctor finding remains a
blocker. Neither is production readiness. Supplementary governance metadata
always sets runtime/policy/attestation verification to false.

## References

| Reference | Contract |
|---|---|
| [Artifact mapping](references/artifact-mapping.md) | Native models, ownership, dedup and privacy |
| [Receipt contract](references/receipt-contract.md) | Independent policy, local observations, optional signatures and trust limits |
| [Execution approval](references/execution-approval.md) | Packaged native operations, existing approval fields, identity and export prerequisites |
| [Manifest schema](references/agentops-manifest.schema.json) | Metadata-only public shape; shared validator also rechecks current evidence |
| [Fixture scope](references/fixtures/README.md) | Synthetic bounded fixture builder and negative coverage |

Validation: `python3 -m unittest discover -s skills/threadlight-agentops/tests -p 'test_*.py' -q`
in an isolated Python 3.12+ environment. These tests are offline contract evidence,
not native package installation, Azure authentication, live runs or readiness.
