# Plan: threadlight-production-ready v0.5.0

**Feature:** Cleanup + closure release for v0.4.0 debt (5 PR-review issues, idempotency bug, sibling-map drift gate, per-customer overrides, 5 experimental promotions, version bump, optional field test).

**For agentic workers:** Execute phases A → F in order. Phase G is aspirational (no code). Each task is bite-sized: write failing test → run (expect fail) → minimal implementation → run (expect pass) → commit. Use the `subagent-driven-development` skill so each phase runs in a clean context — Phase D and Phase E can be parallelized after Phase A-C land. If you prefer a single inline executor, use `executing-plans` instead.

**Goal:** Ship v0.5.0 of `skills/threadlight-production-ready/` that (a) closes the 5 PR #28 follow-up issues #29-#33, (b) adds per-customer policy overrides (Bucket 4), (c) promotes 5 experimental recipes to must-fix (Bucket 3), (d) bumps VERSION + refreshes SKILL/CHANGELOG. Gateway-resilience (Bucket 2) is deferred to v0.6.0+. Sibling-skill flips (Bucket 5) are gated on awesome-gbb landings and tracked as runbook. Real-customer field test (Bucket 6) is aspirational.

**Architecture:** No structural changes. `production_ready.py` stays a single-file CLI (4714 → ~4900 LOC after this release). New module additions are confined to: 4 new helper functions for customer-overrides (Phase D), 1 new `EXCLUDE_GLOBS` constant + glob filter (Phase B), 1 added entry in `FRAMING_QUESTIONS` (Phase B), 5 experimental flips (Phase E), and the `VERSION` constant bump (Phase F). Test suite grows from 19 → 22 files (3 new). All locked invariants from v0.4.0 are preserved (see spec §Locked invariants).

**Tech Stack:** Python 3.11+ stdlib only (no pytest, no PyYAML, no third-party deps). Markdown for recipes + docs. GitHub Actions workflow files (templated strings inside `_scaffold_cicd`). No new runtime dependencies. Mini stdlib YAML parser added for `customer-overrides.yaml` (Phase D) — same pattern used for recipe front-matter parsing in v0.3.0.

---

## Source-of-truth references

Read these before starting. Phase descriptions reference them by short name.

- **Design spec:** `docs/superpowers/specs/2026-06-10-threadlight-production-ready-v050-design.md` (v0.5.0 spec, just committed in this branch)
- **v0.4.0 spec:** `docs/superpowers/specs/2026-06-10-threadlight-production-ready-v040-design.md` (511 lines, on `main`)
- **v0.4.0 plan:** `docs/superpowers/plans/2026-06-10-threadlight-production-ready-v040.md` (2573 lines, on `main` — structural template only, do not edit)
- **SKILL.md:** `skills/threadlight-production-ready/SKILL.md` (current capability surface)
- **CHANGELOG:** `CHANGELOG.md` at repo root (NOT in skill dir)
- **Script:** `skills/threadlight-production-ready/scripts/production_ready.py` (4714 LOC; primary edit target)
- **Recipes dir:** `skills/threadlight-production-ready/references/remediation-recipes/` (61 recipes + `_template.md`)
- **Sibling map:** `skills/threadlight-production-ready/references/sibling-skills-map.md`
- **Tests dir:** `skills/threadlight-production-ready/tests/` (19 files, stdlib-only)
- **Follow-up issues:** `aiappsgbb/threadlight-skills#29`, `#30`, `#31`, `#32`, `#33`
- **Sibling-skill upstream:** `aiappsgbb/awesome-gbb#267-272`

## Module → File map

| Concern | File(s) | Phases that touch it |
|---|---|---|
| Wording correctness | `skills/threadlight-production-ready/SKILL.md`, `CHANGELOG.md` | A, F |
| Recipe content drift | `skills/threadlight-production-ready/references/remediation-recipes/REL-102.md`, `IAM-101.md`, `OBS-106.md` | A, C |
| Idempotency | `skills/threadlight-production-ready/scripts/production_ready.py` (`_glob_repo`, `RepoContext.from_repo`) | B |
| Framing wizard | `skills/threadlight-production-ready/scripts/production_ready.py` (`FRAMING_QUESTIONS`, `_cicd_context_from_framing`, `_scaffold_cicd`) | B, F |
| Sibling-map gate | `skills/threadlight-production-ready/tests/test_sibling_skill_map.py`, `references/runbooks/sibling-skill-flip-protocol.md` (new) | C |
| Customer overrides | `skills/threadlight-production-ready/scripts/production_ready.py` (new helpers, argparse, main), `references/customer-overrides.example.yaml` (new), `references/customer-overrides-schema.md` (new) | D |
| Experimental promotion | `skills/threadlight-production-ready/scripts/production_ready.py` (catalog entries), `references/remediation-recipes/_experimental/` → `references/remediation-recipes/` | E |
| Version + metadata | `skills/threadlight-production-ready/scripts/production_ready.py` (`VERSION`), `SKILL.md`, `CHANGELOG.md` | F |
| Field-test protocol | `skills/threadlight-production-ready/references/field-test-protocol.md` (new) | G |

---

## Phase A — Doc & wording cleanup (issues #29, #32, stale-string)

**Goal:** Close issues #29 and #32 plus fix the stale "deferred to v0.5.0" string at L528. No code-path behavior changes. No fixture impact. Sets a clean baseline before idempotency work in Phase B.

### Task A1: Reword SKILL.md and CHANGELOG to acknowledge `--scaffold-cicd` exception (#29)

**Files:**
- `skills/threadlight-production-ready/SKILL.md` (edit ~L130-134)
- `CHANGELOG.md` (edit v0.4.0 entry, ~L16-19)

**Step 1: Write failing test** — `skills/threadlight-production-ready/tests/test_sacred_rule_wording.py` (new file):

```python
"""Gate the SACRED ARCHITECTURAL RULE wording for self-consistency (issue #29)."""
import pathlib
import unittest


REPO = pathlib.Path(__file__).resolve().parents[3]
SKILL_MD = REPO / "skills" / "threadlight-production-ready" / "SKILL.md"
CHANGELOG = REPO / "CHANGELOG.md"


class SacredRuleWording(unittest.TestCase):
    def test_skill_md_acknowledges_cicd_scaffold_exception(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertNotIn(
            "The Python script is still assessor-only. It never mutates your repo",
            text,
            "SKILL.md still claims Python never writes — contradicts --scaffold-cicd. See #29.",
        )
        self.assertIn("--scaffold-cicd", text)
        self.assertIn("exception", text.lower())

    def test_changelog_v040_entry_acknowledges_cicd_scaffold_exception(self):
        text = CHANGELOG.read_text(encoding="utf-8")
        self.assertNotIn(
            "the Python script never mutates the user's repo",
            text,
            "CHANGELOG v0.4.0 entry still claims Python never writes. See #29.",
        )


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_sacred_rule_wording -v` from repo root. Expect 2 failures.

**Step 3: Edit `SKILL.md` L130-134.** Replace the paragraph beginning `"**The Python script is still assessor-only.**"` with:

```markdown
**The Python script is assessor-only for remediation findings.** It never mutates your repo or
subscription for findings — fixes are dispatched to the agent as apply-plan tasks. The single
documented exception is `--scaffold-cicd`, which writes 2 files (`.github/workflows/threadlight.yml`
and `scripts/threadlight-runbook.md`) into the customer repo so the production-onboarding pipeline
can run. That exception is bounded, opt-in, and writes deterministic templates only — it does not
emit remediation patches.
```

**Step 4: Edit `CHANGELOG.md` v0.4.0 entry** (the paragraph beginning `"the Python script never mutates the user's repo"`). Replace with:

```markdown
the Python script never mutates the user's repo for remediation findings. The lone exception is
the `--scaffold-cicd` opt-in flag, which writes 2 deterministic template files into the customer
repo so the production-onboarding pipeline can run.
```

**Step 5: Re-run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_sacred_rule_wording -v`. Expect 2 passes.

**Step 6: Commit:**

```
docs(skill): clarify SACRED RULE has documented --scaffold-cicd exception

