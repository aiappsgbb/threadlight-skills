# MSA agent-quality S3+S4 (threadlight-design) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two additive requirements to the `threadlight-design` generation contract so the agents/skills it produces are (S4) honest about knowledge boundaries and (S3) reconcile — not concatenate — when multiple skills compose one answer.

**Architecture:** Edit the inline "MUST have" / "Must include" lists in `threadlight-design/SKILL.md` **Step 6** (the authoritative in-repo generation contract — the referenced `references/skill-template.md` / `references/agents-template.md` do not exist in this repo), plus enrich the **Synthesis** row of `references/process-traits.md` so trait detection carries the S3 guidance forward. Bump the skill's semver. No pipeline, orchestrator, Fast-PoC, or SPEC-schema changes.

**Tech Stack:** Markdown skill-authoring files. Verification via `grep`, a semver regex, and the repo's existing `scripts/ci/check-skill-description-length.py` PR guard. No new test files, no CI edits.

---

## Testing approach (read first — why this plan is not classic TDD)

`threadlight-design` is an **instructions-only** skill: its "output" is produced by an LLM following `SKILL.md`, not by a Python module. The repo ships **no test suite** for it and does not reference it in `.github/workflows/python-pytest.yml`. The only automated PR gate that touches it is `scripts/ci/check-skill-description-length.py` (fails if any `skills/*/SKILL.md` frontmatter `description` exceeds 1024 chars — a silent loader-drop guard). **None of these edits touch the frontmatter `description`**, so that guard stays green.

Therefore each task's verification is a **concrete static assertion** (exact `grep` match counts + a semver check + the description-length guard) rather than a `pytest` red/green cycle. Do **not** scaffold a new `tests/` directory or edit CI — that would exceed the approved spec footprint (2 files + version bump) and the user's hard efficiency constraint. Apply every edit by **exact string match** (the `edit` tool), not by line number — line numbers below are location hints only and will shift as edits land.

## Efficiency guardrails (hard constraint — do not violate)

- **Additive only.** Do not restructure Step 6, do not add a Phase/Step, do not add interview questions.
- **S3 clause is conditional** — it must be emitted only for multi-skill answer-composing agents, so single-skill PoCs stay lean.
- **Zero edits** to `orchestrator.py`, `threadlight-auto`, the pipeline order, or any SPEC schema/field.
- Net new generated-instruction text per produced agent: ~2–4 lines.

---

## File Structure

- **Modify:** `skills/threadlight-design/SKILL.md`
  - Step 6 §1 (per-skill "MUST have" list, ~748–753) — add one S4 bullet.
  - Step 6 §2 (AGENTS.md "Must include", the `- Behavioral guidelines` line, ~766) — expand into an S4 (always) + S3 (conditional) sub-list.
  - Frontmatter `metadata.version` (line 18) — `1.8.0` → `1.8.1`.
- **Modify:** `skills/threadlight-design/references/process-traits.md`
  - Synthesis row (line 43) — extend its "Discovery Questions" cell with the S3 reconciliation pointer.

---

## Task 1: S4 grounding-honesty + S3 synthesis in the Step 6 generation contract

Both findings insert into the same Step 6 region of one file, so they land in one coherent commit.

**Files:**
- Modify: `skills/threadlight-design/SKILL.md:748-753` (S4 per-skill bullet)
- Modify: `skills/threadlight-design/SKILL.md:766` (S4 always clause + S3 conditional clause)

- [ ] **Step 1: Add the S4 grounding-honesty bullet to the per-skill "MUST have" list (Step 6 §1)**

Use the `edit` tool. Match this exact block:

```
- Step-by-step procedure (derived from spec process flow)
- Output schema (derived from spec data models)
```

Replace it with (appends one bullet):

```
- Step-by-step procedure (derived from spec process flow)
- Output schema (derived from spec data models)
- **Grounding & honesty contract** (knowledge-backed skills only): name the grounding source and a labeled-degradation fallback — if the source lacks the answer, say so explicitly, and label any general-knowledge answer as such. (Citation is already covered by the spec's § 7 Citation requirement.)
```

- [ ] **Step 2: Expand the AGENTS.md "Behavioral guidelines" line into the S4 (always) + S3 (conditional) contract (Step 6 §2)**

Use the `edit` tool. Match this exact block (two consecutive lines):

```
- Behavioral guidelines
- **Spec reference**: "This agent implements specs/SPEC.md"
```

Replace it with:

```
- Behavioral guidelines. This section MUST include:
  - **Grounding & honesty** (always): "Ground substantive answers in the agent's knowledge source (spec § 7). If the knowledge source does not contain the answer, say so explicitly; answer from general knowledge only when clearly labeled as such. Cite sources per spec § 7 Citation requirement."
  - **Cross-skill synthesis** (only when the agent composes answers from more than one skill — i.e. ≥ 2 knowledge/analysis/synthesis skills, or the **Synthesis** trait was detected): "When more than one skill contributes to a single answer, reconcile rather than concatenate — preserve technical detail, resolve disagreements explicitly, and surface cross-domain trade-offs."
- **Spec reference**: "This agent implements specs/SPEC.md"
```

- [ ] **Step 3: Verify both clauses are present with the expected match counts**

Run:

