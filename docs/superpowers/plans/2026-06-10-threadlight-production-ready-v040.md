# threadlight-production-ready v0.4.0 — "Production Onboarding" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the skill from a pure assessor into a 3-phase production-onboarding executor (Assess → Refine+Deploy → CI/CD Handoff) while keeping the Python script itself assessor-only. Edits flow through the Copilot agent (Edit/Write tools) and awesome-gbb sibling skills.

**Architecture:** The script keeps emitting `manifest.json`, `report.md`, `trend.csv`, and `scorecard.csv` exactly as v0.3.0 did, plus one new artifact: `apply-plan.json` — a machine-readable map of `{finding_id → recipe}` entries with four `kind` values (`repo-edit`, `sibling-skill`, `manual`, `deferred-to-pipeline`). The agent reads that file and drives Phase 2 (edits + sibling skills) and Phase 3 (CI/CD scaffold from templates). The script never edits the threadlight artifacts themselves; git diff is the audit trail.

**Tech Stack:** Python 3.11 stdlib only (no new deps), markdown for recipes, GitHub Actions YAML + Bicep (text-template only) for CI/CD scaffold, sibling skills invoked via the agent's Skill tool.

---

## Source-of-truth references

- **Design spec (locked):** `docs/superpowers/specs/2026-06-10-threadlight-production-ready-v040-design.md`
- **Script:** `skills/threadlight-production-ready/scripts/production_ready.py` (~3800 lines; v0.4.0 adds ~600 LOC net)
- **VERSION constant:** `scripts/production_ready.py:44` → bump from `"0.3.0"` to `"0.4.0"` in Module E
- **FINDING_CATALOG:** `scripts/production_ready.py:97-284` — 151 total, **61 must-fix non-experimental**, 64 should-fix non-experimental, 24 experimental
- **Args parser:** `scripts/production_ready.py:3672-3712`
- **main():** `scripts/production_ready.py:3715+`
- **SKILL.md:** `skills/threadlight-production-ready/SKILL.md` — metadata.version at L25, "recommends, never executes" contract at L81 (FLIPPED in Module E)
- **Existing fixtures:** `references/fixtures/sample-pilot{,-citadel,-broken,-v4}/` — Module G adds `sample-pilot-restricted`
- **Tests:** `tests/test_*.py` — 10 files, 61 functions, stdlib only, run with `python3 tests/test_<name>.py`
- **Co-authored-by trailer (required on every commit):** `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`

---

## Module → File map

| Module | Purpose | New/Modified files | New tests |
| --- | --- | --- | --- |
| **A** | Framing-Q wizard + `apply-plan.json` schema + dispatcher | `scripts/production_ready.py` (+~300 LOC) | `tests/test_framing_wizard.py`, `tests/test_apply_plan_schema.py` |
| **B** | Per-finding recipe catalog (61 must-fix recipes minimum) | `references/remediation-recipes/{ID}.md` × 61, `references/remediation-recipes/_template.md` | `tests/test_recipe_catalog.py` |
| **C** | Provisioning-rights probe + Phase decision logic | `scripts/production_ready.py` (+~150 LOC) | `tests/test_provisioning_rights.py` |
| **D** | CI/CD template + UAMI/FedCred runbook | `scripts/production_ready.py` (+~100 LOC), `references/cicd-templates/azd-deploy-prod.yml.tmpl`, `references/cicd-templates/central-team-onboarding.md.tmpl` | `tests/test_cicd_scaffold.py` |
| **E** | SKILL.md 3-phase rewrite + VERSION bump | `SKILL.md` (substantial rewrite), `scripts/production_ready.py:44` | Covered by Module G end-to-end test |
| **F** | Awesome-gbb sibling-skill invocation map | `references/sibling-skill-invocations.md` | `tests/test_sibling_skill_map.py` |
| **G** | Field test + `sample-pilot-restricted` fixture + end-to-end + CHANGELOG | `references/fixtures/sample-pilot-restricted/`, `CHANGELOG.md`, real-customer field findings folded into recipes | `tests/test_end_to_end_onboard.py`, fixture baseline tests |

---

## Phase A — Framing-Q wizard + `apply-plan.json` schema

**Purpose:** Add the `--onboard` mode dispatcher that gathers 7 framing answers (TTY prompts or `--framing-file PATH`), then writes a machine-readable `apply-plan.json` so the agent can drive Phase 2 deterministically.

**Files:**
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py:3672-3712` (args parser — add `--onboard`, `--framing-file`, `--apply-plan-out`, `--scaffold-cicd`, `--no-rights-probe`)
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py:3715+` (main() — branch on `--onboard`)
- Create: section `# region: framing_wizard` near top of script (~150 LOC)
- Create: section `# region: apply_plan_schema` near framing_wizard (~150 LOC)
- Create: `skills/threadlight-production-ready/tests/test_framing_wizard.py`
- Create: `skills/threadlight-production-ready/tests/test_apply_plan_schema.py`

### Task A1: Define the 7-question framing schema constant

- [ ] **Step 1: Write the failing test**

`tests/test_framing_wizard.py`:

```python
import os, sys, importlib.util, pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "production_ready.py"
spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def test_framing_questions_have_required_fields():
    qs = mod.FRAMING_QUESTIONS
    assert len(qs) == 7, "v0.4.0 ships exactly 7 framing questions"
    for q in qs:
        assert {"id", "prompt", "kind", "required"}.issubset(q.keys())
        assert q["kind"] in {"text", "choice", "bool"}
        if q["kind"] == "choice":
            assert q.get("choices"), f"choice question {q['id']} needs choices"

def test_framing_question_ids_are_canonical():
    ids = {q["id"] for q in mod.FRAMING_QUESTIONS}
    assert ids == {
        "target_subscription_id",
        "target_resource_group",
        "target_posture",
        "provisioning_rights",
        "central_platform_team",
        "restricted_environment",
        "cicd_target",
    }

if __name__ == "__main__":
    test_framing_questions_have_required_fields()
    test_framing_question_ids_are_canonical()
    print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

```
cd skills/threadlight-production-ready && python3 tests/test_framing_wizard.py
```

Expected: `AttributeError: module 'production_ready' has no attribute 'FRAMING_QUESTIONS'`

- [ ] **Step 3: Add FRAMING_QUESTIONS constant**

Insert near top of `production_ready.py`, right after the VERSION constant block:

```python
# region: framing_wizard

FRAMING_QUESTIONS = [
    {"id": "target_subscription_id", "prompt": "Azure subscription ID for the production target?", "kind": "text", "required": True},
    {"id": "target_resource_group", "prompt": "Resource group name for the production target?", "kind": "text", "required": True},
    {"id": "target_posture", "prompt": "Posture profile?", "kind": "choice", "choices": ["citadel-spoke", "standard-ai-gateway", "agt", "hybrid"], "required": True},
    {"id": "provisioning_rights", "prompt": "Do you have Contributor or higher on the target RG?", "kind": "bool", "required": True},
    {"id": "central_platform_team", "prompt": "Is there a central platform/Citadel team that owns shared infra (gateways, KV, networking)?", "kind": "bool", "required": True},
    {"id": "restricted_environment", "prompt": "Is direct write access to the target restricted (i.e. all changes must go through CI/CD)?", "kind": "bool", "required": True},
    {"id": "cicd_target", "prompt": "CI/CD target?", "kind": "choice", "choices": ["github-actions", "azure-devops", "none"], "required": True},
]
```

- [ ] **Step 4: Run test to verify it passes**

```
python3 tests/test_framing_wizard.py
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/scripts/production_ready.py \
        skills/threadlight-production-ready/tests/test_framing_wizard.py
git commit -m "feat(framing): add FRAMING_QUESTIONS canonical list

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task A2: TTY-driven framing wizard

- [ ] **Step 1: Write the failing test**

Append to `tests/test_framing_wizard.py`:

```python
import io

def test_wizard_reads_answers_from_stdin():
    fake_in = io.StringIO("00000000-0000-0000-0000-000000000000\nmy-rg\ncitadel-spoke\ny\nn\nn\ngithub-actions\n")
    fake_out = io.StringIO()
    answers = mod.run_framing_wizard(istream=fake_in, ostream=fake_out)
    assert answers["target_subscription_id"] == "00000000-0000-0000-0000-000000000000"
    assert answers["target_resource_group"] == "my-rg"
    assert answers["target_posture"] == "citadel-spoke"
    assert answers["provisioning_rights"] is True
    assert answers["central_platform_team"] is False
    assert answers["restricted_environment"] is False
    assert answers["cicd_target"] == "github-actions"

def test_wizard_rejects_invalid_choice():
    fake_in = io.StringIO("00000000-0000-0000-0000-000000000000\nmy-rg\nbanana\ncitadel-spoke\ny\nn\nn\ngithub-actions\n")
    fake_out = io.StringIO()
    answers = mod.run_framing_wizard(istream=fake_in, ostream=fake_out)
    assert answers["target_posture"] == "citadel-spoke"  # invalid input re-prompts
```

- [ ] **Step 2: Run test to verify failures**

Expected: `AttributeError: run_framing_wizard`

- [ ] **Step 3: Implement `run_framing_wizard`**

Add to the `framing_wizard` region:

```python
def _coerce_bool(s: str):
    s = s.strip().lower()
    if s in {"y", "yes", "true", "1"}: return True
    if s in {"n", "no", "false", "0"}: return False
    return None

def run_framing_wizard(istream=None, ostream=None):
    import sys
    istream = istream or sys.stdin
    ostream = ostream or sys.stdout
    answers = {}
    for q in FRAMING_QUESTIONS:
        while True:
            print(q["prompt"], file=ostream)
            if q["kind"] == "choice":
                print(f"  choices: {', '.join(q['choices'])}", file=ostream)
            raw = istream.readline()
            if not raw and q["required"]:
                raise SystemExit(f"framing wizard: EOF on required question {q['id']}")
            raw = raw.strip()
            if q["kind"] == "bool":
                v = _coerce_bool(raw)
                if v is None: continue
                answers[q["id"]] = v; break
            if q["kind"] == "choice":
                if raw not in q["choices"]: continue
                answers[q["id"]] = raw; break
            if not raw and q["required"]: continue
            answers[q["id"]] = raw; break
    return answers
```

- [ ] **Step 4: Run tests**

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(framing): TTY-driven 7-question wizard

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task A3: `--framing-file PATH` non-interactive mode

- [ ] **Step 1: Write the failing test**

```python
import json, tempfile, pathlib

def test_framing_file_loader_reads_json():
    payload = {
        "target_subscription_id": "00000000-0000-0000-0000-000000000000",
        "target_resource_group": "my-rg",
        "target_posture": "citadel-spoke",
        "provisioning_rights": True,
        "central_platform_team": False,
        "restricted_environment": True,
        "cicd_target": "github-actions",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(payload, f); tmp = pathlib.Path(f.name)
    answers = mod.load_framing_file(tmp)
    assert answers == payload

def test_framing_file_rejects_missing_required():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"target_subscription_id": "x"}, f); tmp = pathlib.Path(f.name)
    try:
        mod.load_framing_file(tmp); assert False, "should have raised"
    except SystemExit as e:
        assert "missing required" in str(e).lower()
```

- [ ] **Step 2: Run test, observe failure (`load_framing_file` missing)**

- [ ] **Step 3: Implement `load_framing_file`**

```python
def load_framing_file(path):
    import json, pathlib
    data = json.loads(pathlib.Path(path).read_text())
    required = [q["id"] for q in FRAMING_QUESTIONS if q["required"]]
    missing = [k for k in required if k not in data]
    if missing:
        raise SystemExit(f"framing file: missing required keys: {missing}")
    return data

# endregion: framing_wizard
```

- [ ] **Step 4: Run tests** → green

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(framing): --framing-file PATH non-interactive loader

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task A4: Apply-plan schema + writer

- [ ] **Step 1: Write the failing test**

`tests/test_apply_plan_schema.py`:

```python
import os, sys, importlib.util, pathlib, json, hashlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "production_ready.py"
spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def test_apply_plan_entry_kinds():
    assert mod.APPLY_PLAN_KINDS == {"repo-edit", "sibling-skill", "manual", "deferred-to-pipeline"}

def test_build_apply_plan_pins_manifest_sha():
    manifest = {"version": "0.4.0", "findings": []}
    plan = mod.build_apply_plan(manifest=manifest, recipes={}, framing={"target_posture": "citadel-spoke"})
    expected_sha = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    assert plan["manifest_sha256"] == expected_sha
    assert plan["schema_version"] == 1
    assert plan["framing"]["target_posture"] == "citadel-spoke"

def test_build_apply_plan_rejects_unknown_kind():
    manifest = {"version": "0.4.0", "findings": [{"id": "AGT-001", "status": "fail"}]}
    recipes = {"AGT-001": {"kind": "bogus", "summary": "x"}}
    try:
        mod.build_apply_plan(manifest=manifest, recipes=recipes, framing={})
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "bogus" in str(e)
```

- [ ] **Step 2: Run test, observe failure (`APPLY_PLAN_KINDS` missing)**

- [ ] **Step 3: Implement schema**

Insert after framing region:

```python
# region: apply_plan_schema

APPLY_PLAN_KINDS = {"repo-edit", "sibling-skill", "manual", "deferred-to-pipeline"}
APPLY_PLAN_SCHEMA_VERSION = 1

def build_apply_plan(*, manifest, recipes, framing):
    import json, hashlib
    sha = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    entries = []
    for f in manifest.get("findings", []):
        if f.get("status") not in {"fail", "warn"}:
            continue
        rid = f["id"]
        recipe = recipes.get(rid, {"kind": "manual", "summary": f"No recipe registered for {rid}"})
        if recipe["kind"] not in APPLY_PLAN_KINDS:
            raise SystemExit(f"apply-plan: recipe for {rid} has unknown kind {recipe['kind']!r}")
        entries.append({"finding_id": rid, **recipe})
    return {
        "schema_version": APPLY_PLAN_SCHEMA_VERSION,
        "manifest_sha256": sha,
        "framing": framing,
        "entries": entries,
    }

def write_apply_plan(plan, path):
    import json, pathlib
    pathlib.Path(path).write_text(json.dumps(plan, indent=2, sort_keys=True))

# endregion: apply_plan_schema
```

- [ ] **Step 4: Run tests** → green

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(apply-plan): schema + builder pinned to manifest sha

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task A5: Wire `--onboard` into args parser + dispatcher

- [ ] **Step 1: Write the failing test**

Append to `test_framing_wizard.py`:

```python
def test_args_parser_accepts_onboard_flags():
    args = mod._parse_args(["--onboard", "--framing-file", "/tmp/x.json",
                            "--apply-plan-out", "/tmp/apply-plan.json",
                            "--scaffold-cicd", "--no-rights-probe",
                            "--out", "/tmp/out", "--target-rg", "rg"])
    assert args.onboard is True
    assert args.framing_file == "/tmp/x.json"
    assert args.apply_plan_out == "/tmp/apply-plan.json"
    assert args.scaffold_cicd is True
    assert args.no_rights_probe is True
```

- [ ] **Step 2: Run test, observe failure**

- [ ] **Step 3: Extend `_parse_args` (around L3672-3712)**

Add inside `_parse_args`:

```python
parser.add_argument("--onboard", action="store_true",
    help="Enter 3-phase production onboarding mode (writes apply-plan.json)")
parser.add_argument("--framing-file", default=None,
    help="JSON file with framing answers (skips TTY wizard)")
parser.add_argument("--apply-plan-out", default=None,
    help="Path to write apply-plan.json (defaults to --out/apply-plan.json)")
parser.add_argument("--scaffold-cicd", action="store_true",
    help="Phase 3: write CI/CD scaffold templates to --out")
parser.add_argument("--no-rights-probe", action="store_true",
    help="Skip the live provisioning-rights probe (use framing answer only)")
```

- [ ] **Step 4: Run test** → green

- [ ] **Step 5: Wire dispatcher in `main()`**

At top of `main()` after args parse, before existing assessor flow:

```python
if args.onboard:
    framing = (load_framing_file(args.framing_file) if args.framing_file
               else run_framing_wizard())
    manifest = _run_assessment_for_onboard(args, framing)  # existing assessor flow,
                                                            # returns manifest dict
    recipes = load_recipe_catalog(_recipe_catalog_dir())
    plan = build_apply_plan(manifest=manifest, recipes=recipes, framing=framing)
    out = args.apply_plan_out or str(pathlib.Path(args.out) / "apply-plan.json")
    write_apply_plan(plan, out)
    print(_phase_decision_banner(framing, plan))
    return 0
```

(Stubs: `_run_assessment_for_onboard`, `load_recipe_catalog`, `_recipe_catalog_dir`, `_phase_decision_banner` are filled in Tasks A6 / B / C — leave as `NotImplementedError` for now.)

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(onboard): wire --onboard dispatcher + 5 new flags

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task A6: Recipe catalog loader

- [ ] **Step 1: Write the failing test**

```python
def test_recipe_catalog_loader_parses_markdown(tmp_path):
    rdir = tmp_path / "remediation-recipes"
    rdir.mkdir()
    (rdir / "AGT-001.md").write_text("""---
kind: repo-edit
summary: Set defaultPolicyContentType to JSON
target_file: infra/foundry/agent.bicep
edit_type: replace
---

## Target file
infra/foundry/agent.bicep

## Edit type
replace

## Edit recipe
Replace `defaultPolicyContentType: 'XML'` with `defaultPolicyContentType: 'JSON'`.

## Verification
Re-run threadlight, AGT-001 status flips to `pass`.
""")
    recipes = mod.load_recipe_catalog(rdir)
    assert "AGT-001" in recipes
    assert recipes["AGT-001"]["kind"] == "repo-edit"
    assert recipes["AGT-001"]["summary"].startswith("Set defaultPolicyContentType")
```

- [ ] **Step 2: Run, observe failure (`load_recipe_catalog` missing)**

- [ ] **Step 3: Implement loader**

In `apply_plan_schema` region:

```python
def _recipe_catalog_dir():
    import pathlib
    return pathlib.Path(__file__).resolve().parent.parent / "references" / "remediation-recipes"

def load_recipe_catalog(rdir):
    import pathlib, re
    recipes = {}
    for path in sorted(pathlib.Path(rdir).glob("*.md")):
        if path.name.startswith("_"):  # skip _template.md
            continue
        rid = path.stem
        text = path.read_text()
        if not text.startswith("---\n"):
            raise SystemExit(f"recipe {rid}: missing YAML front-matter")
        _, fm, body = text.split("---\n", 2)
        meta = {}
        for line in fm.strip().splitlines():
            if ":" not in line: continue
            k, v = line.split(":", 1); meta[k.strip()] = v.strip()
        if meta.get("kind") not in APPLY_PLAN_KINDS:
            raise SystemExit(f"recipe {rid}: kind {meta.get('kind')!r} not in {APPLY_PLAN_KINDS}")
        recipes[rid] = {
            "kind": meta["kind"],
            "summary": meta.get("summary", ""),
            "target_file": meta.get("target_file"),
            "edit_type": meta.get("edit_type"),
            "recipe_path": str(path),
        }
    return recipes
```

- [ ] **Step 4: Run test** → green

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(apply-plan): recipe catalog loader (markdown + YAML front-matter)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---
## Phase B — Per-finding recipe catalog (61 must-fix recipes)

**Purpose:** Every must-fix non-experimental finding gets a markdown recipe at `references/remediation-recipes/{ID}.md`. The recipe is the source of truth that turns "AGT-001 failed" into either (a) a deterministic Edit/Write the agent runs, (b) a sibling skill invocation, (c) a manual instruction with explicit reasoning, or (d) a "this is the pipeline's job" deferral. Should-fix recipes are optional in v0.4.0; if missing, `apply_plan` materializes a `kind: manual` placeholder.

**Files:**
- Create: `skills/threadlight-production-ready/references/remediation-recipes/_template.md`
- Create: `skills/threadlight-production-ready/references/remediation-recipes/{ID}.md` × 61
- Create: `skills/threadlight-production-ready/tests/test_recipe_catalog.py`

**Must-fix per-pillar breakdown (61 total):**

| Pillar | Count | IDs |
| --- | --- | --- |
| agent-governance | 5 | AGT-001, AGT-002, AGT-005, AGT-V4-001, AGT-V4-006 |
| continuous-evals | 3 | EVAL-001, EVAL-002, EVAL-003 |
| cost | 3 | COST-001, COST-002, COST-101 |
| hitl-audit | 3 | HITL-002, HITL-003, HITL-101 |
| identity-access | 5 | IAM-001, IAM-002, IAM-003, IAM-101, IAM-102 |
| model-lifecycle | 4 | MDL-001, MDL-004, MDL-101, MDL-110 |
| network-posture | 6 | NET-001, NET-002, NET-003, NET-101, NET-102, NET-501 |
| observability | 8 | OBS-001, OBS-002, OBS-003, OBS-101, OBS-102, OBS-104, OBS-105, OBS-106 |
| reliability | 5 | REL-001, REL-002, REL-003, REL-007, REL-102 |
| responsible-ai | 4 | RAI-001, RAI-002, RAI-003, RAI-101 |
| secrets | 8 | SEC-001, SEC-002, SEC-003, SEC-005, SEC-007, SEC-101, SEC-102, SEC-103 |
| sre-handover | 4 | SRE-001, SRE-002, SRE-101, GOV-201 |
| supply-chain | 3 | SUP-001, SUP-002, SUP-003 |

### Task B1: Recipe template

- [ ] **Step 1: Create `_template.md`**

`references/remediation-recipes/_template.md`:

```markdown
---
kind: repo-edit | sibling-skill | manual | deferred-to-pipeline
summary: One-line description of what fixes this finding
target_file: relative/path/from/repo/root.bicep
edit_type: replace | insert | append | delete
sibling_skill: optional-skill-name-from-awesome-gbb
---

## Target file
`relative/path/from/repo/root.bicep`

## Edit type
`replace` (concrete: what kind of edit the agent should make)

## Edit recipe
Concrete instructions for the agent — exact strings to find, exact text to insert, or exact sibling-skill name + inputs to invoke.

## Verification
How the agent (or operator) confirms the fix worked. Usually: re-run `production_ready.py --target-rg <RG> --target-sub <SUB>` and check that the finding flips from `fail` to `pass`.
```

- [ ] **Step 2: Commit**

```bash
git add skills/threadlight-production-ready/references/remediation-recipes/_template.md
git commit -m "docs(recipes): canonical recipe template

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task B2: Recipe catalog enforcement test

- [ ] **Step 1: Write the failing test (will be red until B3-B15 land)**

`tests/test_recipe_catalog.py`:

```python
import os, sys, importlib.util, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_ready.py"
RDIR = ROOT / "references" / "remediation-recipes"

spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

REQUIRED_SECTIONS = ("## Target file", "## Edit type", "## Edit recipe", "## Verification")

def _must_fix_ids():
    out = []
    for fid, meta in mod.FINDING_CATALOG.items():
        if meta.get("severity") == "must-fix" and not meta.get("experimental"):
            out.append(fid)
    return sorted(out)

def test_every_must_fix_has_recipe():
    missing = [fid for fid in _must_fix_ids() if not (RDIR / f"{fid}.md").exists()]
    assert not missing, f"must-fix findings without recipe: {missing}"

def test_every_recipe_has_required_sections():
    bad = []
    for path in RDIR.glob("*.md"):
        if path.name.startswith("_"): continue
        text = path.read_text()
        for sec in REQUIRED_SECTIONS:
            if sec not in text: bad.append(f"{path.name}: missing {sec!r}")
    assert not bad, "\n".join(bad)

def test_loader_accepts_every_recipe():
    mod.load_recipe_catalog(RDIR)  # raises on bad YAML / bad kind

if __name__ == "__main__":
    test_every_must_fix_has_recipe()
    test_every_recipe_has_required_sections()
    test_loader_accepts_every_recipe()
    print("OK")
```

- [ ] **Step 2: Run, expect RED** — this is the gate that turns green only when Phase B is complete.

```
python3 tests/test_recipe_catalog.py
```

Expected: `AssertionError: must-fix findings without recipe: [...61 IDs...]`

- [ ] **Step 3: Commit (red test as gate)**

```bash
git add skills/threadlight-production-ready/tests/test_recipe_catalog.py
git commit -m "test(recipes): enforce recipe coverage for must-fix findings (currently red)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task B3: Exemplar — NET-002 (repo-edit, replace)

NET-002 is a "private endpoint approval state = Approved" finding. This is the canonical `kind: repo-edit` example for the executor.

- [ ] **Step 1: Create `references/remediation-recipes/NET-002.md`**

```markdown
---
kind: repo-edit
summary: Set private endpoint privateLinkServiceConnections[].privateLinkServiceConnectionState.status to 'Approved' in Bicep
target_file: infra/foundry/private-endpoint.bicep
edit_type: replace
---

## Target file
`infra/foundry/private-endpoint.bicep` (canonical name; agent should search for any `privateEndpoint` resource that targets the Foundry account if path differs).

## Edit type
`replace`

## Edit recipe
1. Locate the `Microsoft.Network/privateEndpoints` resource targeting the Foundry account.
2. Inside `properties.privateLinkServiceConnections[0].privateLinkServiceConnectionState`, find the `status` property.
3. Replace any value other than `'Approved'` with `'Approved'`.
4. If `privateLinkServiceConnectionState` is missing entirely, insert:
   ```bicep
   privateLinkServiceConnectionState: {
     status: 'Approved'
     description: 'Auto-approved by threadlight-production-ready v0.4.0 onboarding'
   }
   ```
5. If the Bicep references `manualPrivateLinkServiceConnections` instead, prefer migrating that resource to `privateLinkServiceConnections` (manual approval is the v0.3.0 anti-pattern that triggered NET-002).

## Verification
Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`. NET-002 should flip from `fail` to `pass`. If still failing, the live PE in Azure may need `az network private-endpoint-connection approve` — the recipe stops at the Bicep edit; pipeline runs the approval.
```

- [ ] **Step 2: Commit**

```bash
git add skills/threadlight-production-ready/references/remediation-recipes/NET-002.md
git commit -m "docs(recipes): NET-002 (repo-edit) — PE auto-approval in Bicep

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task B4: Exemplar — NET-501 (sibling-skill)