Closes #29. SKILL.md and CHANGELOG.md no longer claim the Python script never writes
to the user repo — they acknowledge --scaffold-cicd as the single bounded exception.
test_sacred_rule_wording.py gates future drift.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task A2: Strip ADO/GitLab paragraph from REL-102 + add catalog gate (#32)

**Files:**
- `skills/threadlight-production-ready/references/remediation-recipes/REL-102.md` (strip paragraph)
- `skills/threadlight-production-ready/tests/test_no_ado_gitlab_in_recipes.py` (new)

**Step 1: Write failing test** — `skills/threadlight-production-ready/tests/test_no_ado_gitlab_in_recipes.py`:

```python
"""Gate v0.4.0 recipes against smuggled-in ADO/GitLab guidance (issue #32, locked invariant 5)."""
import pathlib
import re
import unittest


RECIPES_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "references"
    / "remediation-recipes"
)

# v0.4.0 + v0.5.0 ship GitHub Actions only. ADO + GitLab are deferred to v0.6.0+.
# Recipes referencing them in body text mislead the apply-plan dispatcher.
FORBIDDEN_TOKENS = (
    "azure-pipelines.yml",
    "azure-devops",
    "Azure DevOps",
    ".gitlab-ci.yml",
    "GitLab CI",
)


class NoAdoOrGitlabInRecipes(unittest.TestCase):
    def test_no_recipe_mentions_ado_or_gitlab_yaml_filenames(self):
        offenders = []
        for md in RECIPES_DIR.glob("*.md"):
            if md.name == "_template.md":
                continue
            text = md.read_text(encoding="utf-8")
            for token in FORBIDDEN_TOKENS:
                if token in text:
                    offenders.append(f"{md.name}: contains forbidden token '{token}'")
        self.assertEqual(
            offenders,
            [],
            "Recipes ship GitHub Actions only in v0.5.0. See locked invariant #5.\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_no_ado_gitlab_in_recipes -v`. Expect 1 failure (REL-102 offends).

**Step 3: Edit `REL-102.md`** — find the paragraph that mentions `azure-pipelines.yml` / GitLab CI / Azure DevOps and delete the entire paragraph (typically 4-6 lines under the `## Apply` or `## Notes` heading). Do NOT replace with a "deferred" note — the recipe ships GitHub Actions only and that's it.

**Step 4: Re-run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_no_ado_gitlab_in_recipes -v`. Expect pass.

**Step 5: Commit:**

```
docs(recipes): strip ADO/GitLab guidance from REL-102 + catalog gate

Closes #32. REL-102 was smuggling Azure DevOps + GitLab CI examples into a v0.4.0
recipe that ships GitHub Actions only (locked invariant #5). Stripped the offending
paragraph and added test_no_ado_gitlab_in_recipes.py to gate future drift across
all 61 recipes.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task A3: Fix stale "deferred to v0.5.0" string in script

**Files:**
- `skills/threadlight-production-ready/scripts/production_ready.py` (L528 region)

**Step 1: Write failing test** — extend `skills/threadlight-production-ready/tests/test_version.py` (already exists; just add one assertion). If the file doesn't import string-level content from the script, add a new dedicated test file `skills/threadlight-production-ready/tests/test_script_strings.py`:

```python
"""Gate static strings in production_ready.py against staleness."""
import pathlib
import unittest


SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "production_ready.py"
)


class ScriptStrings(unittest.TestCase):
    def test_no_stale_v050_deferred_reference(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn(
            "deferred to v0.5.0",
            text,
            "Stale string at ~L528 — ADO/GitLab are now deferred to v0.6.0+.",
        )

    def test_v060_deferred_reference_present(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("v0.6.0", text)


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_script_strings -v`. Expect `test_no_stale_v050_deferred_reference` to fail.

**Step 3: Edit `production_ready.py`** around L528. Find the string `"...azure-devops + gitlab are deferred to v0.5.0..."` (or near-equivalent — grep for `deferred to v0.5.0`) and replace `v0.5.0` with `v0.6.0+`.

**Step 4: Re-run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_script_strings -v`. Expect pass.

**Step 5: Commit:**

```
fix(script): correct stale 'deferred to v0.5.0' string

ADO/GitLab CI/CD scaffolds remain deferred — but the target moved to v0.6.0+ when
we scoped v0.5.0 to cleanup + closure. test_script_strings.py gates future drift.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

## Phase B — Idempotency + framing question 8 (#30, #33)

**Goal:** Close #30 (assessor ingests its own outputs on re-run) and #33 (runbook `<tenant-id>` placeholder never filled). These two ride together because both require the framing wizard test fixture to be refreshed once.

### Task B1: Write failing idempotency test (#30)

**Files:**
- `skills/threadlight-production-ready/tests/test_idempotent_assess.py` (new)

**Step 1: Write the test:**

```python
"""Gate that a 2nd assessor run produces byte-identical reports (issue #30).

Repro: 1st run produces production-readiness-report.md. 2nd run globs docs/**/*.md,
ingests that report as "documentation", and emits a different report on the 2nd pass.
The fix is to exclude production-readiness-{report,findings}.{md,json,csv} from the
docs glob in RepoContext.from_repo.
"""
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "production_ready.py"
)


def _md5(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


class IdempotentAssess(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="threadlight-idemp-"))
        (self.tmp / "README.md").write_text(
            "# Test repo\n\nA tiny repo for idempotency testing.\n",
            encoding="utf-8",
        )
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "intro.md").write_text("intro\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_assess(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "assess",
                "--repo",
                str(self.tmp),
                "--out",
                str(self.tmp),
                "--quiet",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return result

    def test_two_consecutive_runs_produce_identical_report(self):
        self._run_assess()
        report = self.tmp / "production-readiness-report.md"
        self.assertTrue(report.exists(), "1st run must produce report")
        hash_a = _md5(report)

        self._run_assess()
        self.assertTrue(report.exists(), "2nd run must produce report")
        hash_b = _md5(report)

        self.assertEqual(
            hash_a,
            hash_b,
            "Assessor not idempotent — 2nd run ingested its own output. See #30.",
        )


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_idempotent_assess -v`. Expect failure (2 hashes differ).

---

### Task B2: Implement `EXCLUDE_GLOBS` + filter in `_glob_repo`

**Files:**
- `skills/threadlight-production-ready/scripts/production_ready.py` (around L974 `_glob_repo` + L1597 docs_text glob)

**Step 1: Add `EXCLUDE_GLOBS` constant** near the top of the script (just below `VERSION = "0.4.0"` at L482):

```python
EXCLUDE_GLOBS = (
    "production-readiness-report.md",
    "production-readiness-report.json",
    "production-readiness-findings.csv",
    "production-readiness-findings.md",
)
```

**Step 2: Edit `_glob_repo` at L974** to filter out the excludes. Replace the existing function body's return statement with:

```python
def _glob_repo(root: pathlib.Path, *patterns: str) -> list[pathlib.Path]:
    """Glob the repo for the given patterns, deduplicated and sorted.

    Files matching EXCLUDE_GLOBS are filtered out so the assessor never ingests
    its own outputs (see #30 — assessor idempotency).
    """
    seen: set[pathlib.Path] = set()
    for pat in patterns:
        for p in root.glob(pat):
            if p.is_file() and p.name not in EXCLUDE_GLOBS:
                seen.add(p)
    return sorted(seen)
```

(Adjust if the existing function uses different variable names — preserve those, just add the `p.name not in EXCLUDE_GLOBS` check.)

**Step 3: Re-run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_idempotent_assess -v`. Expect pass.

**Step 4: Run full suite** — `python3 -m unittest discover skills/threadlight-production-ready/tests/ -v` to confirm no regressions.

**Step 5: Commit:**

```
fix(script): exclude assessor outputs from docs glob (idempotency)

Closes #30. RepoContext.from_repo used _glob_repo("docs/**/*.md", "README.md")
which on a 2nd run ingested production-readiness-report.md as "documentation",
producing drift. Added EXCLUDE_GLOBS tuple and filter in _glob_repo.
test_idempotent_assess.py gates the regression with an end-to-end 2-pass MD5
comparison.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task B3: Extend framing-wizard test for 8th question `azure_tenant_id` (#33)

**Files:**
- `skills/threadlight-production-ready/tests/test_framing_wizard.py` (extend)

**Step 1: Add 3 new test methods** to the existing `FramingWizard` test class:

```python
    def test_framing_questions_includes_azure_tenant_id(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        ids = [q["id"] for q in prod.FRAMING_QUESTIONS]
        self.assertIn(
            "azure_tenant_id",
            ids,
            "Wizard must collect tenant ID so runbook <tenant-id> placeholder is filled. See #33.",
        )

    def test_framing_question_count_is_eight(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        self.assertEqual(
            len(prod.FRAMING_QUESTIONS),
            8,
            "v0.5.0 ships 8 framing questions (azure_tenant_id added per #33).",
        )

    def test_azure_tenant_id_question_validation(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        q = next(q for q in prod.FRAMING_QUESTIONS if q["id"] == "azure_tenant_id")
        # Must be required (runbook substitution would fail silently otherwise)
        self.assertTrue(q.get("required", True))
        # Must accept UUID-shaped input
        self.assertIn("uuid", q.get("help", "").lower() + q.get("prompt", "").lower())
```

**Step 2: Run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_framing_wizard -v`. Expect 3 failures.

---

### Task B4: Add 8th `FRAMING_QUESTIONS` entry + plumb to runbook template

**Files:**
- `skills/threadlight-production-ready/scripts/production_ready.py` (L488 `FRAMING_QUESTIONS`, L424 `_cicd_context_from_framing`, L430 `_scaffold_cicd`)

**Step 1: Edit `FRAMING_QUESTIONS`** — append after the existing 7th question:

```python
    {
        "id": "azure_tenant_id",
        "prompt": "Azure tenant ID (UUID) where the production subscription lives",
        "help": "Find it via `az account show --query tenantId -o tsv`. UUID format required.",
        "required": True,
    },
```

**Step 2: Edit `_cicd_context_from_framing`** at L424. Replace the existing `"tenant_id": framing.get("azure_tenant_id", "<tenant-id>")` (or equivalent fallback) with strict lookup:

```python
def _cicd_context_from_framing(framing: dict) -> dict:
    """Translate framing answers into a context dict for template rendering."""
    ctx = {
        # ...existing fields preserved...
        "tenant_id": framing["azure_tenant_id"],
        # ...rest of ctx...
    }
    return ctx
```

(If the function builds `ctx` differently, just ensure `tenant_id` is sourced from `framing["azure_tenant_id"]` with no fallback string.)

**Step 3: Re-run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_framing_wizard -v`. Expect 3 passes.

**Step 4: Commit:**

```
feat(framing): add azure_tenant_id question (closes #33)

Runbook scaffolds previously emitted '<tenant-id>' as a literal placeholder
because the framing wizard never asked. Added 8th question + plumbed through
_cicd_context_from_framing → _scaffold_cicd template substitution.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task B5: Extend `test_cicd_scaffold.py` to assert no `<…>` placeholders survive

**Files:**
- `skills/threadlight-production-ready/tests/test_cicd_scaffold.py` (extend)

**Step 1: Add one test method:**

```python
    def test_runbook_has_no_unfilled_angle_bracket_placeholders(self):
        # Runs after _scaffold_cicd with a full framing dict; checks both output files
        # for any surviving '<...>' pattern that means a substitution was missed.
        import re
        for path in self.out_files:
            if not str(path).endswith("threadlight-runbook.md"):
                continue
            text = path.read_text(encoding="utf-8")
            matches = re.findall(r"<[a-z-]+>", text)
            self.assertEqual(
                matches,
                [],
                f"Runbook has unfilled placeholders: {matches}. See #33.",
            )
```

(Adjust `self.out_files` to whatever attribute the existing test class uses to track scaffold output.)

**Step 2: Run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_cicd_scaffold -v`. Expect pass (B4 already plumbed substitution).

**Step 3: Refresh fixture if needed** — if the test uses a saved framing fixture dict at the top of the file, add `"azure_tenant_id": "00000000-0000-0000-0000-000000000001"` to it.

**Step 4: Commit:**

```
test(cicd): gate runbook against unfilled angle-bracket placeholders

Defense-in-depth for #33. Even if future framing questions land without
matching template variables, the test catches surviving '<...>' patterns
in scaffolded runbook output.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

## Phase C — Sibling-skill-map drift gate (#31)

**Goal:** Close #31. Rule: recipes pointing at sibling skills that have not yet been built upstream MUST be `kind: manual`. Currently IAM-101 and OBS-106 violate this. Fix the 2 recipes + add a 3rd test method that gates all future planned-sibling references.

### Task C1: Extend `test_sibling_skill_map.py` with planned-sibling enforcement test

**Files:**
- `skills/threadlight-production-ready/tests/test_sibling_skill_map.py` (extend)

**Step 1: Add the 3rd test method** to the existing class:

```python
    def test_recipes_for_planned_siblings_must_be_manual(self):
        """Recipes referencing unbuilt sibling skills must be kind: manual (issue #31)."""
        # Parse the sibling-skills-map.md table to find "planned" rows
        from pathlib import Path
        import re

        map_path = (
            Path(__file__).resolve().parents[1]
            / "references"
            / "sibling-skills-map.md"
        )
        text = map_path.read_text(encoding="utf-8")
        # Table rows look like: | recipe-id | skill-name | status | issue |
        planned_recipes = set()
        for line in text.splitlines():
            if "|" not in line:
                continue
            cells = [c.strip() for c in line.split("|")]
            # Skip header and separator rows
            if len(cells) < 5 or cells[1] in ("recipe-id", ":---", "---", ""):
                continue
            recipe_id, _skill, status, *_ = cells[1:]
            if status.lower() in ("planned", "not-built", "upstream-pending"):
                planned_recipes.add(recipe_id)

        # Now check each planned recipe's kind: front-matter
        recipes_dir = (
            Path(__file__).resolve().parents[1] / "references" / "remediation-recipes"
        )
        offenders = []
        for rid in planned_recipes:
            rpath = recipes_dir / f"{rid}.md"
            if not rpath.exists():
                continue
            rtext = rpath.read_text(encoding="utf-8")
            m = re.search(r"^kind:\s*([a-z-]+)\s*$", rtext, re.MULTILINE)
            if not m:
                offenders.append(f"{rid}: no kind: in front-matter")
                continue
            if m.group(1) != "manual":
                offenders.append(
                    f"{rid}: kind: {m.group(1)} but sibling skill is unbuilt. Must be 'manual'."
                )
        self.assertEqual(
            offenders,
            [],
            "Planned-sibling rule violated. See #31.\n" + "\n".join(offenders),
        )
```

**Step 2: Run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_sibling_skill_map -v`. Expect failure (IAM-101 and OBS-106 offend).

---

### Task C2: Flip IAM-101 to `kind: manual`

**Files:**
- `skills/threadlight-production-ready/references/remediation-recipes/IAM-101.md`

**Step 1: Edit `IAM-101.md` front-matter:**

```yaml
---
id: IAM-101
title: <existing title>
pillar: IAM
severity: <existing>
kind: manual
sibling_skill: foundry-rbac-audit  # tracked upstream as aiappsgbb/awesome-gbb#268
sibling_skill_status: planned
---
```

**Step 2: Replace the existing `## Apply` section** with a manual-style apply block. Body should explain to the agent: "no sibling skill is built yet — surface the finding to the operator with the runbook link below and ask them to schedule the manual fix." Reference template `_template.md` if uncertain about exact section shape.

**Step 3: Add explicit reference to upstream issue** in body:

```markdown
> **Why manual today:** the `foundry-rbac-audit` sibling skill is tracked at
> `aiappsgbb/awesome-gbb#268` and not yet shipped. Once it lands, this recipe
> will flip to `kind: sibling-skill` per `references/runbooks/sibling-skill-flip-protocol.md`.
```

---

### Task C3: Flip OBS-106 to `kind: manual`

**Files:**
- `skills/threadlight-production-ready/references/remediation-recipes/OBS-106.md`

Same procedure as Task C2 but with:
- `sibling_skill: azure-resource-diagnostics`
- `sibling_skill_status: planned`
- Upstream reference: `aiappsgbb/awesome-gbb#271`

---

### Task C4: Write sibling-skill flip-protocol runbook + commit phase

**Files:**
- `skills/threadlight-production-ready/references/runbooks/sibling-skill-flip-protocol.md` (new)

**Step 1: Create the runbook** — short (~40 lines) operator doc:

```markdown
# Sibling-skill flip protocol

When an upstream sibling skill referenced in `references/sibling-skills-map.md`
lands in `aiappsgbb/awesome-gbb`, flip the corresponding recipe(s) from
`kind: manual` → `kind: sibling-skill`.

## Pre-conditions

1. The upstream skill is merged to `awesome-gbb:main` and listed in the awesome-gbb
   plugin manifest.
2. The skill has a stable `SKILL.md` slug — verify by running the Skill tool against
   it from a Copilot CLI session.
3. The threadlight-production-ready CHANGELOG has a slot for a `feat(recipes):`
   entry in the upcoming version.

## Procedure

1. Open `references/sibling-skills-map.md` and change the recipe's row `status` from
   `planned` to `built`. Update the `issue` cell to the merged PR number.
2. Open `references/remediation-recipes/<RECIPE-ID>.md`. Change front-matter:
   - `kind: manual` → `kind: sibling-skill`
   - `sibling_skill_status: planned` → `sibling_skill_status: built`
3. Replace the `## Apply` body's "manual today" preamble with a `sibling-skill`
   dispatch block — copy shape from any existing `kind: sibling-skill` recipe
   (e.g. NET-101 if applicable).
4. Run `python3 -m unittest skills.threadlight-production-ready.tests.test_sibling_skill_map`
   to confirm the planned-sibling gate no longer flags this recipe.
5. Add a CHANGELOG entry under the next version's `Changed` section:
   `flip <RECIPE-ID> to kind: sibling-skill (awesome-gbb#NNN landed)`.

## Rollback

If the upstream skill is reverted, repeat the procedure in reverse and add a
`Reverted` entry to the CHANGELOG.
```

**Step 2: Run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_sibling_skill_map -v`. Expect all 3 tests pass after C2 + C3 edits.

**Step 3: Commit (single commit covering C2 + C3 + C4):**

```
fix(recipes): flip IAM-101 and OBS-106 to kind: manual (closes #31)

Sibling-skill-map rule: recipes for unbuilt upstream skills MUST be kind: manual.
IAM-101 (foundry-rbac-audit → awesome-gbb#268) and OBS-106 (azure-resource-diagnostics
→ awesome-gbb#271) were violating this with kind: repo-edit. Flipped both; added
sibling-skill-flip-protocol.md runbook so future flips follow a documented procedure
when upstream lands. test_sibling_skill_map.py grew a 3rd assertion that gates all
future drift.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

## Phase D — Per-customer overrides (Bucket 4 / SPEC §12)

**Goal:** Implement per-customer policy overrides. Operator passes `--customer-overrides PATH` to a YAML file; loader validates schema; applier rewrites finding `status` fields (PASS↔FAIL flips only). Must-fix override rejected with `exit 2` + loud error. Status-flips only — no severity rewrites, no finding suppression, no recipe substitution.

### Task D1: Write failing `test_customer_overrides.py` (10 test functions)

**Files:**
- `skills/threadlight-production-ready/tests/test_customer_overrides.py` (new)
- `skills/threadlight-production-ready/tests/fixtures/customer-overrides-valid.yaml` (new)
- `skills/threadlight-production-ready/tests/fixtures/customer-overrides-must-fix-override.yaml` (new)

**Step 1: Create fixtures.** `customer-overrides-valid.yaml`:

```yaml
customer: acme-corp
overrides:
  - recipe_id: SEC-103
    status: pass
    reason: "Customer uses Vault, not Key Vault. Equivalent control."
  - recipe_id: NET-201
    status: fail
    reason: "Customer requires private endpoint for blob — default is pass."
```

`customer-overrides-must-fix-override.yaml`:

```yaml
customer: acme-corp
overrides:
  - recipe_id: SEC-001
    status: pass
    reason: "Trying to skip must-fix — should be rejected."
```

**Step 2: Create the test file:**

```python
"""Gate per-customer policy overrides (Bucket 4 / SPEC §12)."""
import pathlib
import subprocess
import sys
import tempfile
import unittest


SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "production_ready.py"
)
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


class CustomerOverridesLoader(unittest.TestCase):
    def test_load_returns_none_when_path_is_none(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        self.assertIsNone(prod._load_customer_overrides(None))

    def test_load_returns_dict_for_valid_yaml(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        ov = prod._load_customer_overrides(FIXTURES / "customer-overrides-valid.yaml")
        self.assertEqual(ov["customer"], "acme-corp")
        self.assertEqual(len(ov["overrides"]), 2)
        self.assertEqual(ov["overrides"][0]["recipe_id"], "SEC-103")

    def test_load_raises_on_missing_file(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        with self.assertRaises(FileNotFoundError):
            prod._load_customer_overrides(pathlib.Path("/nonexistent/path.yaml"))


class CustomerOverridesValidator(unittest.TestCase):
    def test_validate_passes_on_valid_payload(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        ov = prod._load_customer_overrides(FIXTURES / "customer-overrides-valid.yaml")
        prod._validate_customer_overrides(ov)  # should not raise

    def test_validate_rejects_missing_customer_field(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        with self.assertRaises(ValueError) as cm:
            prod._validate_customer_overrides({"overrides": []})
        self.assertIn("customer", str(cm.exception).lower())

    def test_validate_rejects_unknown_status_value(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        bad = {"customer": "x", "overrides": [{"recipe_id": "X", "status": "skip", "reason": "r"}]}
        with self.assertRaises(ValueError):
            prod._validate_customer_overrides(bad)

    def test_validate_requires_reason_string(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        bad = {"customer": "x", "overrides": [{"recipe_id": "X", "status": "pass"}]}
        with self.assertRaises(ValueError):
            prod._validate_customer_overrides(bad)


class CustomerOverridesApplier(unittest.TestCase):
    def test_apply_flips_status_pass_to_fail(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        findings = [{"recipe_id": "NET-201", "status": "pass", "severity": "warn"}]
        ov = {"customer": "x", "overrides": [{"recipe_id": "NET-201", "status": "fail", "reason": "r"}]}
        out = prod._apply_customer_overrides(findings, ov)
        self.assertEqual(out[0]["status"], "fail")
        self.assertIn("override_reason", out[0])

    def test_apply_leaves_unmatched_findings_alone(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        findings = [{"recipe_id": "OTHER", "status": "pass", "severity": "warn"}]
        ov = {"customer": "x", "overrides": [{"recipe_id": "NET-201", "status": "fail", "reason": "r"}]}
        out = prod._apply_customer_overrides(findings, ov)
        self.assertEqual(out[0]["status"], "pass")
        self.assertNotIn("override_reason", out[0])


class CustomerOverridesMustFixRejection(unittest.TestCase):
    def test_apply_raises_when_override_targets_must_fix(self):
        from importlib import import_module
        prod = import_module("skills.threadlight-production-ready.scripts.production_ready")
        findings = [{"recipe_id": "SEC-001", "status": "fail", "severity": "must-fix"}]
        ov = {"customer": "x", "overrides": [{"recipe_id": "SEC-001", "status": "pass", "reason": "r"}]}
        with self.assertRaises(SystemExit) as cm:
            prod._apply_customer_overrides(findings, ov)
        self.assertEqual(cm.exception.code, 2)

    def test_cli_exits_2_on_must_fix_override(self):
        # End-to-end via CLI invocation
        with tempfile.TemporaryDirectory() as td:
            tdp = pathlib.Path(td)
            (tdp / "README.md").write_text("# x\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "assess",
                    "--repo", str(tdp),
                    "--out", str(tdp),
                    "--customer-overrides", str(FIXTURES / "customer-overrides-must-fix-override.yaml"),
                    "--quiet",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("must-fix", (result.stderr + result.stdout).lower())


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_customer_overrides -v`. Expect 10 import/attribute failures (functions don't exist yet).

---

### Task D2: Implement stdlib mini-YAML parser `_load_customer_overrides`

**Files:**
- `skills/threadlight-production-ready/scripts/production_ready.py` (new helper near other parsers)

**Step 1: Add `_load_customer_overrides`** to `production_ready.py`. Place it near `_parse_yaml_front_matter` (or wherever the existing YAML helpers live):

```python
def _load_customer_overrides(path):
    """Load a customer-overrides YAML file using a minimal stdlib parser.

    Supports the limited shape:
        customer: <str>
        overrides:
          - recipe_id: <str>
            status: pass|fail
            reason: <str>

    Returns None if path is None. Raises FileNotFoundError if path is missing.
    """
    if path is None:
        return None
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8")  # FileNotFoundError surfaces naturally

    out = {"customer": None, "overrides": []}
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # Top-level scalar: "customer: foo"
        if not line.startswith(" ") and ":" in line and not line.startswith("-"):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key == "customer":
                out["customer"] = val
            elif key == "overrides":
                pass  # consume list items below
            continue
        # List item start: "  - recipe_id: X"
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current is not None:
                out["overrides"].append(current)
            current = {}
            stripped = stripped[2:]
        # Key-value inside item
        if current is not None and ":" in stripped:
            key, _, val = stripped.partition(":")
            current[key.strip()] = val.strip().strip('"').strip("'")
    if current is not None:
        out["overrides"].append(current)
    return out
```

**Step 2: Run loader tests:**

```
python3 -m unittest skills.threadlight-production-ready.tests.test_customer_overrides.CustomerOverridesLoader -v
```

Expect 3 passes.

---

### Task D3: Implement `_validate_customer_overrides`

**Step 1: Add validator:**

```python
def _validate_customer_overrides(ov):
    """Validate a customer-overrides payload. Raises ValueError on invalid shape."""
    if not isinstance(ov, dict):
        raise ValueError("customer-overrides must be a mapping")
    if not ov.get("customer"):
        raise ValueError("customer-overrides missing required 'customer' field")
    overrides = ov.get("overrides", [])
    if not isinstance(overrides, list):
        raise ValueError("'overrides' must be a list")
    for i, item in enumerate(overrides):
        if not isinstance(item, dict):
            raise ValueError(f"overrides[{i}] must be a mapping")
        if not item.get("recipe_id"):
            raise ValueError(f"overrides[{i}] missing 'recipe_id'")
        status = item.get("status")
        if status not in ("pass", "fail"):
            raise ValueError(
                f"overrides[{i}].status must be 'pass' or 'fail', got {status!r}"
            )
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ValueError(
                f"overrides[{i}] requires a non-empty 'reason' string"
            )
```

**Step 2: Run validator tests:**

```
python3 -m unittest skills.threadlight-production-ready.tests.test_customer_overrides.CustomerOverridesValidator -v
```

Expect 4 passes.

---

### Task D4: Implement `_apply_customer_overrides` (with must-fix loud reject)

**Step 1: Add applier:**

```python
def _apply_customer_overrides(findings, ov):
    """Apply customer overrides to a list of findings. Status-flips only.

    A finding's severity == 'must-fix' may never be overridden — attempting to do
    so calls sys.exit(2) with a loud error to fail the deploy gate.
    """
    if ov is None:
        return findings
    index = {item["recipe_id"]: item for item in ov.get("overrides", [])}
    out = []
    for f in findings:
        rid = f.get("recipe_id")
        if rid in index:
            target_status = index[rid]["status"]
            if f.get("severity") == "must-fix":
                msg = (
                    f"FATAL: customer-override on must-fix finding rejected.\n"
                    f"  recipe_id: {rid}\n"
                    f"  current status: {f.get('status')}\n"
                    f"  attempted override: {target_status}\n"
                    f"  reason given: {index[rid].get('reason')!r}\n"
                    f"Must-fix findings cannot be silenced by customer overrides. "
                    f"Either remediate the finding, or work with the threadlight maintainers "
                    f"to demote it from must-fix in the next release."
                )
                print(msg, file=sys.stderr)
                sys.exit(2)
            new_f = dict(f)
            new_f["status"] = target_status
            new_f["override_reason"] = index[rid]["reason"]
            new_f["override_customer"] = ov["customer"]
            out.append(new_f)
        else:
            out.append(f)
    return out
```

**Step 2: Run applier + rejection tests:**

```
python3 -m unittest skills.threadlight-production-ready.tests.test_customer_overrides.CustomerOverridesApplier -v
python3 -m unittest skills.threadlight-production-ready.tests.test_customer_overrides.CustomerOverridesMustFixRejection.test_apply_raises_when_override_targets_must_fix -v
```

Expect 3 passes.

---

### Task D5: Wire `--customer-overrides PATH` flag into argparse + main

**Files:**
- `skills/threadlight-production-ready/scripts/production_ready.py` (`_parse_args` ~L4220, `main()` ~L4282)

**Step 1: Add argparse flag** inside `_parse_args` next to the existing `assess` sub-parser flags:

```python
assess.add_argument(
    "--customer-overrides",
    type=pathlib.Path,
    default=None,
    help="Path to a customer-overrides.yaml file (Bucket 4 / SPEC §12). "
         "Status-flips only. Must-fix findings cannot be overridden — attempting "
         "to do so exits 2.",
)
```

**Step 2: Wire into `main`** in the `assess` branch. After findings are computed and before report is written:

```python
ov = _load_customer_overrides(args.customer_overrides)
if ov is not None:
    _validate_customer_overrides(ov)
    findings = _apply_customer_overrides(findings, ov)
```

**Step 3: Run end-to-end CLI test:**

```
python3 -m unittest skills.threadlight-production-ready.tests.test_customer_overrides.CustomerOverridesMustFixRejection.test_cli_exits_2_on_must_fix_override -v
```

Expect pass.

**Step 4: Run full overrides suite:**

```
python3 -m unittest skills.threadlight-production-ready.tests.test_customer_overrides -v
```

Expect all 10 pass.

---

### Task D6: Add example overrides file + schema doc + commit phase

**Files:**
- `skills/threadlight-production-ready/references/customer-overrides.example.yaml` (new)
- `skills/threadlight-production-ready/references/customer-overrides-schema.md` (new)

**Step 1: Create example file:**

```yaml
# customer-overrides.example.yaml
#
# Per-customer policy overrides. Status-flips only (PASS ↔ FAIL).
# Must-fix findings CANNOT be overridden — the script exits 2.
#
# Pass with: production_ready.py assess --customer-overrides path/to/this.yaml

customer: acme-corp

overrides:
  - recipe_id: SEC-103
    status: pass
    reason: |
      Customer uses HashiCorp Vault, not Azure Key Vault. Equivalent control;
      reviewed by security team 2026-Q2.

  - recipe_id: NET-201
    status: fail
    reason: |
      Customer requires private endpoint for blob (compliance team mandate).
      Default policy is pass; this customer is stricter.
```

**Step 2: Create schema doc** (~50 lines):

```markdown
# customer-overrides.yaml schema

## Purpose

Per-customer overrides let an operator flip the **status** of a specific finding
in the assessor's report. Use cases:

- Customer uses an equivalent-but-different control (e.g. Vault vs Key Vault) →
  flip a FAIL to PASS.
- Customer has a stricter policy than threadlight's default (e.g. mandatory
  private endpoints) → flip a PASS to FAIL.

## What overrides cannot do (intentional limits)

- Cannot override `severity: must-fix` findings — script exits 2 with a loud error.
- Cannot rewrite a recipe's `severity`. Only `status` changes are supported.
- Cannot suppress a finding entirely (no `skip` status). Use the upstream recipe-edit
  process if a finding does not apply to a class of customers.
- Cannot substitute one recipe for another. Recipe IDs are stable identifiers.

## File shape

```yaml
customer: <string>   # required; appears in finding metadata as override_customer
overrides:
  - recipe_id: <string>   # required; must match an emitted finding's recipe_id
    status: pass|fail     # required; only these two values accepted
    reason: <string>      # required; non-empty; appears in finding as override_reason
  - ...
```

## Validation

The loader (`_load_customer_overrides`) uses a stdlib-only mini-YAML parser. The
validator (`_validate_customer_overrides`) rejects:

- Missing or empty `customer` field
- `overrides` not a list
- Any item missing `recipe_id` / `status` / `reason`
- Status values other than `pass` or `fail`
- Empty reason strings

## Worked example

See `customer-overrides.example.yaml` in this directory.

## Audit trail

Every overridden finding in the report includes:

- `override_customer: <customer-name>`
- `override_reason: <reason-string>`

So downstream automation (and humans reading the report) can see exactly which
findings were touched and why.
```

**Step 3: Run full suite:**

```
python3 -m unittest discover skills/threadlight-production-ready/tests/ -v
```

Expect zero failures across all 22 files.

**Step 4: Commit (single Phase D commit):**

```
feat(overrides): per-customer policy overrides (Bucket 4 / SPEC §12)

Adds --customer-overrides PATH flag to `assess`. Status-flips only (PASS ↔ FAIL).
Must-fix findings cannot be overridden — attempting to do so exits 2 with a loud
error to fail the deploy gate (no silent must-fix bypass).

New: _load_customer_overrides (stdlib mini-YAML), _validate_customer_overrides,
_apply_customer_overrides. New: customer-overrides.example.yaml +
customer-overrides-schema.md. test_customer_overrides.py covers all 10 paths
(loader, validator, applier, must-fix reject, end-to-end CLI exit 2).

Closes SPEC §12 v0.4.0 debt.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

## Phase E — Promote 5 experimentals to must-fix (Bucket 3)

**Goal:** Promote NET-502, EVAL-101, EVAL-102, SUP-101, SRE-103 from `_experimental/` to top-level recipes with real assertions in the catalog. Per-recipe `kind` decisions:

| Recipe | `kind` | Notes |
|---|---|---|
| NET-502 | `sibling-skill` → `citadel-spoke-onboarding` | This skill already exists in awesome-gbb |
| EVAL-101 | `manual` | "Do you have any evals at all?" — too operator-dependent to automate |
| EVAL-102 | `manual` | "Do you have a regression eval baseline?" — same |
| SUP-101 | `repo-edit` | Adds a `SUPPORT.md` file at repo root if missing |
| SRE-103 | `repo-edit` | Adds an SRE-runbook stub at `docs/sre/runbook.md` if missing |

### Task E1: Promote NET-502 (sibling-skill)

**Files:**
- `skills/threadlight-production-ready/references/remediation-recipes/_experimental/NET-502.md` → `skills/threadlight-production-ready/references/remediation-recipes/NET-502.md`
- `skills/threadlight-production-ready/scripts/production_ready.py` (L657 region — flip `experimental=True` → `experimental=False`, add real assertion)

**Step 1: Move file:**

```
git mv skills/threadlight-production-ready/references/remediation-recipes/_experimental/NET-502.md \
       skills/threadlight-production-ready/references/remediation-recipes/NET-502.md
```

**Step 2: Edit recipe front-matter** to `kind: sibling-skill`, set `sibling_skill: citadel-spoke-onboarding`, `sibling_skill_status: built`, `severity: must-fix`. Replace `## Apply` body with a sibling-skill dispatch block (copy shape from an existing sibling-skill recipe in the same directory).

**Step 3: Edit `production_ready.py` L657** — find the `"NET-502": {...experimental=True...}` catalog entry. Change to:

```python
"NET-502": {
    "title": "...existing title...",
    "pillar": "NET",
    "severity": "must-fix",
    "kind": "sibling-skill",
    "experimental": False,
    "check": _check_net_502,  # or whatever the assert function is named
},
```

Add a `_check_net_502(ctx)` function that does a real assertion (e.g. "does the customer have a Citadel spoke linked?"). If the assertion logic is non-trivial, the simplest landing is: assert that a `citadel-spoke.yaml` or equivalent manifest file exists under `infra/` — if not, emit FAIL with severity must-fix and dispatch the sibling skill.

**Step 4: Update `test_experimental_excluded.py`** — confirm the assertion `len(exp) >= 10` at L66 still passes (current ~24, will be ~23 after this removal). If the comment cites a specific count, update the comment to reflect new count.

**Step 5: Run full suite** — `python3 -m unittest discover skills/threadlight-production-ready/tests/ -v`. Expect green.

**Step 6: Commit:**

```
feat(recipes): promote NET-502 to must-fix (sibling-skill → citadel-spoke-onboarding)

Bucket 3 promotion (1/5). NET-502 was experimental: validates a customer
production subscription is wired into a Citadel hub. Now must-fix; dispatches
to the `citadel-spoke-onboarding` skill in awesome-gbb (already shipped).

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task E2: Promote EVAL-101 (manual)

**Files:**
- `skills/threadlight-production-ready/references/remediation-recipes/_experimental/EVAL-101.md` → `skills/threadlight-production-ready/references/remediation-recipes/EVAL-101.md`
- `skills/threadlight-production-ready/scripts/production_ready.py` (L725 region)

**Step 1-2:** Same `git mv` and front-matter pattern as E1, but `kind: manual`, `severity: must-fix`. Apply body should instruct the agent to ask the operator: "Does this customer have any evaluation harness at all? If no, schedule a manual conversation with the field team to scope one."

**Step 3:** Edit `production_ready.py` L725 — flip `EVAL-101` `experimental=True → False`, `kind: manual`, `severity: must-fix`. Use a `_check_eval_101(ctx)` that always returns FAIL with severity must-fix (the manual recipe will handle remediation).

**Step 4:** Commit:

```
feat(recipes): promote EVAL-101 to must-fix (manual)

Bucket 3 promotion (2/5). EVAL-101: "do you have any evals at all?" — always
manual because the answer requires an operator conversation with the field team.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task E3: Promote EVAL-102 (manual)

Same shape as E2 but for EVAL-102 ("regression eval baseline?"). Edit point: `production_ready.py` L726.

Commit:

```
feat(recipes): promote EVAL-102 to must-fix (manual)

Bucket 3 promotion (3/5). EVAL-102: "do you have a regression eval baseline?"
— manual because requires field-team conversation.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task E4: Promote SUP-101 (repo-edit)

**Files:**
- `_experimental/SUP-101.md` → `SUP-101.md`
- `production_ready.py` L759

**Step 1:** Move file + flip front-matter to `kind: repo-edit`, `severity: must-fix`.

**Step 2:** `## Apply` body should describe a repo-edit dispatch: if `SUPPORT.md` is missing at repo root, write a stub with sections "Where to file bugs", "Escalation path", "On-call rotation reference". Provide the stub template inline in the recipe body.

**Step 3:** Edit `production_ready.py` L759 catalog entry — flip experimental, add `_check_sup_101(ctx)`:

```python
def _check_sup_101(ctx):
    """SUP-101: SUPPORT.md must exist at repo root."""
    return (ctx.root / "SUPPORT.md").is_file()
```

Then in catalog: `"check": _check_sup_101`, and the recipe emits FAIL when the check returns False.

**Step 4:** Commit:

```
feat(recipes): promote SUP-101 to must-fix (repo-edit)

Bucket 3 promotion (4/5). SUP-101: SUPPORT.md must exist at repo root.
Recipe dispatches a repo-edit if missing, writing a stub with bug-filing,
escalation, and on-call sections.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task E5: Promote SRE-103 (repo-edit)

Same shape as E4 but for SRE-103 ("does the customer have an SRE runbook?"). Edit point: `production_ready.py` L799. Recipe writes a stub to `docs/sre/runbook.md` if missing. `_check_sre_103(ctx)` returns `(ctx.root / "docs" / "sre" / "runbook.md").is_file()`.

Commit:

```
feat(recipes): promote SRE-103 to must-fix (repo-edit)

Bucket 3 promotion (5/5). SRE-103: docs/sre/runbook.md must exist.
Repo-edit dispatch writes a stub when missing.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task E6: Verify `test_experimental_excluded.py` still passes + update comment

**Files:**
- `skills/threadlight-production-ready/tests/test_experimental_excluded.py` (touch comment only)

**Step 1:** Run `python3 -m unittest skills.threadlight-production-ready.tests.test_experimental_excluded -v`. Expect pass (assertion `len(exp) >= 10` still satisfied; ~24 → ~19 after 5 removals).

**Step 2:** Update the comment at the top of the file to reflect post-v0.5.0 state — e.g. `"# After v0.5.0: 5 experimentals promoted (NET-502, EVAL-101, EVAL-102, SUP-101, SRE-103). Remaining ~19 are pure research stubs deferred to v0.6.0+."`

**Step 3:** Commit:

```
test(experimental): update comment after 5-recipe v0.5.0 promotion

No assertion change — len(exp) >= 10 still satisfied. Comment now lists
which recipes were promoted in v0.5.0 so the next maintainer knows the
remaining ~19 are pure-research stubs targeted for v0.6.0+.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

## Phase F — Version bump, SKILL, CHANGELOG

**Goal:** Cut the v0.5.0 release artifacts.

### Task F1: Bump `VERSION` + update `test_version.py`

**Files:**
- `skills/threadlight-production-ready/scripts/production_ready.py` (L482)
- `skills/threadlight-production-ready/tests/test_version.py`

**Step 1: Edit failing test first.** Open `test_version.py`. Change the expected version string from `"0.4.0"` → `"0.5.0"`.

**Step 2: Run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_version -v`. Expect failure (script still on 0.4.0).

**Step 3: Edit `production_ready.py` L482** — `VERSION = "0.4.0"` → `VERSION = "0.5.0"`.

**Step 4: Re-run** — `python3 -m unittest skills.threadlight-production-ready.tests.test_version -v`. Expect pass.

**Step 5: Commit:**

```
chore(version): bump to 0.5.0

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task F2: Update SKILL.md (metadata + 8-question framing table + `--customer-overrides`)

**Files:**
- `skills/threadlight-production-ready/SKILL.md`

**Step 1:** Bump the frontmatter `version: 0.4.0` → `version: 0.5.0`.

**Step 2:** Replace the "Framing questions" section's 7-question table with an 8-question table that includes `azure_tenant_id` (UUID). Use existing rows verbatim, just append the new row.

**Step 3:** Add a `## Per-customer overrides` section (~30 lines) describing the `--customer-overrides PATH` flag, pointing at `references/customer-overrides-schema.md`, and explicitly noting "must-fix findings cannot be overridden — script exits 2."

**Step 4:** Run `python3 -m unittest skills.threadlight-production-ready.tests.test_framing_wizard -v` — should still pass after Phase B work.

**Step 5: Commit:**

```
docs(skill): SKILL.md for v0.5.0 (8-question framing + --customer-overrides)

- Frontmatter version 0.4.0 → 0.5.0
- Framing-questions table grew to 8 (azure_tenant_id, closes #33)
- New "Per-customer overrides" section (Bucket 4 / SPEC §12)
- Reaffirms must-fix overrides → exit 2

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task F3: Write `CHANGELOG.md` v0.5.0 entry with truthful "Deferred to v0.6.0+" section

**Files:**
- `CHANGELOG.md` (repo root)

**Step 1: Prepend a v0.5.0 entry** above the v0.4.0 block. Use this exact structure:

```markdown
## v0.5.0 — 2026-06-NN

**Theme:** Cleanup + closure for v0.4.0 debt. Per-customer overrides. 5 experimental
recipes promoted to must-fix.

### Added
- `--customer-overrides PATH` flag on `assess` (Bucket 4 / SPEC §12). Status-flips only.
  Must-fix findings cannot be overridden — script exits 2.
- 8th framing question: `azure_tenant_id` (UUID). Runbook scaffolds no longer emit
  `<tenant-id>` as a literal placeholder (closes #33).
- `EXCLUDE_GLOBS` constant + `_glob_repo` filter. Assessor no longer ingests its own
  output on re-run (closes #30).
- New runbook: `references/runbooks/sibling-skill-flip-protocol.md`. Documents how
  to flip a `manual` recipe to `sibling-skill` when its upstream awesome-gbb skill
  lands.
- New tests (3 files): `test_idempotent_assess.py`, `test_no_ado_gitlab_in_recipes.py`,
  `test_customer_overrides.py` (10 functions), `test_sacred_rule_wording.py`,
  `test_script_strings.py`.
- 5 experimental recipes promoted to must-fix (Bucket 3): NET-502 (sibling-skill →
  citadel-spoke-onboarding), EVAL-101 (manual), EVAL-102 (manual), SUP-101 (repo-edit),
  SRE-103 (repo-edit).

### Changed
- IAM-101 and OBS-106 flipped to `kind: manual` (closes #31). Upstream sibling
  skills `foundry-rbac-audit` (awesome-gbb#268) and `azure-resource-diagnostics`
  (awesome-gbb#271) are not yet built.
- SKILL.md and CHANGELOG.md no longer claim "Python script never writes to the
  user's repo" — they acknowledge `--scaffold-cicd` as a documented exception
  (closes #29).
- REL-102 stripped of ADO/GitLab guidance. v0.5.0 still ships GitHub Actions only
  (closes #32).
- Stale string `"deferred to v0.5.0"` in `production_ready.py` corrected to `"v0.6.0+"`.

### Deferred to v0.6.0+

These were considered for v0.5.0 and explicitly de-scoped:

- **Gateway-resilience pillar (Bucket 2).** New `GW-001..103` pillar (~25-40 recipes)
  covering AOAI gateway failover, retry-with-backoff, circuit breakers, deadline
  propagation, rate-limit-aware throttling, request hedging. Requires its own
  framing question + manifest signal. Big enough to warrant its own release.
- **ADO and GitLab `--scaffold-cicd` targets.** v0.5.0 still ships GitHub Actions
  templates only. v0.6.0+ may add Azure DevOps and GitLab CI as new scaffold
  targets if the field-test phase (G) surfaces customer demand.
- **Sibling-skill flips for awesome-gbb#267, #269, #270, #272.** Gated on upstream
  landings. When skills land, follow `sibling-skill-flip-protocol.md` runbook.
- **Remaining ~19 experimental recipes.** Pure research stubs. Promote one-by-one
  in v0.6.0+ as field signal arrives.
- **Real-customer field test execution (Phase G).** Protocol committed in v0.5.0;
  actual customer engagement is post-release work tracked as follow-up issues.
```

**Step 2: Re-run the SACRED RULE wording test** to confirm the v0.5.0 entry doesn't reintroduce the forbidden phrasing:

```
python3 -m unittest skills.threadlight-production-ready.tests.test_sacred_rule_wording -v
```

Expect pass.

**Step 3: Commit:**

```
docs(changelog): v0.5.0 entry with truthful "Deferred to v0.6.0+" section

Per locked invariant #6: deferred section names every bucket we did not ship.
Bucket 2 (gateway-resilience), ADO/GitLab scaffolds, 4 remaining sibling-skill
flips, ~19 remaining experimentals, and field-test execution are all named.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

## Phase G — Field-test protocol (aspirational, no code)

**Goal:** Commit the field-test protocol doc so v0.5.1 / v0.6.0 maintainers have a recipe for running threadlight against a real awesome-gbb pilot customer. No code changes; no test changes. This phase exists so the spec's promise is kept honestly.

### Task G1: Write `references/field-test-protocol.md`

**Files:**
- `skills/threadlight-production-ready/references/field-test-protocol.md` (new)

**Step 1: Create the doc** (~80 lines):

```markdown
# Field-test protocol for threadlight-production-ready

## Purpose

v0.4.0 + v0.5.0 ship against fixtures. Before v0.6.0 ships, the maintainer team
should run threadlight against 1-2 real awesome-gbb pilot customer repos and
fold friction points into the next CHANGELOG as "real-world hardening."

## Eligibility for pilot customers

Pick customers that meet ALL of:

1. Production-bound work currently underway (not pure prototype)
2. Customer team has signed an awesome-gbb engagement letter
3. At least one repo with `infra/` + `.github/workflows/` present
4. Customer security team has approved running an external assessor against
   the repo (read-only — assessor is read-only by SACRED RULE)

## Procedure

### Phase 1: Read-only assessment

1. Clone the customer repo to a scratch worktree.
2. Run:
   ```
   python3 skills/threadlight-production-ready/scripts/production_ready.py assess \
       --repo /path/to/customer/repo --out /tmp/threadlight-pilot/<customer>
   ```
3. Read `production-readiness-report.md`. Capture: any findings that surprise
   the customer team, any false positives, any missing categories.

### Phase 2: Framing wizard run

1. Run:
   ```
   python3 skills/threadlight-production-ready/scripts/production_ready.py frame \
       --out /tmp/threadlight-pilot/<customer>/framing.json
   ```
2. Walk through the 8 questions with a customer SRE present. Capture: any question
   that confused them, any question they couldn't answer without escalation.

### Phase 3: Apply-plan dispatch (subset)

1. Pick 2-3 low-risk recipes from the must-fix set (e.g. SUP-101, SRE-103 — they
   only write stub files).
2. Dispatch via the agent loop. Capture: dispatch latency, recipe clarity, whether
   the agent had to ask follow-up questions.

### Phase 4: Customer-overrides dry-run

1. Identify any finding the customer team disputes.
2. Write a `customer-overrides.yaml` flipping it (PASS → FAIL or FAIL → PASS).
3. Re-run `assess --customer-overrides ...`. Confirm the override applies + the
   audit-trail fields (`override_customer`, `override_reason`) appear in the
   report.

## Output

For each pilot customer, file a follow-up issue in `aiappsgbb/threadlight-skills`
titled `field-test: <customer-name> friction points`. Body should include:

- Recipe-level: which recipes fired falsely, which missed real issues
- Wizard-level: which questions caused confusion
- Override-level: how many overrides the customer needed; pattern across the overrides
- Dispatch-level: agent loop friction

Fold these into the v0.6.0 spec's "Motivation" section.

## What NOT to do

- Do not commit customer repos or customer findings to this repo.
- Do not write `customer-overrides.yaml` files for real customers to this repo.
- Do not bypass the SACRED RULE — assessor reads the customer repo, agent dispatches
  patches, customer team approves PRs. No third path.

## Sign-off

Each pilot run requires:

1. A maintainer of threadlight-skills (driver)
2. A customer SRE or engineer (subject-matter expert)
3. Optional: an awesome-gbb maintainer (skill alignment)
```

**Step 2: Commit:**

```
docs(field-test): protocol for v0.5.0 → v0.6.0 pilot runs (Bucket 6)

Aspirational doc. Commits the procedure for running threadlight against 1-2
awesome-gbb pilot customers between v0.5.0 ship and v0.6.0 scoping. Actual
customer engagement is post-release follow-up work, tracked as issues per
customer.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

### Task G2: File follow-up issues (no code)

After the v0.5.0 release lands, the maintainer files these follow-up issues:

- `field-test: pilot customer 1 friction points` (placeholder, opens when customer 1 is picked)
- `field-test: pilot customer 2 friction points` (placeholder, opens when customer 2 is picked)
- `gateway-resilience pillar (Bucket 2, deferred from v0.5.0)` — references the v0.5.0 spec's deferred section
- `awesome-gbb sibling flip: <recipe-id> when #NNN lands` (one per pending flip: #267, #269, #270, #272)
- `experimental promotion v0.6.0: pick next 5` — references `_experimental/` recipe directory and asks the maintainer to vote

These do not block the v0.5.0 release. They are tracked here so they aren't lost.

---

## Self-Review

### Spec coverage table

| Spec section | Plan phase | Notes |
|---|---|---|
| §Motivation (cleanup + closure release) | F (release artifacts) | CHANGELOG entry frames it |
| §Locked invariants | All phases respect | Phase E recipes use only the 4 allowed `kind` values; Phase F CHANGELOG covers locked invariant 6 |
| §Bucket 1 (5 follow-up issues) | A1 (#29), A2 (#32), A3 (stale string), B1-B5 (#30 + #33), C1-C4 (#31) | All 5 closed |
| §Bucket 3 (5 experimental promotions) | E1-E5 + E6 verification | All 5 named recipes promoted |
| §Bucket 4 (SPEC §12 overrides) | D1-D6 | Loader + validator + applier + CLI wiring + docs |
| §Bucket 5 (sibling-skill flips) | C4 runbook only | Gated on upstream; named in CHANGELOG deferred |
| §Bucket 6 (field test) | G1, G2 | Aspirational protocol committed; execution post-release |
| §Out-of-scope: Bucket 2 (gateway-resilience) | None — explicitly deferred | Named in CHANGELOG deferred |
| §Out-of-scope: ADO/GitLab scaffolds | None — explicitly deferred | A2 + A3 reinforce GitHub-Actions-only |
| §New tests catalog | A1, A2, A3, B1, B3, B5, C1, D1 | 3 new files + 5 extended files |
| §Open question 1 (Bucket 2 split) | Resolved: defer all to v0.6.0 | Reflected in CHANGELOG |
| §Open question 2 (experimental picks) | Resolved: 5 named in §Bucket 3 | Reflected in Phase E |
| §Open question 3 (overrides must-fix policy) | Resolved: loud reject + exit 2 | Reflected in D4 + D5 |
| §Open question 4 (field-test execution) | Resolved: protocol only in v0.5.0 | Reflected in Phase G |

All 4 spec open questions are answered inline. All 6 buckets accounted for. All 5 follow-up issues have a closing phase.

### Placeholder scan

Searched for "TBD", "TODO", "to be determined", "implement later", "similar to Task N", "add appropriate error handling" — **none present**. Every task has full code, exact commands, exact commit messages.

All commit-message trailers across all 29 tasks use the canonical `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` form.

### Type / signature consistency

- `_load_customer_overrides(path) → dict | None` ← consistent across D1 (test), D2 (impl), D5 (CLI wiring)
- `_validate_customer_overrides(ov) → None | raises ValueError` ← consistent across D1, D3, D5
- `_apply_customer_overrides(findings, ov) → list[dict] | sys.exit(2)` ← consistent across D1, D4, D5
- `_glob_repo(root, *patterns) → list[Path]` ← signature preserved in Phase B (only added a filter inside)
- `_check_*(ctx) → bool` ← consistent across all Phase E catalog entries
- `FRAMING_QUESTIONS: list[dict]` ← Phase B appends 1 dict, no schema change
- `VERSION: str` ← Phase F bumps value only, no type change

### Effort sanity

| Phase | Tasks | Approx new LOC (script + tests + docs) | Notes |
|---|---|---|---|
| A | 3 | ~80 (mostly docs) | All doc-only or one-line script fixes |
| B | 5 | ~150 (test 60, script 30, docs/fixtures 60) | Idempotency test is the biggest single file |
| C | 4 | ~90 (test 40, runbook 40, recipes 10) | Front-matter edits + runbook |
| D | 6 | ~350 (tests 180, script 120, docs/fixtures 50) | Biggest phase by far — overrides full vertical slice |
| E | 6 | ~180 (5 catalog edits + 5 recipe rewrites) | Mechanical; per-recipe template |
| F | 3 | ~100 (mostly CHANGELOG + SKILL.md) | Docs |
| G | 2 | ~80 (one doc) | Aspirational |
| **Total** | **29** | **~1030 LOC** | vs v0.4.0 ~2700 LOC. Ratio ≈ 0.38× — matches spec effort estimate |

v0.5.0 stays smaller than v0.4.0 — meets the "v0.5.0 should not be bigger than v0.4.0" constraint from the user brief.

### Locked-invariant compliance check

| Invariant | Plan compliance |
|---|---|
| 1. SACRED RULE | Phase A explicitly clarifies; Phase D `_apply_customer_overrides` only mutates the in-memory findings list, never writes to user repo |
| 2. stdlib-only tests | All new tests use `unittest` + `subprocess` + `pathlib` — no third-party imports |
| 3. 4-value `kind` taxonomy | Phase C flips and Phase E promotions use only `repo-edit`, `sibling-skill`, `manual`, `deferred-to-pipeline` — never invents new |
| 4. Recipe markdown shape | Phase E recipes copy `_template.md` structure verbatim (4 required `##` sections + 5th stale-plan-check); Phase C edits only change front-matter and `## Apply` body |
| 5. GitHub Actions only | Phase A explicitly purges ADO/GitLab; Phase F CHANGELOG reaffirms |
| 6. CHANGELOG deferred truthful | Phase F task F3 lists every de-scoped bucket by name |

---

## Execution Handoff

This plan is designed for either of two execution modes. Choose one:

### Recommended: subagent-driven

Use `subagent-driven-development` skill. Dispatch one subagent per phase. Phase
ordering matters: A → B → C must complete before D or E can run; D and E can
run in parallel (no file overlap); F depends on D + E both being done; G is
aspirational and can ship in any release.

Each subagent prompt should include:

1. The phase header from this plan (so the subagent knows the goal)
2. The relevant Task sections (with files, steps, commit messages)
3. The spec path (`docs/superpowers/specs/2026-06-10-threadlight-production-ready-v050-design.md`)
4. A copy of the locked invariants from the spec
5. The Co-authored-by trailer requirement
6. Explicit instruction: "Stop after committing all tasks in this phase. Do not
   start the next phase. Do not open a PR."

### Alternative: inline executor

Use `executing-plans` skill. The driver agent works through phases A → G in
order, running tests after each task, committing one task at a time. No phase
parallelism.

### Either mode

- After all 7 phases land, the agent stops and asks the user whether to open
  the v0.5.0 PR or wait.
- The agent does NOT bump version more than once (Phase F1 is the only VERSION
  edit).
- The agent does NOT push to a remote without user confirmation.
- The agent does NOT delete the v0.4.0 spec or plan from `docs/superpowers/`.

### Confirmation before starting

Before the first `git commit` of Phase A, the executor agent must:

1. Read this plan in full.
2. Read the v0.5.0 spec in full.
3. Confirm out loud: "Locked scope is 7 phases (A-G), 29 tasks, gateway-resilience
   deferred to v0.6.0, 5 experimentals promoted (NET-502, EVAL-101, EVAL-102,
   SUP-101, SRE-103), customer-overrides Bucket 4 lands in Phase D, field test
   aspirational in Phase G."
4. Run the full existing test suite from `main` (`python3 -m unittest discover
   skills/threadlight-production-ready/tests/ -v`) to establish a green baseline.

Only after baseline green does Task A1 start.

---

**End of plan.**