```bash
cd "$(git rev-parse --show-toplevel)"
echo "grounding (expect 2):" && grep -c "Grounding & honesty" skills/threadlight-design/SKILL.md
echo "synthesis (expect 1):" && grep -c "Cross-skill synthesis" skills/threadlight-design/SKILL.md
echo "conditional wording (expect 1):" && grep -c "only when the agent composes answers from more than one skill" skills/threadlight-design/SKILL.md
```

Expected output:
```
grounding (expect 2):
2
synthesis (expect 1):
1
conditional wording (expect 1):
1
```

- [ ] **Step 4: Confirm the description-length PR guard still passes (frontmatter untouched)**

Run:

```bash
python scripts/ci/check-skill-description-length.py
echo "exit: $?"
```

Expected: script prints its OK output and `exit: 0`.

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-design/SKILL.md
git commit -m "feat(threadlight-design): generate grounding-honesty + conditional cross-skill synthesis contract

S4: every generated skill/agent must say so when its knowledge source
lacks the answer and label general-knowledge answers (citation already
enforced via spec § 7). S3: multi-skill answer-composing agents must
reconcile, not concatenate. Additive to Step 6; no pipeline changes.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 2: Carry the S3 reconciliation pointer through Synthesis trait detection

**Files:**
- Modify: `skills/threadlight-design/references/process-traits.md:43` (Synthesis row)

- [ ] **Step 1: Extend the Synthesis row's "Discovery Questions" cell**

Use the `edit` tool. Match this exact line:

```
| **Synthesis** | Combine multiple sources into new content | `synthesize`, `summarize` skills | What output format? Depth? Citation needs? |
```

Replace it with:

```
| **Synthesis** | Combine multiple sources into new content | `synthesize`, `summarize` skills | What output format? Depth? Citation needs? Multiple skills composing one answer → require a **cross-skill reconciliation** clause (preserve detail, resolve disagreement, surface trade-offs) in AGENTS.md Behavioral guidelines. |
```

- [ ] **Step 2: Verify the pointer is present**

Run:

```bash
cd "$(git rev-parse --show-toplevel)"
echo "reconciliation pointer (expect 1):" && grep -c "cross-skill reconciliation" skills/threadlight-design/references/process-traits.md
```

Expected output:
```
reconciliation pointer (expect 1):
1
```

- [ ] **Step 3: Commit**

```bash
git add skills/threadlight-design/references/process-traits.md
git commit -m "feat(threadlight-design): route Synthesis trait to the S3 reconciliation clause

Trait detection now points at the cross-skill reconciliation contract so
discovery carries the guidance into Step 6 generation.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 3: Version bump + full verification

**Files:**
- Modify: `skills/threadlight-design/SKILL.md:18` (`metadata.version`)

- [ ] **Step 1: Bump the skill version**

Use the `edit` tool. Match this exact line:

```
  version: "1.8.0"
```

Replace it with:

```
  version: "1.8.1"
```

- [ ] **Step 2: Verify the bump is well-formed semver**

Run:

```bash
cd "$(git rev-parse --show-toplevel)"
python - <<'PY'
import re, pathlib
s = pathlib.Path("skills/threadlight-design/SKILL.md").read_text(encoding="utf-8")
m = re.search(r'version:\s*"([^"]+)"', s)
assert m, "no metadata.version found"
assert m.group(1) == "1.8.1", f"expected 1.8.1, got {m.group(1)}"
assert re.match(r"^\d+\.\d+\.\d+$", m.group(1)), "not semver"
print("version OK:", m.group(1))
PY
```

Expected output:
```
version OK: 1.8.1
```

- [ ] **Step 3: Run the full spec verification suite (matches the design doc's Verification section)**

Run:

```bash
cd "$(git rev-parse --show-toplevel)"
grep -n "Grounding & honesty" skills/threadlight-design/SKILL.md
grep -n "Cross-skill synthesis" skills/threadlight-design/SKILL.md
grep -n "cross-skill reconciliation" skills/threadlight-design/references/process-traits.md
grep -n 'version: "1.8.1"' skills/threadlight-design/SKILL.md
python scripts/ci/check-skill-description-length.py; echo "guard exit: $?"
```

Expected: two `Grounding & honesty` hits, one `Cross-skill synthesis` hit, one `cross-skill reconciliation` hit, one `version: "1.8.1"` hit, and `guard exit: 0`.

- [ ] **Step 4: Sanity self-check the conditional wording**

Confirm by eye that in `SKILL.md` Step 6 §2 the **Grounding & honesty** sub-bullet is marked "(always)" and the **Cross-skill synthesis** sub-bullet is marked "(only when the agent composes answers from more than one skill …)". The honesty clause must be unconditional; the synthesis clause must be conditional. If either marker is wrong, fix it before committing.

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-design/SKILL.md
git commit -m "chore(threadlight-design): bump to 1.8.1 for S3/S4 generation-contract additions

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Done criteria

- `SKILL.md` Step 6 §1 has the S4 grounding-honesty bullet; §2 has the S4 (always) grounding-honesty clause and the S3 (conditional) cross-skill synthesis clause.
- `process-traits.md` Synthesis row points at the cross-skill reconciliation contract.
- `metadata.version` is `1.8.1`.
- Description-length guard exits 0; no other file changed.
- Next-wave backlog (S1, S2, S5, per-domain evals) remains recorded in `docs/superpowers/specs/2026-07-02-msa-agent-quality-learnings-design.md` — out of scope here.