NET-501 (Citadel-spoke posture: spoke must reach hub APIM) is the canonical `kind: sibling-skill` example because the fix lives in the awesome-gbb `citadel-spoke-onboarding` skill.

- [ ] **Step 1: Create `references/remediation-recipes/NET-501.md`**

```markdown
---
kind: sibling-skill
summary: Onboard the Foundry spoke to the Citadel hub (peering + APIM Access Contract)
sibling_skill: citadel-spoke-onboarding
---

## Target file
N/A — this fix runs the awesome-gbb sibling skill `citadel-spoke-onboarding`, which performs network peering and APIM Access Contract creation in the customer's environment.

## Edit type
`sibling-skill`

## Edit recipe
Invoke the awesome-gbb skill `citadel-spoke-onboarding` via the Copilot Skill tool with inputs:

```json
{
  "spoke_subscription_id": "<framing.target_subscription_id>",
  "spoke_resource_group": "<framing.target_resource_group>",
  "hub_subscription_id": "<discovered from posture context — see SKILL.md §Hub discovery>",
  "hub_apim_resource_id": "<discovered>",
  "access_contract_product": "<framing.target_posture-specific; see citadel-spoke-onboarding SKILL.md>"
}
```

If hub identity is unknown, the agent should ask the user before invoking — never guess hub coordinates.

## Verification
Re-run threadlight: NET-501 should flip from `fail` to `pass` once the Access Contract reconciles in APIM. Allow ~5 minutes for propagation. If still failing, check the sibling-skill's own output for partial provisioning state.
```

- [ ] **Step 2: Commit**

```bash
git add skills/threadlight-production-ready/references/remediation-recipes/NET-501.md
git commit -m "docs(recipes): NET-501 (sibling-skill) — Citadel spoke onboarding

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task B5: Exemplar — SEC-005 (manual)

SEC-005 (Key Vault soft-delete + purge protection both enabled) is the canonical `kind: manual` example because flipping `enablePurgeProtection: true` is irreversible — we explicitly want a human to acknowledge the lock-in.

- [ ] **Step 1: Create `references/remediation-recipes/SEC-005.md`**

```markdown
---
kind: manual
summary: Enable Key Vault soft-delete + purge protection (irreversible — requires operator acknowledgement)
target_file: infra/foundry/kv.bicep
edit_type: manual
---

## Target file
`infra/foundry/kv.bicep` (or wherever the KV resource is declared).

## Edit type
`manual` — DO NOT auto-apply. Purge protection cannot be disabled once enabled; the operator must acknowledge the lock-in.

## Edit recipe
Present this to the operator:

> Key Vault `<KV name>` is missing `enablePurgeProtection: true`. Enabling this is **irreversible** — once set, the vault and its secrets cannot be hard-deleted for the soft-delete retention period (default 90 days), even by subscription owners. This is the required production posture. Proceed?
>
> If yes, the agent will edit `infra/foundry/kv.bicep` to add:
> ```bicep
> properties: {
>   enableSoftDelete: true
>   enablePurgeProtection: true
>   softDeleteRetentionInDays: 90
> }
> ```
> and commit the change. If no, mark this finding as accepted-risk in the apply-plan and continue.

## Verification
Re-run threadlight after Bicep deploy. SEC-005 flips to `pass`. To confirm at the data-plane: `az keyvault show -n <name> --query "properties.enablePurgeProtection"` returns `true`.
```

- [ ] **Step 2: Commit**

```bash
git add skills/threadlight-production-ready/references/remediation-recipes/SEC-005.md
git commit -m "docs(recipes): SEC-005 (manual) — KV purge protection acknowledgement

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task B6: Exemplar — REL-102 (deferred-to-pipeline)

REL-102 (multi-region failover smoke test passed) is the canonical `kind: deferred-to-pipeline` example because we cannot meaningfully run a regional failover at apply time — it must live in the production CI/CD pipeline as a post-deploy gate.

- [ ] **Step 1: Create `references/remediation-recipes/REL-102.md`**

```markdown
---
kind: deferred-to-pipeline
summary: Multi-region failover smoke test runs in CI/CD post-deploy
---

## Target file
`.github/workflows/azd-deploy-prod.yml` (created by Phase 3 scaffold in Module D)

## Edit type
`deferred-to-pipeline` — the assessment cannot run a regional failover at apply time. Phase 3 must include a post-deploy gate.

## Edit recipe
When Phase 3 (Module D) writes the CI/CD scaffold, ensure the post-deploy job contains a step that:
1. Calls the failover endpoint (recorded in framing.target_posture-specific manifest section).
2. Asserts HTTP 200 from the secondary region.
3. Fails the workflow if the assertion fails.

The scaffold template `references/cicd-templates/azd-deploy-prod.yml.tmpl` already contains the `smoke-failover` job stub — no additional edit is required here beyond making sure Phase 3 ran.

## Verification
Pipeline run shows `smoke-failover` job green. Threadlight REL-102 flips to `pass` once the manifest records a successful pipeline run within the last 7 days.
```

- [ ] **Step 2: Commit**

```bash
git add skills/threadlight-production-ready/references/remediation-recipes/REL-102.md
git commit -m "docs(recipes): REL-102 (deferred-to-pipeline) — failover smoke test

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task B7-B13: Remaining 56 recipes, one commit per pillar bucket

For the remaining 56 must-fix recipes, work pillar-by-pillar. Each pillar bucket = one commit. Use the patterns established in B3-B6:

- Bicep / repo-edit fixes (most common pattern) → mirror NET-002 (B3)
- Awesome-gbb skill invocations → mirror NET-501 (B4)
- Irreversible or judgment-call fixes → mirror SEC-005 (B5)
- Things the pipeline must verify (load test, failover drill, alert fire-and-receive) → mirror REL-102 (B6)

Default rule: if the fix is a single deterministic edit to a Bicep/Terraform/YAML/JSON file the assessor can identify, use `repo-edit`. If the fix needs cross-resource coordination (peering, identity, gateway), use `sibling-skill`. If it's irreversible, use `manual`. If it can only be observed in a running pipeline, use `deferred-to-pipeline`.

- [ ] **B7 — agent-governance (5 recipes)**: AGT-001, AGT-002, AGT-005, AGT-V4-001, AGT-V4-006. Commit: `docs(recipes): agent-governance recipes (B7)`.

- [ ] **B8 — continuous-evals + cost (6 recipes)**: EVAL-001..003, COST-001, COST-002, COST-101. Commit: `docs(recipes): continuous-evals + cost recipes (B8)`.

- [ ] **B9 — hitl-audit + identity-access (8 recipes)**: HITL-002, HITL-003, HITL-101, IAM-001..003, IAM-101, IAM-102. Commit: `docs(recipes): hitl-audit + identity-access recipes (B9)`.

- [ ] **B10 — model-lifecycle (4 recipes)**: MDL-001, MDL-004, MDL-101, MDL-110. Commit: `docs(recipes): model-lifecycle recipes (B10)`.

- [ ] **B11 — network-posture remaining (5 recipes)**: NET-001, NET-003, NET-101, NET-102 (NET-002 done in B3, NET-501 done in B4). Commit: `docs(recipes): network-posture recipes (B11)`.

- [ ] **B12 — observability (8 recipes)**: OBS-001..003, OBS-101, OBS-102, OBS-104..106. Largest single bucket. Commit: `docs(recipes): observability recipes (B12)`.

- [ ] **B13 — reliability + responsible-ai (8 recipes)**: REL-001..003, REL-007, REL-102 (done in B6 — skip), RAI-001..003, RAI-101. Commit: `docs(recipes): reliability + responsible-ai recipes (B13)`.

- [ ] **B14 — secrets (7 recipes; SEC-005 done in B5 — skip)**: SEC-001..003, SEC-007, SEC-101..103. Commit: `docs(recipes): secrets recipes (B14)`.

- [ ] **B15 — sre-handover + supply-chain (7 recipes)**: SRE-001, SRE-002, SRE-101, GOV-201, SUP-001..003. Commit: `docs(recipes): sre-handover + supply-chain recipes (B15)`.

### Task B16: Turn the catalog test green

- [ ] **Step 1: Run the gate test**

```
python3 tests/test_recipe_catalog.py
```

Expected: `OK` (all 61 must-fix have recipes; all recipes have required sections; loader accepts every recipe).

- [ ] **Step 2: If RED, fix the gap**

The error message names exact missing IDs or sections — add the missing recipe(s) in a follow-up commit, then re-run.

- [ ] **Step 3: Commit (gate now green)**

If you needed cleanup commits, the final one closes Phase B:

```bash
git commit --allow-empty -m "test(recipes): catalog gate now green (Phase B complete)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---
## Phase C — Provisioning-rights probe + Phase decision banner

**Purpose:** Decide at assess time whether the agent will actually be able to execute Phase 2 in the target subscription. The probe enumerates the operator's effective Azure RBAC against the target RG, classifies the result as `full | constrained | none`, and emits a banner so the operator sees `Phase 2 ⇒ self-service` or `Phase 2 ⇒ central-team handoff` BEFORE any apply-plan is materialized.

**Files:**
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py` (add `_probe_provisioning_rights`, `_phase_decision`, banner emission)
- Create: `skills/threadlight-production-ready/tests/test_rights_probe.py`
- Create: `skills/threadlight-production-ready/tests/test_phase_decision.py`

**Design constraints** (from approved spec §Phase 1):
- The probe is **live RBAC** — it shells `az role assignment list --assignee <currentUser> --scope <RG-scope>` and parses roles.
- `--no-rights-probe` skips the probe entirely and forces `rights_class=unknown` (used by CI to bypass live calls).
- Classification rules:
  - `full` — operator has at least one of {Owner, Contributor, User Access Administrator} at the RG scope (or higher).
  - `constrained` — operator has Reader-equivalent but no write roles; phase decision = central-team handoff.
  - `none` — operator has no role assignments at the RG scope (likely needs an access request first).
  - `unknown` — probe skipped or failed; phase decision = warn-and-continue (assume `constrained`).

### Task C1: Define `RightsClass` constants + `PhaseDecision` shape

- [ ] **Step 1: Add to `production_ready.py` near the top of the file (after constants block, ~L60)**

```python
RIGHTS_FULL = "full"
RIGHTS_CONSTRAINED = "constrained"
RIGHTS_NONE = "none"
RIGHTS_UNKNOWN = "unknown"

PHASE_SELF_SERVICE = "self-service"
PHASE_CENTRAL_HANDOFF = "central-team handoff"
PHASE_BLOCKED = "blocked — no RG access"

WRITE_ROLES = ("Owner", "Contributor", "User Access Administrator")
READ_ROLES = ("Reader", "Monitoring Reader", "Cost Management Reader")
```

- [ ] **Step 2: Commit**

```bash
git add skills/threadlight-production-ready/scripts/production_ready.py
git commit -m "feat(rights): add rights-class + phase-decision constants

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task C2: TDD — `_classify_rights(roles: list[str]) -> str`

- [ ] **Step 1: Write the failing test**

`tests/test_rights_probe.py`:

```python
import importlib.util, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("production_ready", ROOT / "scripts" / "production_ready.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def test_full_when_owner():
    assert mod._classify_rights(["Owner"]) == mod.RIGHTS_FULL

def test_full_when_contributor():
    assert mod._classify_rights(["Contributor"]) == mod.RIGHTS_FULL

def test_full_when_uaa():
    assert mod._classify_rights(["User Access Administrator"]) == mod.RIGHTS_FULL

def test_constrained_when_only_reader():
    assert mod._classify_rights(["Reader"]) == mod.RIGHTS_CONSTRAINED

def test_constrained_when_only_monitoring_reader():
    assert mod._classify_rights(["Monitoring Reader"]) == mod.RIGHTS_CONSTRAINED

def test_none_when_empty():
    assert mod._classify_rights([]) == mod.RIGHTS_NONE

def test_full_wins_over_reader():
    assert mod._classify_rights(["Reader", "Contributor"]) == mod.RIGHTS_FULL

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"): fn()
    print("OK")
```

- [ ] **Step 2: Run, expect FAIL** (`_classify_rights` not defined)

- [ ] **Step 3: Implement `_classify_rights`** in `production_ready.py`:

```python
def _classify_rights(roles):
    role_set = {r.strip() for r in roles if r}
    if role_set & set(WRITE_ROLES):
        return RIGHTS_FULL
    if role_set & set(READ_ROLES):
        return RIGHTS_CONSTRAINED
    return RIGHTS_NONE
```

- [ ] **Step 4: Run test → PASS**

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/scripts/production_ready.py skills/threadlight-production-ready/tests/test_rights_probe.py
git commit -m "feat(rights): _classify_rights from role list

Co-authored-by: Copilot <223556219+Copilot@users.noreply.function.com>"
```

> ⚠ Fix the typo in the commit-trailer above before running: `noreply.github.com`. The plan is reproduced verbatim, including the typo, so the executor sees and corrects it (Copilot CLI rejects commits with malformed trailers).

### Task C3: TDD — `_probe_provisioning_rights(subscription_id, resource_group) -> dict`

The probe shells `az role assignment list` for the current signed-in user. Output is parsed JSON. We mock `subprocess.run` in tests; we do not test against a live subscription.

- [ ] **Step 1: Add to `tests/test_rights_probe.py`**

```python
import subprocess, json
from unittest.mock import patch, MagicMock

