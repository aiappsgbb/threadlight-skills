# AGT governance wiring — author, gate, attest

Governance is proven the way the real Agent Governance Toolkit works: a
committed, schema-valid **policy** that CI **lints, replays, and verifies**, plus
a committed **attestation**. No in-process "governance middleware" is imported —
enforcement is evidenced at CI time, not asserted by a runtime shim. This mirrors
remediation recipe `AGT-001`.

```bash
pip install "agent-governance-toolkit[core]"   # CLI `agt`; [core] enables `agt test`
```

## 1. Author a real policy (`policy.yaml`)

The real schema requires top-level `version` (the ruleset's own semver — **not**
the toolkit version), `name`, and `rules[]`; each rule has `conditions` +
`action` (`allow | deny | audit | block | escalate | rate_limit`). Start from
`references/policy-templates/{default,hitl,pii-deny}.policy.yaml` and declare a
default-deny posture.

```bash
agt lint-policy policy.yaml     # schema check (base install; always available)
```

## 2. Replay expected verdicts (`agt test`)

Commit `agt test` fixtures next to the policy so its behaviour is pinned:

```yaml
# fixtures/policy-fixtures.yaml
- id: shell-exec-is-blocked
  input: { tool: { name: shell_exec } }
  expected_verdict: block
  expected_rule: block-shell-exec
```

```bash
agt test policy.yaml fixtures/   # replay (needs the [core] runtime)
```

> **`agt test` is advisory in 4.1.0.** Its replay path binds an `agent_os`
> `PolicyDocument` model that requires a singular `condition:` and only allows
> `allow|deny|audit|block` — so it rejects the `conditions[]` lists and the
> `escalate` action that `agt lint-policy` and the `agent_os` runtime both
> accept. A HITL (`escalate`) policy therefore lints clean and is valid at
> runtime yet fails replay. Keep `agt test` as a non-blocking signal until the
> toolkit reconciles the schema; `agt lint-policy` + `agt verify` are the
> authoritative gates.

## 3. Gate CI and attest (`agt verify`)

Run governance in CI so it can never be skipped, and commit the OWASP ASI 2026
attestation (`governance-attestation/v1`):

```yaml
# .github/workflows/governance.yml
- run: pip install "agent-governance-toolkit[core]"
- run: agt lint-policy policy.yaml                 # required gate
- name: Replay policy fixtures (advisory)
  continue-on-error: true                          # see the note above
  run: agt test policy.yaml fixtures/
- run: agt verify --badge | tee docs/agt-verifier-report.md   # required gate
```

`agt verify` reports **coverage** of the ten ASI controls. Coverage reflects how
much of the real runtime governance is wired (`agent_compliance` /
`agent_os.integrations.*`); the attestation names any control still absent. Raise
it by wiring the framework integration for your agent runtime — that is the deep
upstream work `foundry-agt` owns.

## 4. (Optional) defence-in-depth evaluators

The real evaluators can also run in the agent's own request path:

```python
from agent_compliance import PromptDefenseEvaluator, PromptDefenseConfig
report = PromptDefenseEvaluator(PromptDefenseConfig()).evaluate(prompt)
if report.is_blocking(min_grade="C"):
    ...  # reject
```

## Score the evidence

```bash
python3 scripts/govern_check.py --target . --emit   # refresh govern-manifest.json
```

A committed policy + `agt test` fixtures + CI gate + `agt verify` attestation is
what `threadlight-govern` scores as `attestation_present` / `ci_gate_present` /
`policy_tests_present`, and what `threadlight-production-ready` reads to flip
pillars 2 + 7 to verified.