def _fake_run(returncode=0, stdout="[]", stderr=""):
    m = MagicMock(); m.returncode = returncode; m.stdout = stdout; m.stderr = stderr
    return m

def test_probe_owner():
    fake = _fake_run(stdout=json.dumps([{"roleDefinitionName": "Owner"}]))
    with patch.object(subprocess, "run", return_value=fake):
        result = mod._probe_provisioning_rights("00000000-0000-0000-0000-000000000000", "rg-test")
    assert result["rights_class"] == mod.RIGHTS_FULL
    assert result["roles"] == ["Owner"]
    assert result["probe_skipped"] is False

def test_probe_empty():
    fake = _fake_run(stdout="[]")
    with patch.object(subprocess, "run", return_value=fake):
        result = mod._probe_provisioning_rights("sub", "rg")
    assert result["rights_class"] == mod.RIGHTS_NONE
    assert result["roles"] == []

def test_probe_az_failure_returns_unknown():
    fake = _fake_run(returncode=1, stderr="ERROR: please run 'az login'")
    with patch.object(subprocess, "run", return_value=fake):
        result = mod._probe_provisioning_rights("sub", "rg")
    assert result["rights_class"] == mod.RIGHTS_UNKNOWN
    assert "az login" in result["error"]
```

- [ ] **Step 2: Run → FAIL** (function not defined)

- [ ] **Step 3: Implement `_probe_provisioning_rights`**:

```python
def _probe_provisioning_rights(subscription_id, resource_group):
    """Shell `az role assignment list --assignee <me> --scope <RG-scope>`,
    classify the returned roles. Returns a dict suitable for the manifest.
    Never raises — on any error returns rights_class=unknown with the error string.
    """
    scope = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    cmd = ["az", "role", "assignment", "list", "--assignee", "@me", "--scope", scope, "-o", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        return {"rights_class": RIGHTS_UNKNOWN, "roles": [], "probe_skipped": False,
                "error": f"az invocation failed: {e}"}
    if r.returncode != 0:
        return {"rights_class": RIGHTS_UNKNOWN, "roles": [], "probe_skipped": False,
                "error": r.stderr.strip() or "az exited non-zero"}
    try:
        data = json.loads(r.stdout or "[]")
    except json.JSONDecodeError as e:
        return {"rights_class": RIGHTS_UNKNOWN, "roles": [], "probe_skipped": False,
                "error": f"json parse failed: {e}"}
    roles = [a.get("roleDefinitionName", "") for a in data]
    return {"rights_class": _classify_rights(roles), "roles": roles, "probe_skipped": False, "error": None}
```

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/scripts/production_ready.py skills/threadlight-production-ready/tests/test_rights_probe.py
git commit -m "feat(rights): live RBAC probe with az role assignment list

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task C4: TDD — `--no-rights-probe` short-circuits to `unknown`

- [ ] **Step 1: Add test**

```python
def test_probe_skipped_flag_short_circuits():
    result = mod._probe_provisioning_rights("sub", "rg", skip=True)
    assert result["rights_class"] == mod.RIGHTS_UNKNOWN
    assert result["probe_skipped"] is True
    assert result["roles"] == []
```

- [ ] **Step 2: Update `_probe_provisioning_rights` signature**:

```python
def _probe_provisioning_rights(subscription_id, resource_group, skip=False):
    if skip:
        return {"rights_class": RIGHTS_UNKNOWN, "roles": [], "probe_skipped": True, "error": None}
    # ... rest unchanged
```

- [ ] **Step 3: Run → PASS**

- [ ] **Step 4: Commit**

```bash
git commit -am "feat(rights): --no-rights-probe short-circuit

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task C5: TDD — `_phase_decision(framing, rights_result) -> dict`

- [ ] **Step 1: Write `tests/test_phase_decision.py`**

```python
import importlib.util, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("production_ready", ROOT / "scripts" / "production_ready.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def _framing(rights="full", restricted=False):
    return {"provisioning_rights": rights, "restricted_environment": restricted}

def test_full_rights_unrestricted_selfservice():
    rights = {"rights_class": mod.RIGHTS_FULL, "roles": ["Owner"], "probe_skipped": False, "error": None}
    d = mod._phase_decision(_framing("full", False), rights)
    assert d["phase2_mode"] == mod.PHASE_SELF_SERVICE

def test_constrained_rights_central_handoff():
    rights = {"rights_class": mod.RIGHTS_CONSTRAINED, "roles": ["Reader"], "probe_skipped": False, "error": None}
    d = mod._phase_decision(_framing("constrained", False), rights)
    assert d["phase2_mode"] == mod.PHASE_CENTRAL_HANDOFF

def test_restricted_environment_forces_handoff_even_with_owner():
    rights = {"rights_class": mod.RIGHTS_FULL, "roles": ["Owner"], "probe_skipped": False, "error": None}
    d = mod._phase_decision(_framing("full", True), rights)
    assert d["phase2_mode"] == mod.PHASE_CENTRAL_HANDOFF
    assert "restricted environment" in d["reason"].lower()

def test_unknown_rights_warns_and_assumes_constrained():
    rights = {"rights_class": mod.RIGHTS_UNKNOWN, "roles": [], "probe_skipped": True, "error": None}
    d = mod._phase_decision(_framing("full", False), rights)
    assert d["phase2_mode"] == mod.PHASE_CENTRAL_HANDOFF
    assert d["warning"] is not None

def test_no_rights_blocked():
    rights = {"rights_class": mod.RIGHTS_NONE, "roles": [], "probe_skipped": False, "error": None}
    d = mod._phase_decision(_framing("full", False), rights)
    assert d["phase2_mode"] == mod.PHASE_BLOCKED

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"): fn()
    print("OK")
```

- [ ] **Step 2: Run → FAIL** (`_phase_decision` not defined)

- [ ] **Step 3: Implement**:

```python
def _phase_decision(framing, rights_result):
    """Decide phase-2 mode from framing + live rights probe. Pure function — no I/O."""
    rclass = rights_result["rights_class"]
    if framing.get("restricted_environment"):
        return {"phase2_mode": PHASE_CENTRAL_HANDOFF,
                "reason": "Framing-Q6 marked restricted environment — handoff regardless of rights",
                "warning": None}
    if rclass == RIGHTS_NONE:
        return {"phase2_mode": PHASE_BLOCKED,
                "reason": "Operator has no role assignments at target RG scope",
                "warning": "Request access (Contributor or Owner) before re-running"}
    if rclass == RIGHTS_FULL:
        return {"phase2_mode": PHASE_SELF_SERVICE,
                "reason": "Operator has write-capable role at RG scope",
                "warning": None}
    if rclass == RIGHTS_UNKNOWN:
        return {"phase2_mode": PHASE_CENTRAL_HANDOFF,
                "reason": "Rights probe skipped or failed — assuming constrained",
                "warning": "Re-run without --no-rights-probe to confirm self-service eligibility"}
    return {"phase2_mode": PHASE_CENTRAL_HANDOFF,
            "reason": f"Operator rights classified as {rclass}",
            "warning": None}
```

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/scripts/production_ready.py skills/threadlight-production-ready/tests/test_phase_decision.py
git commit -m "feat(rights): _phase_decision banner logic

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task C6: Wire probe + decision into `main()` and emit banner

- [ ] **Step 1: In `main()`, after framing is loaded and before assessment runs, insert:**

```python
rights_result = _probe_provisioning_rights(
    args.target_sub or framing.get("target_subscription_id"),
    args.target_rg  or framing.get("target_resource_group"),
    skip=args.no_rights_probe,
)
phase_dec = _phase_decision(framing, rights_result)
_emit_phase_banner(framing, rights_result, phase_dec, sink=sys.stderr)
manifest["rights_probe"] = rights_result
manifest["phase_decision"] = phase_dec
```

- [ ] **Step 2: Implement `_emit_phase_banner`**:

```python
def _emit_phase_banner(framing, rights, decision, sink=sys.stderr):
    bar = "━" * 64
    print(bar, file=sink)
    print(f"THREADLIGHT v0.4.0 — Production Onboarding", file=sink)
    print(f"  Target: {framing.get('target_subscription_id')}/{framing.get('target_resource_group')}", file=sink)
    print(f"  Posture: {framing.get('target_posture')}", file=sink)
    print(f"  Rights:  {rights['rights_class']}  (roles: {', '.join(rights['roles']) or '—'})", file=sink)
    print(f"  Phase 2: ⇒ {decision['phase2_mode']}", file=sink)
    print(f"           reason: {decision['reason']}", file=sink)
    if decision.get("warning"):
        print(f"  ⚠ warning: {decision['warning']}", file=sink)
    print(bar, file=sink)
```

- [ ] **Step 3: Add an end-to-end smoke test** in `tests/test_phase_decision.py`:

```python
def test_banner_emits_without_error(capsys=None):
    import io
    framing = {"target_subscription_id": "sub", "target_resource_group": "rg",
               "target_posture": "standard-ai-gateway", "restricted_environment": False}
    rights  = {"rights_class": mod.RIGHTS_FULL, "roles": ["Owner"], "probe_skipped": False, "error": None}
    dec     = mod._phase_decision(framing, rights)
    buf = io.StringIO()
    mod._emit_phase_banner(framing, rights, dec, sink=buf)
    text = buf.getvalue()
    assert "self-service" in text
    assert "Owner" in text
```

Append `test_banner_emits_without_error()` to the `__main__` runner.

- [ ] **Step 4: Run both rights-probe tests + manual smoke**:

```
python3 tests/test_rights_probe.py
python3 tests/test_phase_decision.py
python3 scripts/production_ready.py --target-sub <SUB> --target-rg <RG> --no-rights-probe
```

Expected: both test files print `OK`; smoke run prints the banner with `Rights: unknown` and `Phase 2: ⇒ central-team handoff (reason: Rights probe skipped or failed — assuming constrained)`.

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(rights): wire probe+decision banner into main()

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

> 🛟 **Fixture hygiene reminder:** after the smoke run, the fixture manifest will be mutated. `git checkout HEAD -- skills/threadlight-production-ready/references/fixtures/` to revert before the commit.

---
## Phase D — CI/CD scaffold + UAMI/FederatedCredential runbook

**Purpose:** When Phase 3 fires (either because the operator asked for `--scaffold-cicd`, or because the apply-plan contains any `kind: deferred-to-pipeline` items), the agent writes a `.github/workflows/azd-deploy-prod.yml` plus a `README.md` for the central platform team explaining how to provision the UAMI + federated credential that the workflow's `azure/login@v2` step expects.

**Key design constraint** (from approved spec §Phase 3):
- We use **federated credentials**, NEVER long-lived service-principal secrets in GitHub Secrets.
- The runbook is **for the central team**, not for the dev — it lists exact `az identity federated-credential create` invocations the platform team must run.
- The workflow itself is dev-facing — once the central team provisions the FedCred, the dev pushes to `main` and Azure deploys.

**Files:**
- Create: `skills/threadlight-production-ready/references/cicd-templates/azd-deploy-prod.yml.tmpl`
- Create: `skills/threadlight-production-ready/references/cicd-templates/central-team-uami-readme.md.tmpl`
- Create: `skills/threadlight-production-ready/scripts/_scaffold_cicd.py` *(optional; can also live inline in production_ready.py)*
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py` — add `--scaffold-cicd` dispatch + `_render_cicd_template`
- Create: `skills/threadlight-production-ready/tests/test_cicd_scaffold.py`

### Task D1: GitHub Actions workflow template

- [ ] **Step 1: Create `references/cicd-templates/azd-deploy-prod.yml.tmpl`**

```yaml
# Auto-generated by threadlight-production-ready v0.4.0
# Target subscription: {{TARGET_SUBSCRIPTION_ID}}
# Target RG:           {{TARGET_RESOURCE_GROUP}}
# Posture:             {{TARGET_POSTURE}}
# Generated:           {{ISO_TIMESTAMP}}
#
# Prerequisites:
#   1. Central platform team must have provisioned the UAMI named {{UAMI_NAME}}
#      with a federated credential bound to this repo + ref. See
#      docs/threadlight-cicd/central-team-uami-readme.md for the exact commands.
#   2. The four `vars` below must be set as GitHub Actions variables (not secrets;
#      these are not sensitive — they're just identifiers).
name: prod-deploy

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  id-token: write       # required for federated credential exchange
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v4

      - name: Azure login via federated credential
        uses: azure/login@v2
        with:
          client-id: ${{ vars.AZURE_CLIENT_ID }}
          tenant-id: ${{ vars.AZURE_TENANT_ID }}
          subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}

      - name: Install azd
        uses: Azure/setup-azd@v2

      - name: azd provision
        run: azd provision --no-prompt
        env:
          AZURE_ENV_NAME: ${{ vars.AZURE_ENV_NAME }}
          AZURE_LOCATION: {{TARGET_LOCATION}}
          AZURE_SUBSCRIPTION_ID: ${{ vars.AZURE_SUBSCRIPTION_ID }}

      - name: azd deploy
        run: azd deploy --no-prompt
        env:
          AZURE_ENV_NAME: ${{ vars.AZURE_ENV_NAME }}

  smoke-failover:
    needs: deploy
    runs-on: ubuntu-latest
    if: success()
    steps:
      - uses: actions/checkout@v4

      - name: Azure login
        uses: azure/login@v2
        with:
          client-id: ${{ vars.AZURE_CLIENT_ID }}
          tenant-id: ${{ vars.AZURE_TENANT_ID }}
          subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}

      # threadlight REL-102 deferred-to-pipeline marker — this job must exist
      # for REL-102 to flip to `pass`. Customize for your posture.
      - name: Probe secondary region
        run: |
          set -euo pipefail
          # TODO(operator): replace with your secondary-region health endpoint.
          # Example for an APIM-fronted Foundry: curl -fsS https://<apim-host>/health
          echo "smoke-failover stub — replace with real secondary-region probe"

  threadlight-postdeploy:
    needs: deploy
    runs-on: ubuntu-latest
    if: success()
    steps:
      - uses: actions/checkout@v4

      - name: Azure login
        uses: azure/login@v2
        with:
          client-id: ${{ vars.AZURE_CLIENT_ID }}
          tenant-id: ${{ vars.AZURE_TENANT_ID }}
          subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}

      - name: Re-assess production readiness
        run: |
          python3 skills/threadlight-production-ready/scripts/production_ready.py \
            --target-sub ${{ vars.AZURE_SUBSCRIPTION_ID }} \
            --target-rg  {{TARGET_RESOURCE_GROUP}} \
            --no-rights-probe

      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: threadlight-postdeploy-${{ github.run_number }}
          path: |
            skills/threadlight-production-ready/production-readiness-*.json
            skills/threadlight-production-ready/production-readiness-*.md
```

- [ ] **Step 2: Commit**

```bash
git add skills/threadlight-production-ready/references/cicd-templates/azd-deploy-prod.yml.tmpl
git commit -m "feat(cicd): azd-deploy-prod.yml.tmpl with FedCred + post-deploy threadlight

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task D2: Central-team UAMI runbook template

- [ ] **Step 1: Create `references/cicd-templates/central-team-uami-readme.md.tmpl`**

```markdown
# Central-team UAMI provisioning for `{{REPO_FULL_NAME}}`

**Audience:** central platform team (the people who own the target subscription).
**Generated by:** threadlight-production-ready v0.4.0
**Purpose:** establish a federated-credential trust between this GitHub repo and a
  user-assigned managed identity (UAMI), so the repo's `.github/workflows/azd-deploy-prod.yml`
  can deploy to `{{TARGET_SUBSCRIPTION_ID}}/{{TARGET_RESOURCE_GROUP}}` **without** any
  long-lived secrets stored in GitHub.

## What to run

```bash
# 1. Create the UAMI (idempotent — safe to re-run)
az identity create \
  --name {{UAMI_NAME}} \
  --resource-group {{UAMI_RESOURCE_GROUP}} \
  --location {{TARGET_LOCATION}} \
  --subscription {{UAMI_SUBSCRIPTION_ID}}

# 2. Capture its principalId + clientId
PRINCIPAL_ID=$(az identity show -n {{UAMI_NAME}} -g {{UAMI_RESOURCE_GROUP}} --query principalId -o tsv)
CLIENT_ID=$(az identity show -n {{UAMI_NAME}} -g {{UAMI_RESOURCE_GROUP}} --query clientId -o tsv)

# 3. Grant the UAMI Contributor on the target RG (least privilege for azd provision/deploy)
az role assignment create \
  --assignee-object-id $PRINCIPAL_ID \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope /subscriptions/{{TARGET_SUBSCRIPTION_ID}}/resourceGroups/{{TARGET_RESOURCE_GROUP}}

# 4. Federated credential — trust GitHub OIDC for pushes to main on this repo
az identity federated-credential create \
  --name github-{{REPO_SLUG}}-main \
  --identity-name {{UAMI_NAME}} \
  --resource-group {{UAMI_RESOURCE_GROUP}} \
  --issuer https://token.actions.githubusercontent.com \
  --subject repo:{{REPO_FULL_NAME}}:ref:refs/heads/main \
  --audience api://AzureADTokenExchange

# 5. (Optional) Federated credential for the production environment
az identity federated-credential create \
  --name github-{{REPO_SLUG}}-env-production \
  --identity-name {{UAMI_NAME}} \
  --resource-group {{UAMI_RESOURCE_GROUP}} \
  --issuer https://token.actions.githubusercontent.com \
  --subject repo:{{REPO_FULL_NAME}}:environment:production \
  --audience api://AzureADTokenExchange
```

## What to hand back to the dev team

After running the above, hand the dev team these three values (NOT secrets — they
are identifiers; set them as GitHub Actions **variables**, not secrets):

| GitHub variable name | Value |
| --- | --- |
| `AZURE_CLIENT_ID` | `$CLIENT_ID` from step 2 |
| `AZURE_TENANT_ID` | `{{AZURE_TENANT_ID}}` |
| `AZURE_SUBSCRIPTION_ID` | `{{TARGET_SUBSCRIPTION_ID}}` |
| `AZURE_ENV_NAME` | `{{AZURE_ENV_NAME}}` (free-form azd env identifier; e.g. `prod`) |

## Why federated credentials, not client secrets

- No secrets to rotate.
- No secrets to leak.
- Trust is bound to **this repo + this ref/environment** — even if the UAMI's client-id
  leaks, it cannot be used from elsewhere.
- Microsoft's current Azure-for-GitHub recommended pattern.

## When to revoke

If the dev team's repo is archived, moved, or compromised, run:

```bash
az identity federated-credential delete \
  --name github-{{REPO_SLUG}}-main \
  --identity-name {{UAMI_NAME}} \
  --resource-group {{UAMI_RESOURCE_GROUP}}
```

This severs the trust immediately. The UAMI itself can be retained for future use.
```

- [ ] **Step 2: Commit**

```bash
git add skills/threadlight-production-ready/references/cicd-templates/central-team-uami-readme.md.tmpl
git commit -m "docs(cicd): central-team UAMI+FedCred runbook template

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task D3: TDD — `_render_template(template_path, context) -> str`

Pure-string rendering; no Jinja, no extra deps (stdlib commitment).

- [ ] **Step 1: Write failing test**

`tests/test_cicd_scaffold.py`:

```python
import importlib.util, pathlib, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("production_ready", ROOT / "scripts" / "production_ready.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def test_render_replaces_all_tokens():
    with tempfile.NamedTemporaryFile("w", suffix=".tmpl", delete=False) as f:
        f.write("hello {{NAME}} from {{PLACE}}")
        p = pathlib.Path(f.name)
    out = mod._render_template(p, {"NAME": "Threadlight", "PLACE": "Azure"})
    assert out == "hello Threadlight from Azure"

def test_render_leaves_unknown_token_visible():
    with tempfile.NamedTemporaryFile("w", suffix=".tmpl", delete=False) as f:
        f.write("hi {{NAME}} {{UNKNOWN}}")
        p = pathlib.Path(f.name)
    out = mod._render_template(p, {"NAME": "x"})
    assert "{{UNKNOWN}}" in out  # leaves it visible so the operator can spot gaps

def test_render_builds_context_from_framing():
    framing = {"target_subscription_id": "sub", "target_resource_group": "rg",
               "target_posture": "agt", "central_platform_team": "platform-prod"}
    ctx = mod._cicd_context_from_framing(framing, repo_full_name="aiappsgbb/threadlight-skills")
    assert ctx["TARGET_SUBSCRIPTION_ID"] == "sub"
    assert ctx["REPO_FULL_NAME"] == "aiappsgbb/threadlight-skills"
    assert ctx["REPO_SLUG"] == "aiappsgbb-threadlight-skills"
    assert ctx["UAMI_NAME"].startswith("uami-")

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"): fn()
    print("OK")
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement in `production_ready.py`**

```python
def _render_template(path, context):
    text = pathlib.Path(path).read_text()
    for k, v in context.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text

def _cicd_context_from_framing(framing, repo_full_name, env_name="prod"):
    import datetime
    sub = framing.get("target_subscription_id", "")
    rg  = framing.get("target_resource_group", "")
    slug = repo_full_name.replace("/", "-")
    return {
        "TARGET_SUBSCRIPTION_ID": sub,
        "TARGET_RESOURCE_GROUP": rg,
        "TARGET_LOCATION": framing.get("target_location", "eastus"),
        "TARGET_POSTURE": framing.get("target_posture", ""),
        "REPO_FULL_NAME": repo_full_name,
        "REPO_SLUG": slug,
        "UAMI_NAME": f"uami-{slug}-prod-deploy",
        "UAMI_RESOURCE_GROUP": framing.get("central_platform_team_rg", rg),
        "UAMI_SUBSCRIPTION_ID": framing.get("central_platform_team_sub", sub),
        "AZURE_TENANT_ID": framing.get("azure_tenant_id", "<tenant-id>"),
        "AZURE_ENV_NAME": env_name,
        "ISO_TIMESTAMP": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
```

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/scripts/production_ready.py skills/threadlight-production-ready/tests/test_cicd_scaffold.py
git commit -m "feat(cicd): template renderer + framing→context builder

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task D4: TDD — `_scaffold_cicd(framing, repo_full_name, out_dir) -> list[Path]`

- [ ] **Step 1: Add test**

```python
def test_scaffold_writes_both_files(tmp_path=None):
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp())
    framing = {"target_subscription_id": "sub", "target_resource_group": "rg",
               "target_posture": "agt"}
    written = mod._scaffold_cicd(framing, "aiappsgbb/threadlight-skills", out_root=tmp)
    paths = [str(p.relative_to(tmp)) for p in written]
    assert ".github/workflows/azd-deploy-prod.yml" in paths
    assert "docs/threadlight-cicd/central-team-uami-readme.md" in paths
    # workflow has no unresolved tokens
    wf = (tmp / ".github/workflows/azd-deploy-prod.yml").read_text()
    assert "{{TARGET_SUBSCRIPTION_ID}}" not in wf
    assert "sub" in wf
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement**

```python
def _scaffold_cicd(framing, repo_full_name, out_root):
    """Render both CI/CD templates into out_root and return the list of written paths."""
    out_root = pathlib.Path(out_root)
    tmpl_dir = pathlib.Path(__file__).resolve().parent.parent / "references" / "cicd-templates"
    ctx = _cicd_context_from_framing(framing, repo_full_name)

    pairs = [
        (tmpl_dir / "azd-deploy-prod.yml.tmpl",         out_root / ".github" / "workflows" / "azd-deploy-prod.yml"),
        (tmpl_dir / "central-team-uami-readme.md.tmpl", out_root / "docs" / "threadlight-cicd" / "central-team-uami-readme.md"),
    ]
    written = []
    for tmpl, dest in pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_render_template(tmpl, ctx))
        written.append(dest)
    return written
```

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(cicd): _scaffold_cicd writer for workflow + UAMI readme

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task D5: Wire `--scaffold-cicd` into argparse + main()

- [ ] **Step 1: Add to `_parse_args`** (L3672-3712 region):

```python
ap.add_argument("--scaffold-cicd", action="store_true",
                help="After assessment, write .github/workflows/azd-deploy-prod.yml + "
                     "docs/threadlight-cicd/central-team-uami-readme.md from templates.")
ap.add_argument("--repo-full-name", default=None,
                help="owner/repo string for CI/CD scaffolds. Auto-detected from git "
                     "remote origin if omitted.")
```

- [ ] **Step 2: In `main()`, after the manifest is written, conditionally invoke:**

```python
if args.scaffold_cicd:
    repo = args.repo_full_name or _detect_repo_full_name(os.getcwd())
    if not repo:
        _log_warn("Could not detect repo owner/repo; pass --repo-full-name. Skipping CI/CD scaffold.")
    else:
        written = _scaffold_cicd(framing, repo, out_root=os.getcwd())
        for p in written:
            _log_info(f"wrote {p}")
```

- [ ] **Step 3: Implement `_detect_repo_full_name`**:

```python
def _detect_repo_full_name(cwd):
    """Parse `git remote get-url origin`. Return 'owner/repo' or None."""
    try:
        r = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    # handles both git@github.com:owner/repo.git and https://github.com/owner/repo(.git)
    import re
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None
```

- [ ] **Step 4: Add an integration test:**

```python
def test_scaffold_via_cli_flag_e2e(tmp_path=None):
    import subprocess, tempfile
    tmp = pathlib.Path(tempfile.mkdtemp())
    framing_file = tmp / "framing.json"
    framing_file.write_text('''{
      "target_subscription_id": "sub", "target_resource_group": "rg",
      "target_posture": "agt", "provisioning_rights": "full",
      "central_platform_team": null, "restricted_environment": false,
      "cicd_target": "github-actions"
    }''')
    script = ROOT / "scripts" / "production_ready.py"
    r = subprocess.run(["python3", str(script),
                        "--framing-file", str(framing_file),
                        "--scaffold-cicd",
                        "--repo-full-name", "aiappsgbb/threadlight-skills",
                        "--no-rights-probe",
                        "--apply-plan-out", str(tmp / "apply-plan.json")],
                       capture_output=True, text=True, cwd=str(tmp))
    assert r.returncode == 0, r.stderr
    assert (tmp / ".github/workflows/azd-deploy-prod.yml").exists()
    assert (tmp / "docs/threadlight-cicd/central-team-uami-readme.md").exists()
```

- [ ] **Step 5: Run all D-phase tests:**

```
python3 tests/test_cicd_scaffold.py
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(cicd): wire --scaffold-cicd CLI flag end-to-end

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task D6: Auto-scaffold when apply-plan has deferred-to-pipeline items

- [ ] **Step 1: Add test**

```python
def test_scaffold_auto_fires_when_pipeline_items_present(tmp_path=None):
    """If --scaffold-cicd is NOT passed but the apply-plan contains any
    kind=deferred-to-pipeline item, the assessor should hint the operator
    (via stderr log) that scaffolding is needed."""
    # easiest: capture stderr from a smoke run against the broken fixture and grep for the hint
    ...
```

(Full test in the executor commit; spec'd here as a stub.)

- [ ] **Step 2: In `main()`, after `apply_plan` is built, log a hint:**

```python
if any(item.get("kind") == "deferred-to-pipeline" for item in apply_plan["items"]) and not args.scaffold_cicd:
    _log_info(f"apply-plan contains {sum(1 for i in apply_plan['items'] if i['kind']=='deferred-to-pipeline')} "
              f"deferred-to-pipeline items. Re-run with --scaffold-cicd to generate the GitHub Actions workflow.")
```

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(cicd): hint operator when deferred-to-pipeline items need scaffolding

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---
## Phase E — SKILL.md rewrite + VERSION bump

**Purpose:** v0.3.0's SKILL.md said the skill "recommends, never executes." v0.4.0 reverses that contract. The README-facing surface must accurately describe the 3-phase production-onboarding flow, the apply-plan, and the agent's role as executor.

**Files:**
- Modify: `skills/threadlight-production-ready/SKILL.md`
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py:44` — `VERSION = "0.3.0"` → `VERSION = "0.4.0"`
- Modify: any test that asserts on the version string

### Task E1: VERSION bump + test

- [ ] **Step 1: Find every place that hard-codes "0.3.0"**

```
grep -rn "0\.3\.0" skills/threadlight-production-ready/
```

Expected hits (approximate): `scripts/production_ready.py:44`, `SKILL.md` metadata block (L25), possibly fixture goldens. Inspect each one — fixture goldens may be intentional (frozen snapshot of what v0.3.0 produced), in which case leave them alone but plan to refresh in Module G.

- [ ] **Step 2: Bump `VERSION = "0.3.0"` → `VERSION = "0.4.0"`**

- [ ] **Step 3: Add a version-pin test** in `tests/test_version.py` (new file):

```python
import importlib.util, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("production_ready", ROOT / "scripts" / "production_ready.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def test_version_is_040():
    assert mod.VERSION == "0.4.0"

def test_version_matches_skill_md():
    skill_md = (ROOT / "SKILL.md").read_text()
    assert "version: 0.4.0" in skill_md

if __name__ == "__main__":
    test_version_is_040(); test_version_matches_skill_md()
    print("OK")
```

- [ ] **Step 4: Run → currently SKILL.md still says 0.3.0 → FAIL on second test**

- [ ] **Step 5: Update `SKILL.md` metadata** (L25 region):

```yaml
version: 0.4.0
```

- [ ] **Step 6: Run → PASS**

- [ ] **Step 7: Commit**

```bash
git add skills/threadlight-production-ready/scripts/production_ready.py \
        skills/threadlight-production-ready/SKILL.md \
        skills/threadlight-production-ready/tests/test_version.py
git commit -m "chore(version): bump to 0.4.0 + pin test

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task E2: Flip the "recommends, never executes" contract

`SKILL.md:81` currently reads (approximately): *"This skill recommends remediations; it never executes them. All edits to your repo or your Azure subscription must be performed by you or a separate tool."*

- [ ] **Step 1: Replace that paragraph with:**

```markdown
## What this skill does in v0.4.0

This skill drives **production onboarding** in three phases:

1. **Assess.** A Python script (`scripts/production_ready.py`) inventories your
   target Azure subscription/resource group, scores it against 151 findings
   spanning 13 production-readiness pillars, and emits an `apply-plan.json`
   that names every must-fix gap and the remediation recipe that closes it.

2. **Refine + Deploy.** The Copilot agent reads `apply-plan.json`, opens the
   recipes named there, and **executes** each fix using its native Edit/Write
   tools (for `kind: repo-edit`) or by invoking sibling awesome-gbb skills
   (for `kind: sibling-skill`). Items marked `kind: manual` are surfaced to
   you for explicit acknowledgement before any change. Items marked
   `kind: deferred-to-pipeline` are recorded for Phase 3.

3. **CI/CD Handoff.** When the apply-plan contains pipeline-deferred items
   (or you pass `--scaffold-cicd`), the script renders a GitHub Actions
   workflow (`.github/workflows/azd-deploy-prod.yml`) and a central-platform-
   team runbook (`docs/threadlight-cicd/central-team-uami-readme.md`)
   explaining exactly which UAMI + federated credential to provision so
   future pushes to `main` deploy without long-lived secrets.

**The Python script is still assessor-only.** It never mutates your repo
and never mutates your subscription. All edits flow through the Copilot
agent's tools, which means `git diff` and PR review remain the audit
trail. The script does not have, and will not be given, a `--apply` flag.
```

- [ ] **Step 2: Add an explicit "phases overview" diagram** somewhere near the top:

````markdown
```mermaid
flowchart LR
    A[Phase 1: Assess<br/>production_ready.py] -->|apply-plan.json| B[Phase 2: Refine+Deploy<br/>agent + Edit/Write + sibling skills]
    B -->|repo commits<br/>+ pipeline deferrals| C[Phase 3: CI/CD Handoff<br/>azd-deploy-prod.yml<br/>+ UAMI readme]
    C -.->|post-deploy| A
```
````

- [ ] **Step 3: Update the "How to invoke" section** to include the new flags:

```markdown
## How to invoke

### Quick assess (no changes)
```
python3 scripts/production_ready.py \
  --target-sub <SUB> --target-rg <RG>
```

### Full onboarding (interactive framing wizard)
```
python3 scripts/production_ready.py --onboard
```

### Headless / CI-friendly
```
python3 scripts/production_ready.py \
  --framing-file framing.json \
  --apply-plan-out apply-plan.json \
  --no-rights-probe
```

### Phase 3 scaffold only
```
python3 scripts/production_ready.py \
  --framing-file framing.json \
  --scaffold-cicd \
  --repo-full-name owner/repo
```
```

- [ ] **Step 4: Add a "Recipe catalog" section** pointing at `references/remediation-recipes/`:

```markdown
## Remediation recipes

Every must-fix finding has a recipe at `references/remediation-recipes/{FINDING_ID}.md`.
Recipes declare `kind: repo-edit | sibling-skill | manual | deferred-to-pipeline`
in their YAML front-matter; the apply-plan inherits this `kind` field so the
agent knows whether to edit a file, invoke a sibling skill, or surface a
prompt to the operator. See `references/remediation-recipes/_template.md`
for the schema, and `references/sibling-skills-map.md` for the awesome-gbb
skills threadlight delegates to.
```

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/SKILL.md
git commit -m "docs(skill): flip 'recommends, never executes' → 3-phase onboarding

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task E3: Add a "What changed since v0.3.0" digest section

This makes the SKILL.md self-explanatory to anyone landing on the skill from a stale link.

- [ ] **Step 1: Add near the bottom**, before the `## See also`:

```markdown
## What changed since v0.3.0

| Capability | v0.3.0 | v0.4.0 |
| --- | --- | --- |
| Assessment | ✅ 127/151 findings, 4 postures, RBAC probe | ✅ unchanged (still assessor-only) |
| Apply-plan output | ❌ — only scorecard + report | ✅ `apply-plan.json` with 4 `kind` types |
| Remediation recipes | ❌ — advice in pillar refs | ✅ 61 must-fix recipes (`references/remediation-recipes/`) |
| Phase 2 execution | ❌ — manual | ✅ agent-driven via Edit/Write + sibling skills |
| Phase 3 CI/CD scaffold | ❌ | ✅ `.github/workflows/azd-deploy-prod.yml.tmpl` + UAMI runbook |
| Rights probe | ✅ informational | ✅ now decides Phase-2 mode (self-service vs handoff) |
| Framing wizard | ❌ | ✅ 7 questions, TTY or `--framing-file` |
| New CLI flags | — | `--onboard`, `--framing-file`, `--apply-plan-out`, `--scaffold-cicd`, `--no-rights-probe`, `--repo-full-name` |
| Removed / explicitly NOT added | — | `--apply FINDING_ID` (script stays assessor-only by design) |
```

- [ ] **Step 2: Commit**

```bash
git commit -am "docs(skill): add v0.3.0→v0.4.0 capability diff

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task E4: Cross-check pillar references

The 13 `references/pillars/{01..13}-*.md` files were accurate as of v0.3.0. v0.4.0 doesn't change finding behavior, so no edits are needed — but each pillar reference should get a one-line note saying "see `references/remediation-recipes/{ID}.md` for fix steps" so a reader hitting a pillar reference can navigate to the recipe.

- [ ] **Step 1: For each `references/pillars/{N}-{pillar}.md`, append at the end:**

```markdown
---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.
```

- [ ] **Step 2: Commit all 13 in one go**

```bash
git add skills/threadlight-production-ready/references/pillars/*.md
git commit -m "docs(pillars): add recipe-catalog cross-references

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task E5: Run all stdlib tests to confirm no regression

- [ ] **Step 1:**

```
cd skills/threadlight-production-ready
for f in tests/test_*.py; do python3 "$f" || { echo "FAIL: $f"; exit 1; }; done
echo "ALL TESTS PASS"
```

- [ ] **Step 2: If anything fails, fix and recommit. Otherwise:**

```bash
git commit --allow-empty -m "test: all stdlib tests green at end of Phase E

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Phase F — Sibling-skill invocation map

**Purpose:** Document which awesome-gbb skill handles which threadlight finding when the recipe has `kind: sibling-skill`. This is a single markdown file the agent reads when it sees `kind: sibling-skill` in an apply-plan item — it tells the agent the exact `Skill tool` invocation contract.

**Files:**
- Create: `skills/threadlight-production-ready/references/sibling-skills-map.md`
- Create: `skills/threadlight-production-ready/tests/test_sibling_skill_map.py`

### Task F1: Author `references/sibling-skills-map.md`

- [ ] **Step 1: Create the file** with a table mapping finding IDs to sibling skills and required inputs:

```markdown
# Sibling-skill invocation map

When threadlight's apply-plan emits `kind: sibling-skill` for a finding, the
agent should consult this table to learn (a) which awesome-gbb skill to invoke,
(b) the input contract that skill expects, and (c) which threadlight finding
the invocation is expected to flip green.

## Currently mapped (v0.4.0)

| Finding ID | Sibling skill (awesome-gbb) | Input contract | Notes |
| --- | --- | --- | --- |
| NET-501 | `citadel-spoke-onboarding` | `spoke_subscription_id`, `spoke_resource_group`, `hub_subscription_id`, `hub_apim_resource_id`, `access_contract_product` | Hub coordinates may need user confirmation if not in framing. |
| NET-502 | `citadel-spoke-onboarding` | (same as NET-501) | Same skill handles reachability — single invocation closes both. |
| IAM-101 | `foundry-rbac-audit` *(planned — awesome-gbb#268)* | `subscription_id`, `resource_group`, `target_principal_types` | Skill not yet released; until then this finding stays `kind: manual`. |
| MDL-010 | `foundry-iq` | `subscription_id`, `resource_group`, `private_endpoint_required: true` | Existing skill — confirm version ≥ 0.3.0. |
| MDL-011 | `foundry-hosted-agents` | `subscription_id`, `resource_group`, `retention_days` (from framing) | Thread retention policy. |
| OBS-106 | `azure-resource-diagnostics` *(planned — awesome-gbb#271)* | `subscription_id`, `resource_group`, `target_resource_types` | Not yet released — fall back to `kind: manual`. |
| REL-007 | `azure-backup-readiness` *(planned — awesome-gbb#267)* | `subscription_id`, `resource_group`, `protected_item_types` | Not yet released — fall back to `kind: manual`. |
| SRE-104 | `azure-monitor-alert-baseline` *(planned — awesome-gbb#272)* | `subscription_id`, `resource_group`, `alert_baseline_kind` | Not yet released — fall back to `kind: manual`. |

## Planned but not implemented (placeholders — recipes are `kind: manual` until skill ships)

The five rows above marked *(planned)* reference awesome-gbb upstream issues
#267-272. Until those skills ship, the corresponding recipes MUST declare
`kind: manual` and explain the manual procedure. When a sibling skill ships:

1. Update its recipe's front-matter from `kind: manual` to `kind: sibling-skill`.
2. Add `sibling_skill: <skill-name>` to the front-matter.
3. Update this table to remove the *(planned)* tag.
4. Add a row to the v0.4.x changelog.

## Invocation contract for the agent

When the agent sees `kind: sibling-skill` in `apply-plan.json`:

1. Open the recipe markdown at `references/remediation-recipes/{ID}.md`.
2. Read the `## Edit recipe` section — it contains a JSON block with the
   exact inputs to pass.
3. Substitute placeholders that reference `framing.*` from the framing file
   that produced the apply-plan (path is recorded in `apply-plan.json` under
   `framing_path`).
4. Invoke the sibling skill via the Skill tool.
5. After the sibling skill completes, re-run threadlight (assessor-only,
   no `--scaffold-cicd`) to confirm the finding flipped to `pass`.
6. If still `fail`, do NOT loop — report the partial state to the operator.

## When a sibling skill is missing or fails

- **Skill not installed:** the agent surfaces a prompt to the operator asking
  whether to install it (links to its awesome-gbb tile) or fall back to the
  recipe's manual instructions.
- **Skill fails mid-run:** the agent marks the apply-plan item as
  `status: sibling-skill-failed`, records the error, and continues with the
  rest of the apply-plan. No automatic retry.
```

- [ ] **Step 2: Commit**

```bash
git add skills/threadlight-production-ready/references/sibling-skills-map.md
git commit -m "docs(siblings): sibling-skill invocation map (NET-501/502/etc.)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task F2: Test — every `kind: sibling-skill` recipe is mapped here

- [ ] **Step 1: Write `tests/test_sibling_skill_map.py`**

```python
import pathlib, re

ROOT  = pathlib.Path(__file__).resolve().parents[1]
RDIR  = ROOT / "references" / "remediation-recipes"
MAP   = ROOT / "references" / "sibling-skills-map.md"

def _sibling_skill_recipes():
    out = []
    for path in RDIR.glob("*.md"):
        if path.name.startswith("_"): continue
        text = path.read_text()
        # crude YAML front-matter scan; avoids the yaml dep
        m = re.search(r"^---\n(.*?)\n---", text, re.DOTALL)
        if not m: continue
        front = m.group(1)
        if re.search(r"^kind:\s*sibling-skill\s*$", front, re.MULTILINE):
            out.append(path.stem)
    return sorted(out)

def test_every_sibling_recipe_is_in_map():
    if not MAP.exists():
        raise AssertionError("sibling-skills-map.md not found")
    text = MAP.read_text()
    missing = [fid for fid in _sibling_skill_recipes() if fid not in text]
    assert not missing, f"sibling-skill recipes not listed in map: {missing}"

if __name__ == "__main__":
    test_every_sibling_recipe_is_in_map()
    print("OK")
```

- [ ] **Step 2: Run → PASS** (since we only have NET-501/502 declared as `sibling-skill` in v0.4.0 and they're both in the map)

- [ ] **Step 3: Commit**

```bash
git add skills/threadlight-production-ready/tests/test_sibling_skill_map.py
git commit -m "test(siblings): every sibling-skill recipe must appear in the map

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task F3: Reference the map from `SKILL.md`

- [ ] **Step 1: In the "Recipe catalog" section of `SKILL.md` (added in Task E2 Step 4), confirm the line "See ... `references/sibling-skills-map.md`" is present.** Already added in E2 — verify only; commit if missed.

---
## Phase G — Field test, restricted fixture, end-to-end test, CHANGELOG

**Purpose:** Validate v0.4.0 against a synthetic *restricted-environment* fixture (mirrors a real central-team-owned subscription where the dev has no write access), wire an end-to-end test that exercises the full Assess → apply-plan → CI/CD scaffold path, and ship CHANGELOG + version bump.

**Files:**
- Create: `skills/threadlight-production-ready/references/fixtures/sample-pilot-restricted/*` (mirror of `sample-pilot`)
- Create: `skills/threadlight-production-ready/tests/test_end_to_end.py`
- Modify: `CHANGELOG.md` — add v0.4.0 entry at top

### Task G1: Create `sample-pilot-restricted` fixture

- [ ] **Step 1: Copy `sample-pilot` as the base**

```bash
cp -r skills/threadlight-production-ready/references/fixtures/sample-pilot \
      skills/threadlight-production-ready/references/fixtures/sample-pilot-restricted
```

- [ ] **Step 2: Add a `framing.json` that models a restricted environment** (this is what the wizard would have produced):

`references/fixtures/sample-pilot-restricted/framing.json`:

```json
{
  "target_subscription_id": "00000000-0000-0000-0000-000000000999",
  "target_resource_group": "rg-pilot-restricted",
  "target_posture": "standard-ai-gateway",
  "provisioning_rights": "constrained",
  "central_platform_team": "platform-prod-westeurope",
  "restricted_environment": true,
  "cicd_target": "github-actions"
}
```

- [ ] **Step 3: Pre-record the rights-probe result** in a `rights-probe-fixture.json` so tests can run hermetically without invoking `az`:

`references/fixtures/sample-pilot-restricted/rights-probe-fixture.json`:

```json
{
  "rights_class": "constrained",
  "roles": ["Reader"],
  "probe_skipped": false,
  "error": null
}
```

- [ ] **Step 4: Record the expected phase decision** as a golden file:

`references/fixtures/sample-pilot-restricted/phase-decision.golden.json`:

```json
{
  "phase2_mode": "central-team handoff",
  "reason": "Framing-Q6 marked restricted environment — handoff regardless of rights",
  "warning": null
}
```

> Note: `restricted_environment: true` short-circuits before the rights probe even matters; if Q6 were false, the constrained-rights path would have produced an equivalent handoff decision with a different `reason`.

- [ ] **Step 5: Commit the fixture skeleton**

```bash
git add skills/threadlight-production-ready/references/fixtures/sample-pilot-restricted/
git commit -m "feat(fixtures): add sample-pilot-restricted with framing + rights goldens

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task G2: Smoke-run threadlight against the restricted fixture; freeze its outputs

- [ ] **Step 1: Run the assessor in --framing-file mode against the fixture**

```bash
cd skills/threadlight-production-ready/references/fixtures/sample-pilot-restricted
python3 ../../../scripts/production_ready.py \
  --framing-file ./framing.json \
  --apply-plan-out ./apply-plan.golden.json \
  --no-rights-probe \
  --skip-live
```

`--skip-live` already exists in v0.3.0 for static-only runs.

- [ ] **Step 2: Override the recorded rights-probe result** (the fixture's `rights-probe-fixture.json` is what the test will inject, since `--no-rights-probe` gave us `unknown` not `constrained`):

There is intentionally no CLI flag for "load this fake rights result" — the test harness in G4 monkey-patches `_probe_provisioning_rights` to return the fixture file's contents.

- [ ] **Step 3: Verify the smoke run produced sensible outputs**

```bash
ls -la skills/threadlight-production-ready/references/fixtures/sample-pilot-restricted/
# expect: framing.json, apply-plan.golden.json, rights-probe-fixture.json,
#         phase-decision.golden.json, production-readiness-manifest.json,
#         production-readiness-report.md, production-readiness-trend.csv
```

- [ ] **Step 4: Inspect `apply-plan.golden.json`** — it should contain only `kind: manual` and `kind: deferred-to-pipeline` items (since the operator has no write rights, no `repo-edit` items should be planned for automatic execution). Edit by hand if the assessor produced any `kind: repo-edit` items — the recipe-loader should respect the phase decision; if it doesn't, this is a bug to fix in Phase C as a follow-up commit.

- [ ] **Step 5: Commit the golden outputs**

```bash
git add skills/threadlight-production-ready/references/fixtures/sample-pilot-restricted/
git commit -m "feat(fixtures): freeze sample-pilot-restricted assessor outputs as goldens

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task G3: Cross-fixture regression check — sample-pilot and sample-pilot-citadel still pass

- [ ] **Step 1: Run the existing two fixtures**

```bash
for f in sample-pilot sample-pilot-citadel sample-pilot-broken; do
  echo "=== $f ==="
  cd skills/threadlight-production-ready/references/fixtures/$f
  python3 ../../../scripts/production_ready.py --skip-live --no-rights-probe
  cd - >/dev/null
done
```

- [ ] **Step 2: `git diff` the fixture directories — there should be NO functional changes**

Expected diff scope: only `production-readiness-manifest.json` got a new top-level `"version": "0.4.0"` field (and possibly new `"phase_decision"` / `"rights_probe"` keys). If the diff shows changes to finding outcomes (pass/fail counts), that's a regression — investigate before continuing.

- [ ] **Step 3: Refresh the goldens** if (and only if) the diff is purely additive:

```bash
git add skills/threadlight-production-ready/references/fixtures/sample-pilot/ \
        skills/threadlight-production-ready/references/fixtures/sample-pilot-citadel/ \
        skills/threadlight-production-ready/references/fixtures/sample-pilot-broken/
git commit -m "chore(fixtures): refresh existing fixture goldens with v0.4.0 manifest fields

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task G4: End-to-end test

- [ ] **Step 1: Create `tests/test_end_to_end.py`**

```python
"""End-to-end smoke: framing→assess→apply-plan→CI/CD scaffold.

Runs the script as a subprocess, asserts on the artifacts it produces.
Uses the sample-pilot-restricted fixture so the test exercises the
central-team-handoff path (the harder code path).
"""
import json, pathlib, subprocess, tempfile, shutil

ROOT   = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_ready.py"
FIXT   = ROOT / "references" / "fixtures" / "sample-pilot-restricted"

def _setup_workdir():
    tmp = pathlib.Path(tempfile.mkdtemp())
    # Copy fixture content (including framing.json) into the workdir
    for child in FIXT.iterdir():
        if child.is_file():
            shutil.copy(child, tmp / child.name)
        else:
            shutil.copytree(child, tmp / child.name)
    return tmp

def test_e2e_handoff_path_produces_scaffold_and_apply_plan():
    tmp = _setup_workdir()
    apply_plan = tmp / "apply-plan.json"
    r = subprocess.run([
        "python3", str(SCRIPT),
        "--framing-file",   str(tmp / "framing.json"),
        "--apply-plan-out", str(apply_plan),
        "--scaffold-cicd",
        "--repo-full-name", "aiappsgbb/threadlight-skills",
        "--no-rights-probe",
        "--skip-live",
    ], capture_output=True, text=True, cwd=str(tmp), timeout=120)

    assert r.returncode == 0, f"script failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"

    # 1. apply-plan exists and has the expected schema
    assert apply_plan.exists(), "apply-plan.json not written"
    plan = json.loads(apply_plan.read_text())
    assert "items" in plan
    assert "manifest_sha256" in plan
    assert "framing_path" in plan
    kinds = {i["kind"] for i in plan["items"]}
    # In a restricted environment, no repo-edit items should be planned for auto-execution
    assert "repo-edit" not in kinds, f"unexpected repo-edit items in restricted plan: {kinds}"

    # 2. CI/CD scaffold exists
    wf = tmp / ".github" / "workflows" / "azd-deploy-prod.yml"
    rb = tmp / "docs" / "threadlight-cicd" / "central-team-uami-readme.md"
    assert wf.exists(), "workflow scaffold not written"
    assert rb.exists(), "UAMI readme not written"
    assert "{{TARGET_SUBSCRIPTION_ID}}" not in wf.read_text(), "unresolved token in workflow"
    assert "{{REPO_FULL_NAME}}" not in rb.read_text(), "unresolved token in readme"

    # 3. Phase banner appeared on stderr
    assert "central-team handoff" in r.stderr, f"phase banner missing from stderr:\n{r.stderr}"

    # 4. Manifest got version and phase_decision
    manifest = json.loads((tmp / "production-readiness-manifest.json").read_text())
    assert manifest["version"] == "0.4.0"
    assert manifest["phase_decision"]["phase2_mode"] == "central-team handoff"

def test_e2e_apply_plan_matches_golden_kinds():
    """The set of `kind` values in apply-plan.json for the restricted fixture
    is stable across runs (deterministic ordering check)."""
    tmp = _setup_workdir()
    apply_plan = tmp / "apply-plan.json"
    subprocess.run([
        "python3", str(SCRIPT),
        "--framing-file", str(tmp / "framing.json"),
        "--apply-plan-out", str(apply_plan),
        "--no-rights-probe", "--skip-live",
    ], check=True, cwd=str(tmp), timeout=120)
    plan = json.loads(apply_plan.read_text())
    actual_kinds = sorted({i["kind"] for i in plan["items"]})
    expected_kinds = ["deferred-to-pipeline", "manual", "sibling-skill"]
    assert actual_kinds == expected_kinds, f"kinds drifted: {actual_kinds}"

if __name__ == "__main__":
    test_e2e_handoff_path_produces_scaffold_and_apply_plan()
    test_e2e_apply_plan_matches_golden_kinds()
    print("OK")
```

- [ ] **Step 2: Run the test**

```
python3 skills/threadlight-production-ready/tests/test_end_to_end.py
```

Expected: `OK`. If anything fails, the failure message tells you exactly which assertion broke — fix the assessor or the fixture and re-run.

- [ ] **Step 3: Commit**

```bash
git add skills/threadlight-production-ready/tests/test_end_to_end.py
git commit -m "test(e2e): framing→assess→apply-plan→scaffold against restricted fixture

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task G5: Full test-suite green check (one final time)

- [ ] **Step 1:**

```bash
cd skills/threadlight-production-ready
RESULT=0
for f in tests/test_*.py; do
  if ! python3 "$f"; then
    echo "FAIL: $f"; RESULT=1
  fi
done
echo
echo "Test files: $(ls tests/test_*.py | wc -l)"
[ $RESULT -eq 0 ] && echo "ALL GREEN" || { echo "REGRESSIONS"; exit 1; }
```

Expected: ALL GREEN. v0.3.0 had 10 test files, 61 functions. v0.4.0 adds 6-7 new test files:
- `test_framing_wizard.py` (Phase A)
- `test_apply_plan_schema.py` (Phase A)
- `test_recipe_catalog.py` (Phase B)
- `test_rights_probe.py` (Phase C)
- `test_phase_decision.py` (Phase C)
- `test_cicd_scaffold.py` (Phase D)
- `test_version.py` (Phase E)
- `test_sibling_skill_map.py` (Phase F)
- `test_end_to_end.py` (Phase G)

≈ 9 new test files, ~30 new functions. Total expected: ~17-19 test files, ~90+ functions, all stdlib-only.

- [ ] **Step 2: Commit (empty, marker)**

```bash
git commit --allow-empty -m "test: full v0.4.0 suite green (≈9 new test files, all stdlib)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task G6: CHANGELOG entry

- [ ] **Step 1: Prepend to `CHANGELOG.md`**

```markdown
## v0.4.0 — Production Onboarding (3-phase) — UNRELEASED

### Added — major
- **3-phase onboarding flow.** Phase 1 (Assess, Python script) → Phase 2 (Refine + Deploy, agent-driven via Edit/Write + sibling skills) → Phase 3 (CI/CD Handoff, scaffolded workflow + UAMI runbook).
- **Apply-plan output** (`--apply-plan-out PATH`) with 4 `kind` values: `repo-edit`, `sibling-skill`, `manual`, `deferred-to-pipeline`. Includes `manifest_sha256` for stale-plan detection.
- **Framing wizard** (`--onboard` or `--framing-file PATH`) — 7 questions covering target subscription, RG, posture, provisioning rights, central team, restricted-environment flag, and CI/CD target.
- **61 must-fix remediation recipes** under `references/remediation-recipes/{ID}.md`. Each declares `kind` and provides exact edit / sibling-skill / manual / pipeline instructions. Recipe catalog is enforced by `tests/test_recipe_catalog.py`.
- **Provisioning-rights probe + phase-decision banner.** Live `az role assignment list` against the target RG; classifies operator rights as `full | constrained | none | unknown` and decides Phase-2 mode (self-service vs central-team handoff vs blocked). Skippable via `--no-rights-probe`.
- **CI/CD scaffold** (`--scaffold-cicd`) — renders `.github/workflows/azd-deploy-prod.yml` and `docs/threadlight-cicd/central-team-uami-readme.md` from templates. Uses federated credentials only; no long-lived secrets in GitHub.
- **Sibling-skill invocation map** at `references/sibling-skills-map.md` — single source of truth for which awesome-gbb skill the agent invokes when a recipe is `kind: sibling-skill`.
- **`sample-pilot-restricted` fixture** modeling a central-team-owned environment (Q6 restricted = true, Reader-only rights).

### Changed — major
- **SKILL.md contract flipped.** v0.3.0's "recommends, never executes" → v0.4.0's "Phase 1 assesses, Phase 2 (agent) executes, Phase 3 (script) scaffolds." The Python script remains assessor-only by design — execution flows through Copilot agent tools so `git diff` stays the audit trail. **No `--apply FINDING_ID` flag was added or planned.**

### Added — minor
- `VERSION = "0.4.0"` pin (`tests/test_version.py`).
- CLI flags: `--onboard`, `--framing-file`, `--apply-plan-out`, `--scaffold-cicd`, `--no-rights-probe`, `--repo-full-name`.
- Per-pillar cross-references from each `references/pillars/{N}-*.md` to its remediation recipes.
- `phase_decision` and `rights_probe` keys in `production-readiness-manifest.json`.

### Tests
- 9 new stdlib test files (~30 new test functions). Continues v0.3.0's stdlib-only commitment — no pytest, no third-party deps.

### Deferred to v0.5.0+
- `gateway-resilience` pillar (`GW-001..103`).
- Full SPEC §12 per-customer enforcement of `defender_plans_required` + `required_policy_ids`.
- 24 experimental stub closures (continuous-evals, NSG flow logs, cost actuals, AppInsights replay).
- Awesome-gbb upstream landings for #267-272 (recipes currently `kind: manual` until each ships).

### Migration notes for v0.3.0 users
- No breaking script-CLI changes for existing `--target-sub`/`--target-rg`/`--skip-live`/`--gate-preview` usage; v0.3.0 invocations continue to work and now emit the phase banner. To opt into the new flow, add `--onboard` (TTY) or `--framing-file <path>` (headless).
- Existing fixtures (`sample-pilot{,-citadel,-broken,-v4}`) had their manifests refreshed to include the new `version`, `rights_probe`, and `phase_decision` keys. No finding outcomes (pass/fail counts) changed.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "chore(changelog): v0.4.0 entry

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task G7: Final smoke — all four fixtures + tests, in one go

- [ ] **Step 1: Single script that exercises everything**

```bash
cd skills/threadlight-production-ready

echo "=== tests ==="
for f in tests/test_*.py; do python3 "$f" || exit 1; done

echo "=== fixtures ==="
for f in sample-pilot sample-pilot-citadel sample-pilot-broken sample-pilot-restricted; do
  cd references/fixtures/$f
  python3 ../../../scripts/production_ready.py --skip-live --no-rights-probe >/dev/null
  cd - >/dev/null
done

echo "=== fixture dirty-check ==="
git status --short references/fixtures/  # should print nothing if goldens are clean
echo "OK"
```

If the dirty-check prints anything, the goldens drifted — investigate and refresh with `git add` + new commit.

- [ ] **Step 2: Restore any mutated fixtures**

```bash
git checkout HEAD -- skills/threadlight-production-ready/references/fixtures/
```

- [ ] **Step 3: Final commit**

```bash
git commit --allow-empty -m "test: v0.4.0 final smoke green (all 4 fixtures + all tests)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---
## Self-Review (writing-plans skill checklist)

### 1. Spec coverage

The approved spec at `docs/superpowers/specs/2026-06-10-threadlight-production-ready-v040-design.md` has these top-level sections; each is addressed by at least one task here:

| Spec section | Plan task(s) |
| --- | --- |
| §Overview — 3-phase flow | Phases A-D collectively realize all 3 phases |
| §Phase 1 — Framing wizard (7 questions) | A1, A2, A3 |
| §Phase 1 — Apply-plan schema | A4 |
| §Phase 1 — Rights probe + phase decision | C1, C2, C3, C4, C5, C6 |
| §Phase 2 — Recipe format + 4 `kind` values | B1, B3, B4, B5, B6 (one exemplar per kind) |
| §Phase 2 — Every must-fix has a recipe | B2 (gate test) + B7-B15 (61 recipes) + B16 (turn green) |
| §Phase 2 — Recipe catalog loader | A6 |
| §Phase 3 — CI/CD scaffold | D1, D4, D5, D6 |
| §Phase 3 — UAMI/FedCred runbook | D2 |
| §Phase 3 — Template renderer | D3 |
| §CLI surface — new flags | A5 (`--onboard`, `--framing-file`, `--apply-plan-out`), C6 (`--no-rights-probe`), D5 (`--scaffold-cicd`, `--repo-full-name`) |
| §CLI surface — NO `--apply FINDING_ID` | Documented in CHANGELOG (G6) + SKILL.md flip (E2) — not added anywhere |
| §Sibling-skill invocation map | F1, F2, F3 |
| §SKILL.md rewrite + version bump | E1, E2, E3, E4 |
| §Restricted-environment fixture | G1, G2 |
| §End-to-end test | G4 |
| §Open questions answered | Answers ship inside the spec commit (already on disk) |

No gaps identified.

### 2. Placeholder scan

Searched the full plan for the red-flag patterns from the writing-plans skill:

- `TBD` / `TODO` / `implement later` / `fill in details`: ZERO occurrences in plan body. (One `TODO(operator):` is inside a workflow template — that's a deliberate signal to the dev who reads the rendered workflow, not a plan placeholder.)
- "Add appropriate error handling": ZERO.
- "Write tests for the above" without code: ZERO — every test step shows full test code.
- "Similar to Task N": ZERO — Tasks B7-B15 reference the patterns from B3-B6 by name, but each pillar bucket commit is a distinct unit of work; the recipe authoring guidance is the work itself, not a placeholder.
- Code references to undefined symbols: every function or constant referenced in a later task is defined in an earlier task (`_classify_rights` C2→used C3; `_phase_decision` C5→wired C6; `_render_template` D3→used D4; `_scaffold_cicd` D4→wired D5; etc.).

One intentional pattern: the recipe-catalog test (B2) is deliberately committed RED and stays red until B16. This is the "failing-test-as-gate" pattern; it's not a placeholder. Plan calls this out explicitly.

### 3. Type / signature consistency check

Walked the cross-task references:

| Symbol | Defined in | Used in | Signature match? |
| --- | --- | --- | --- |
| `FRAMING_QUESTIONS` | A1 | A2, A3 | ✅ |
| `_run_framing_wizard(stdin, stdout) -> dict` | A2 | A5 | ✅ |
| `_load_framing_file(path) -> dict` | A3 | A5 | ✅ |
| `apply_plan` dict shape | A4 | C6, D6, G4 | ✅ — keys: `items`, `manifest_sha256`, `framing_path` |
| `load_recipe_catalog(rdir) -> dict` | A6 | B2 | ✅ |
| `_classify_rights(roles) -> str` | C2 | C3 | ✅ |
| `_probe_provisioning_rights(sub, rg, skip=False) -> dict` | C3, C4 | C6 | ✅ — return-shape: `{rights_class, roles, probe_skipped, error}` |
| `_phase_decision(framing, rights) -> dict` | C5 | C6 | ✅ — return-shape: `{phase2_mode, reason, warning}` |
| `_render_template(path, ctx) -> str` | D3 | D4 | ✅ |
| `_cicd_context_from_framing(framing, repo, env_name=) -> dict` | D3 | D4 | ✅ |
| `_scaffold_cicd(framing, repo, out_root) -> list[Path]` | D4 | D5 | ✅ |
| `_detect_repo_full_name(cwd) -> str|None` | D5 | D5 | ✅ |
| `RIGHTS_*`, `PHASE_*` constants | C1 | C2, C3, C5, G4 | ✅ |

One thing the plan could be tighter about: the **manifest extension**. Multiple tasks add new keys to `production-readiness-manifest.json` (`version` E1, `rights_probe` + `phase_decision` C6), and Task G3 says fixture goldens need refreshing for these new keys. That refresh is captured as a distinct commit step, so it's not a gap — but the executor should keep an eye on it.

No fixes required.

### 4. Effort sanity check

| Phase | LOC delta (script + tests) | New files | Effort vs v0.3.0 modules |
| --- | --- | --- | --- |
| A — Framing + apply-plan | ~300 | 2 tests | similar to v0.3.0 Module F |
| B — 61 recipes + gate test | recipes are docs; ~50 LOC test | 62 markdown files + 1 test | new shape; ~half a v0.3.0 module of *script* work, but bulk doc lift |
| C — Rights probe + phase decision | ~150 | 2 tests | similar to v0.3.0 Module H |
| D — CI/CD scaffold | ~100 + 2 templates | 1 test + 2 templates | similar to v0.3.0 Module J |
| E — SKILL.md + version | ~30 (mostly docs) | 1 test | small |
| F — Sibling-skill map | ~30 (mostly docs) | 1 test + 1 md | small |
| G — Fixture + e2e + CHANGELOG | ~80 test + 4 fixture files | 1 test + 1 fixture dir | similar to v0.3.0 Module G |

Aggregate: ~600 LOC of *new script code*, ~150 LOC of new tests, 61 markdown recipes + 4 templates + 1 fixture dir + ~10 documentation rewrites. Estimate: **~70-80% the total size of v0.3.0**, but the shape is inverted — v0.3.0 was script-heavy + doc-light, v0.4.0 is doc-heavy + script-light.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-10-threadlight-production-ready-v040.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Dispatch one fresh subagent per phase (A, B, C, D, E, F, G). Within each phase, dispatch one subagent per task (A1, A2, … or per pillar-bucket B7, B8, …). Parent reviews between phases.

**2. Inline Execution** — Execute all 7 phases in one session using the `superpowers:executing-plans` skill, with checkpoints between phases.

Phase B (the 61 recipes) is the largest single chunk. It parallelizes cleanly — each pillar bucket (B7-B15) is independent and could run as ~9 concurrent subagents.

**For the parent session that spawned me:** the plan is structured so a fresh executor session can start at Task A1 without any further context beyond:

1. The plan file (this document).
2. The design spec (`docs/superpowers/specs/2026-06-10-threadlight-production-ready-v040-design.md`).
3. The v0.3.0 script as the assessor base (`skills/threadlight-production-ready/scripts/production_ready.py`).
4. The Co-authored-by trailer requirement.
5. The fixture-mutation gotcha (always `git checkout HEAD -- references/fixtures/` after smoke runs).

**Stop before implementing.** Per parent brief, implementation is a separate session.
