# Threadlight Router Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a controlled 6-run matrix (3 model arms × 2 workloads of increasing complexity) through the full CI e2e and produce a composite scorecard that says whether gpt-5.4-mini "keeps up" and whether a 5.4+5.4-mini router is worth it.

**Architecture:** Extend the shipped `threadlight-router-bench` skill (Approach A from the spec). Parameterize `threadlight-e2e-foundry.yml` with a `workload` input backed by per-workload "packs" under `.github/workloads/`. Add skill modules for rounds extraction, a quality-rubric scorer, a validation scorecard, a markdown matrix report, and a `validate` subcommand with a 2-wave orchestrator. One-time infra creates a `gpt-5.4` deployment and constrains the router's model subset.

**Tech Stack:** Python 3.13 stdlib (+ pyyaml, already a CI dep), GitHub Actions, Azure CLI + Management REST (`az rest`/curl), `gh` CLI. Tests with pytest.

**Spec:** `docs/superpowers/specs/2026-06-30-threadlight-router-validation-design.md`

**Grounded environment facts (verified 2026-06-30):**
- Account: `aif-shared-gbb-ci` / RG `rg-shared-gbb-ci` / sub `2c745a8f-9d37-45e3-8506-80797e89735e`.
- `gpt-5.4` model: name `gpt-5.4`, version `2026-03-05`, format `OpenAI`, sku `GlobalStandard` (westus3 & swedencentral).
- `gpt-5.4-mini` deployment: model version `2026-03-17`, format `OpenAI`.
- `model-router` deployment: version `2025-11-18`; **currently no `routing` block** (full default pool) → restore = PUT without `routing`.
- Deployments management REST api-version: `2025-10-01-preview`. Subset changes take **up to 5 min** to propagate.
- Router arm must use `wire_api=completions` (Responses route returns HTTP 400); GPT-5 direct deployments use `responses`.
- Existing skill modules: `score.py` (`SCHEMA`, `cost_of`, `scorecard`, `render_scorecard`), `harvest.py` (`parse_phase_parity`, `download_run`, `find_specs_dir`, injectable `runner`), `report.py` (`build_digest`, `render_markdown`), `router_bench.py` (`run_learn`, `run_bench`, `build_parser`, `main`). 30 tests currently green — keep them green.

---

## File Structure

**Create:**
- `.github/workloads/returns-triage/{phases.yml,rubric.yml,meta.yml}` — simple pack (prompts extracted verbatim from today's workflow).
- `.github/workloads/fsi-kyc-aml/{phases.yml,rubric.yml,meta.yml}` — complex pack (new).
- `skills/threadlight-router-bench/scripts/rubric.py` — quality-rubric loader + scorer.
- `skills/threadlight-router-bench/scripts/matrix.py` — 2-wave matrix orchestrator + manifest.
- `skills/threadlight-router-bench/tests/test_rubric.py`
- `skills/threadlight-router-bench/tests/test_rounds.py`
- `skills/threadlight-router-bench/tests/test_validation_score.py`
- `skills/threadlight-router-bench/tests/test_matrix.py`
- `skills/threadlight-router-bench/references/fixtures/phase-design-sample.log` — round-count fixture.
- `skills/threadlight-router-bench/references/fixtures/kyc-spec-good.md` / `kyc-spec-bad.md` — rubric fixtures.
- `scripts/ci/foundry-strong-arm.sh` — idempotent gpt-5.4 deployment create + verify.
- `scripts/ci/router-subset.sh` — record/set/restore router subset.

**Modify:**
- `skills/threadlight-router-bench/scripts/harvest.py` — add `count_rounds`.
- `skills/threadlight-router-bench/scripts/score.py` — add `validation_scorecard`.
- `skills/threadlight-router-bench/scripts/report.py` — add `render_validation_matrix`.
- `skills/threadlight-router-bench/scripts/router_bench.py` — add `validate` subcommand.
- `skills/threadlight-router-bench/SKILL.md` — document `validate` mode.
- `.github/workflows/threadlight-e2e-foundry.yml` — add `workload` input, pack loader, upload `/tmp/copilot-logs`.

---

## Task 1: Quality-rubric scorer (`rubric.py`)

**Files:**
- Create: `skills/threadlight-router-bench/scripts/rubric.py`
- Create: `skills/threadlight-router-bench/tests/test_rubric.py`
- Create: `skills/threadlight-router-bench/references/fixtures/kyc-spec-good.md`, `kyc-spec-bad.md`

- [ ] **Step 1: Write the fixtures**

`kyc-spec-good.md` (covers every hard-point):
```markdown
# KYC/AML Onboarding Spec
Retention: AML records kept 5-7 years; reconciled against GDPR right-to-erasure via legal-hold exemption.
Beneficial owners: identify all >=25% owners (10% threshold for high-risk EU states); nested entity ownership resolved recursively.
SAR: filed within 30 days; tipping-off prohibited - the customer is never notified of a SAR.
CTR: cash >= $10,000 filed; structuring detection aggregates split transactions under the threshold.
EDD: high-risk escalates to Enhanced Due Diligence requiring senior-approval gate.
Jurisdiction: US (BSA/FinCEN) and EU (AMLD) thresholds handled separately, not hard-coded.
```

`kyc-spec-bad.md` (omits tipping-off + retention tension + structuring):
```markdown
# Returns Triage Spec
Customers are onboarded and screened. Beneficial owners over 25% are recorded.
High-risk customers get Enhanced Due Diligence with senior approval.
We support US and EU thresholds.
```

- [ ] **Step 2: Write the failing tests**

```python
# skills/threadlight-router-bench/tests/test_rubric.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rubric

FIX = Path(__file__).resolve().parents[1] / "references" / "fixtures"

RUBRIC = {
    "checks": [
        {"id": "retention-tension", "weight": 1,
         "all_of": ["retention", "gdpr"]},
        {"id": "beneficial-ownership", "weight": 1, "regex": r"25\s*%"},
        {"id": "tipping-off", "weight": 1, "contains": "tipping-off"},
        {"id": "structuring-ctr", "weight": 1,
         "all_of": ["10,000", "structuring"]},
        {"id": "edd-approval", "weight": 1,
         "all_of": ["enhanced due diligence", "senior-approval"]},
        {"id": "multi-jurisdiction", "weight": 1,
         "all_of": ["bsa", "amld"]},
    ]
}

def test_good_spec_scores_full():
    res = rubric.score_rubric(FIX / "kyc-spec-good.md", RUBRIC)
    assert res["score"] == 1.0
    assert all(c["passed"] for c in res["checks"])

def test_bad_spec_fails_specific_checks():
    res = rubric.score_rubric(FIX / "kyc-spec-bad.md", RUBRIC)
    failed = {c["id"] for c in res["checks"] if not c["passed"]}
    assert {"tipping-off", "retention-tension", "structuring-ctr"} <= failed
    assert res["score"] < 0.6

def test_score_rubric_accepts_dir(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "SPEC.md").write_text("tipping-off prohibited", encoding="utf-8")
    res = rubric.score_rubric(tmp_path, {"checks": [
        {"id": "t", "weight": 1, "contains": "tipping-off"}]})
    assert res["score"] == 1.0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_rubric.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rubric'`

- [ ] **Step 4: Implement `rubric.py`**

```python
# skills/threadlight-router-bench/scripts/rubric.py
"""Quality-rubric scorer for threadlight-router-validation.

Scores a built PoC's artifacts (SPEC.md etc.) against a per-workload rubric
of "hard-points". Match strategies, all case-insensitive over concatenated
artifact text:
  - contains: substring present
  - regex:    re.search matches
  - all_of:   every substring present
  - any_of:   at least one substring present
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Artifact files we concatenate for matching, in priority order.
_ARTIFACT_GLOBS = ("specs/SPEC.md", "AGENTS.md", "tests/killer-prompts.md",
                   "specs/*.md")


def _gather_text(target: Path) -> str:
    """Return lowercased concatenated text of relevant artifacts.

    `target` may be a single file or a PoC directory.
    """
    if target.is_file():
        return target.read_text(encoding="utf-8", errors="ignore").lower()
    parts: list[str] = []
    seen: set[Path] = set()
    for pattern in _ARTIFACT_GLOBS:
        for p in sorted(target.glob(pattern)):
            if p.is_file() and p not in seen:
                seen.add(p)
                parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts).lower()


def _check_passes(text: str, check: dict[str, Any]) -> bool:
    if "contains" in check:
        return check["contains"].lower() in text
    if "regex" in check:
        return re.search(check["regex"], text, re.IGNORECASE) is not None
    if "all_of" in check:
        return all(s.lower() in text for s in check["all_of"])
    if "any_of" in check:
        return any(s.lower() in text for s in check["any_of"])
    return False


def score_rubric(target: Path, rubric: dict[str, Any]) -> dict[str, Any]:
    """Score `target` artifacts against `rubric`. Returns score 0..1 + checks."""
    text = _gather_text(Path(target))
    checks_out = []
    total_w = 0.0
    earned_w = 0.0
    for check in rubric.get("checks", []):
        w = float(check.get("weight", 1))
        passed = _check_passes(text, check)
        total_w += w
        if passed:
            earned_w += w
        checks_out.append({"id": check["id"], "passed": passed, "weight": w})
    score = round(earned_w / total_w, 4) if total_w else 0.0
    return {"score": score, "checks": checks_out}


def load_rubric(path: Path) -> dict[str, Any]:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_rubric.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add skills/threadlight-router-bench/scripts/rubric.py \
        skills/threadlight-router-bench/tests/test_rubric.py \
        skills/threadlight-router-bench/references/fixtures/kyc-spec-*.md
git commit -m "feat(router-bench): add quality-rubric scorer

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 2: Rounds extraction (`harvest.count_rounds`)

**Files:**
- Modify: `skills/threadlight-router-bench/scripts/harvest.py`
- Create: `skills/threadlight-router-bench/tests/test_rounds.py`
- Create: `skills/threadlight-router-bench/references/fixtures/phase-design-sample.log`

- [ ] **Step 1: Write the fixture** (`phase-design-sample.log`)

```
----- design prompt -----
Use threadlight-design ...
-------------------------
===== [design] Copilot CLI attempt 1 of 3 =====
● skill(threadlight-design)
● Todo added 4 items
  │ Probing runtime capabilities
● Read retail.md
  │ 225 lines read
● Wrote SPEC.md
===== [design] Attempt 1 succeeded in 240s =====
```

- [ ] **Step 2: Write the failing test**

```python
# skills/threadlight-router-bench/tests/test_rounds.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import harvest

FIX = Path(__file__).resolve().parents[1] / "references" / "fixtures"

def test_count_rounds_counts_steps_and_attempts():
    res = harvest.count_rounds([FIX / "phase-design-sample.log"])
    assert res["steps"] == 4          # four '● ' lines
    assert res["attempts"] == 1       # one 'attempt N of 3' header
    assert res["total"] == 4

def test_count_rounds_missing_file_is_zero():
    res = harvest.count_rounds([FIX / "does-not-exist.log"])
    assert res == {"steps": 0, "attempts": 0, "total": 0}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_rounds.py -v`
Expected: FAIL with `AttributeError: module 'harvest' has no attribute 'count_rounds'`

- [ ] **Step 4: Implement `count_rounds` in `harvest.py`** (append near the other parse functions)

```python
_STEP_RE = __import__("re").compile(r"^\u25cf ")          # '● ' agent step marker
_ATTEMPT_RE = __import__("re").compile(r"Copilot CLI attempt \d+ of \d+")


def count_rounds(phase_log_paths: list[Path]) -> dict[str, int]:
    """Count agent steps ('● ' lines) and retry attempts across phase logs.

    Returns {'steps': int, 'attempts': int, 'total': int}. `total` == steps
    (the headline rounds-to-done effort signal); attempts is reported
    separately so a thrash-and-retry run is distinguishable.
    """
    steps = 0
    attempts = 0
    for path in phase_log_paths:
        p = Path(path)
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if _STEP_RE.match(line):
                steps += 1
            if _ATTEMPT_RE.search(line):
                attempts += 1
    return {"steps": steps, "attempts": attempts, "total": steps}
```

> Note: keep the existing `import re` at top of `harvest.py`; if present, replace the `__import__("re")` shims with module-level `re.compile(...)` constants for cleanliness.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_rounds.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add skills/threadlight-router-bench/scripts/harvest.py \
        skills/threadlight-router-bench/tests/test_rounds.py \
        skills/threadlight-router-bench/references/fixtures/phase-design-sample.log
git commit -m "feat(router-bench): extract rounds-to-done from phase logs

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 3: Validation scorecard (`score.validation_scorecard`)

**Files:**
- Modify: `skills/threadlight-router-bench/scripts/score.py`
- Create: `skills/threadlight-router-bench/tests/test_validation_score.py`

- [ ] **Step 1: Write the failing tests** (exercise every verdict branch)

```python
# skills/threadlight-router-bench/tests/test_validation_score.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import score

def _arm(name, ok=True, rounds=100, rubric=0.9, cost=1.0):
    return {"arm": name, "phases_ok": ok, "rounds": rounds,
            "rubric": rubric, "cost_usd": cost}

def test_mini_keeps_up_on_simple():
    card = score.validation_scorecard("returns-triage", [
        _arm("mini", rounds=110, rubric=0.9, cost=0.9),
        _arm("router", rounds=120, rubric=0.92, cost=8.0),
        _arm("strong", rounds=100, rubric=0.95, cost=20.0),
    ])
    assert card["arms"]["mini"]["verdict"] == "keeps-up"

def test_mini_falls_behind_on_rubric():
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("mini", rounds=140, rubric=0.6, cost=1.0),
        _arm("router", rounds=150, rubric=0.88, cost=8.0),
        _arm("strong", rounds=130, rubric=0.9, cost=22.0),
    ])
    assert card["arms"]["mini"]["verdict"] == "falls-behind"
    assert "rubric" in card["arms"]["mini"]["reasons"]

def test_mini_falls_behind_on_rounds():
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("mini", rounds=300, rubric=0.85, cost=1.0),
        _arm("strong", rounds=130, rubric=0.9, cost=22.0),
    ])
    assert card["arms"]["mini"]["verdict"] == "falls-behind"
    assert "rounds" in card["arms"]["mini"]["reasons"]

def test_router_closes_gap():
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("router", rounds=150, rubric=0.88, cost=8.0),
        _arm("strong", rounds=140, rubric=0.9, cost=22.0),
    ])
    assert card["router_verdict"] == "closes-the-gap"

def test_router_not_worth_it():
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("mini", rounds=140, rubric=0.62, cost=1.0),
        _arm("router", rounds=150, rubric=0.64, cost=20.0),
        _arm("strong", rounds=140, rubric=0.9, cost=22.0),
    ])
    assert card["router_verdict"] == "not-worth-it"

def test_phase_failure_is_falls_behind():
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("mini", ok=False, rounds=120, rubric=0.9, cost=1.0),
        _arm("strong", rounds=120, rubric=0.9, cost=22.0),
    ])
    assert card["arms"]["mini"]["verdict"] == "falls-behind"
    assert "phase" in card["arms"]["mini"]["reasons"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_validation_score.py -v`
Expected: FAIL with `AttributeError: module 'score' has no attribute 'validation_scorecard'`

- [ ] **Step 3: Implement `validation_scorecard` in `score.py`** (append after `scorecard`)

```python
VALIDATION_SCHEMA = "threadlight-router-validation/v1"

# Verdict thresholds (from the design spec §4).
RUBRIC_FLOOR = 0.8
ROUNDS_FACTOR = 1.5
RUBRIC_CLOSE = 0.1   # router rubric within this of strong == "matches"
COST_CLOSE = 0.20    # cost within +/-20% == "approximately equal"


def _arm_index(arms: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {a["arm"]: a for a in arms}


def validation_scorecard(workload: str,
                         arms: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a threadlight-router-validation/v1 scorecard for one workload.

    Each arm dict: {arm, phases_ok: bool, rounds: int, rubric: float,
    cost_usd: float}. Verdicts per design spec: an arm "keeps-up" iff
    phases_ok AND rubric >= RUBRIC_FLOOR AND rounds <= ROUNDS_FACTOR * strong
    rounds; else "falls-behind" with reasons. Router verdict compares the
    router arm to strong.
    """
    idx = _arm_index(arms)
    strong = idx.get("strong")
    strong_rounds = strong["rounds"] if strong else None

    out_arms: dict[str, Any] = {}
    for a in arms:
        reasons: list[str] = []
        if not a.get("phases_ok", False):
            reasons.append("phase")
        if a.get("rubric", 0.0) < RUBRIC_FLOOR:
            reasons.append("rubric")
        if (strong_rounds is not None and a["arm"] != "strong"
                and a.get("rounds", 0) > ROUNDS_FACTOR * strong_rounds):
            reasons.append("rounds")
        verdict = "keeps-up" if not reasons else "falls-behind"
        out_arms[a["arm"]] = {**a, "verdict": verdict, "reasons": reasons}

    router_verdict = None
    router = idx.get("router")
    if router and strong:
        rubric_matches = abs(router["rubric"] - strong["rubric"]) <= RUBRIC_CLOSE
        cheaper = router["cost_usd"] < strong["cost_usd"]
        cost_similar = (abs(router["cost_usd"] - strong["cost_usd"])
                        <= COST_CLOSE * strong["cost_usd"])
        if rubric_matches and cheaper:
            router_verdict = "closes-the-gap"
        elif cost_similar and not rubric_matches:
            router_verdict = "not-worth-it"
        else:
            router_verdict = "mixed"

    return {
        "schema": VALIDATION_SCHEMA,
        "workload": workload,
        "arms": out_arms,
        "router_verdict": router_verdict,
        "thresholds": {"rubric_floor": RUBRIC_FLOOR,
                       "rounds_factor": ROUNDS_FACTOR},
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_validation_score.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-router-bench/scripts/score.py \
        skills/threadlight-router-bench/tests/test_validation_score.py
git commit -m "feat(router-bench): add validation_scorecard with verdict logic

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 4: Validation matrix report (`report.render_validation_matrix`)

**Files:**
- Modify: `skills/threadlight-router-bench/scripts/report.py`
- Modify: `skills/threadlight-router-bench/tests/test_report.py` (add one test)

- [ ] **Step 1: Write the failing test** (append to `test_report.py`)

```python
def test_render_validation_matrix_has_arms_and_verdict():
    import report
    cards = [{
        "schema": "threadlight-router-validation/v1",
        "workload": "fsi-kyc-aml",
        "router_verdict": "closes-the-gap",
        "arms": {
            "mini":   {"arm": "mini", "phases_ok": True, "rounds": 300,
                       "rubric": 0.6, "cost_usd": 1.0, "verdict": "falls-behind",
                       "reasons": ["rubric", "rounds"]},
            "router": {"arm": "router", "phases_ok": True, "rounds": 150,
                       "rubric": 0.88, "cost_usd": 8.0, "verdict": "keeps-up",
                       "reasons": []},
            "strong": {"arm": "strong", "phases_ok": True, "rounds": 140,
                       "rubric": 0.9, "cost_usd": 22.0, "verdict": "keeps-up",
                       "reasons": []},
        },
    }]
    md = report.render_validation_matrix(cards)
    assert "fsi-kyc-aml" in md
    assert "falls-behind" in md
    assert "closes-the-gap" in md
    assert "| mini |" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_report.py::test_render_validation_matrix_has_arms_and_verdict -v`
Expected: FAIL with `AttributeError: module 'report' has no attribute 'render_validation_matrix'`

- [ ] **Step 3: Implement `render_validation_matrix` in `report.py`**

```python
def render_validation_matrix(cards: list[dict[str, Any]]) -> str:
    """Render a markdown matrix (workload x arm x axes) + headline verdicts."""
    lines = ["# Router validation scorecard", ""]
    for card in cards:
        lines.append(f"## {card['workload']}")
        lines.append("")
        lines.append("| arm | phases | rounds | rubric | cost (USD) | verdict |")
        lines.append("|-----|--------|--------|--------|-----------|---------|")
        for arm in ("mini", "router", "strong"):
            a = card["arms"].get(arm)
            if not a:
                continue
            ph = "pass" if a["phases_ok"] else "FAIL"
            lines.append(
                f"| {arm} | {ph} | {a['rounds']} | {a['rubric']:.2f} | "
                f"${a['cost_usd']:.2f} | {a['verdict']} |")
        rv = card.get("router_verdict")
        if rv:
            lines.append("")
            lines.append(f"**Router verdict:** {rv}")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_report.py -v`
Expected: PASS (all report tests incl. the new one)

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-router-bench/scripts/report.py \
        skills/threadlight-router-bench/tests/test_report.py
git commit -m "feat(router-bench): render validation matrix markdown

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 5: Matrix orchestrator (`matrix.py`)

**Files:**
- Create: `skills/threadlight-router-bench/scripts/matrix.py`
- Create: `skills/threadlight-router-bench/tests/test_matrix.py`

- [ ] **Step 1: Write the failing tests**

```python
# skills/threadlight-router-bench/tests/test_matrix.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import matrix

ARMS = [
    {"arm": "mini", "model_deployment": "gpt-5.4-mini", "wire_api": "responses"},
    {"arm": "router", "model_deployment": "model-router", "wire_api": "completions"},
    {"arm": "strong", "model_deployment": "gpt-5.4", "wire_api": "responses"},
]

def test_plan_waves_groups_by_workload_no_same_deployment_overlap():
    waves = matrix.plan_waves(["returns-triage", "fsi-kyc-aml"], ARMS)
    assert len(waves) == 2
    for wave in waves:
        deps = [c["model_deployment"] for c in wave]
        assert len(deps) == len(set(deps))      # no same-deployment overlap
        assert len({c["workload"] for c in wave}) == 1

def test_dispatch_matrix_records_manifest():
    calls = []
    def fake_runner(args):
        calls.append(args)
        if args[:2] == ["run", "list"]:
            return '[{"databaseId": 999, "createdAt": "2026-06-30T10:00:00Z"}]'
        return ""
    cells = matrix.dispatch_matrix(
        ["returns-triage"], ARMS, repo="o/r", ref="br",
        runner=fake_runner, poll=False)
    assert len(cells) == 3
    assert all(c["run_id"] == 999 for c in cells)
    assert any(a[:2] == ["workflow", "run"] for a in calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_matrix.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'matrix'`

- [ ] **Step 3: Implement `matrix.py`**

```python
# skills/threadlight-router-bench/scripts/matrix.py
"""Matrix orchestrator for threadlight-router-validation.

Dispatches the 3-arm x N-workload matrix as cost-clean waves: within a wave,
every cell targets a DISTINCT model deployment so Azure Monitor windows don't
bleed (metrics filter by ModelDeploymentName). One wave per workload.
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Callable

Runner = Callable[[list[str]], str]
WORKFLOW = "threadlight-e2e-foundry.yml"


def _default_runner(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True,
                          text=True).stdout


def plan_waves(workloads: list[str],
               arms: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """One wave per workload; each cell = arm x workload. Guarantees no two
    cells in a wave share a model deployment."""
    waves = []
    for wl in workloads:
        wave = [{**a, "workload": wl} for a in arms]
        deps = [c["model_deployment"] for c in wave]
        if len(deps) != len(set(deps)):
            raise ValueError(f"same-deployment overlap in wave for {wl}")
        waves.append(wave)
    return waves


def _latest_run_id(runner: Runner, repo: str, ref: str) -> int | None:
    out = runner(["run", "list", "--workflow", WORKFLOW, "--repo", repo,
                  "--branch", ref, "--limit", "1",
                  "--json", "databaseId,createdAt"])
    rows = json.loads(out or "[]")
    return rows[0]["databaseId"] if rows else None


def _dispatch_cell(runner: Runner, cell: dict[str, Any], repo: str,
                   ref: str) -> dict[str, Any]:
    runner(["workflow", "run", WORKFLOW, "--repo", repo, "--ref", ref,
            "-f", f"model_deployment={cell['model_deployment']}",
            "-f", f"wire_api={cell['wire_api']}",
            "-f", f"workload={cell['workload']}",
            "-f", "mode=full", "-f", "teardown=true"])
    time.sleep(0)  # dispatch returns no id; caller polls run list
    run_id = _latest_run_id(runner, repo, ref)
    return {**cell, "run_id": run_id}


def dispatch_matrix(workloads: list[str], arms: list[dict[str, Any]], *,
                    repo: str, ref: str, runner: Runner | None = None,
                    poll: bool = True,
                    poll_interval: int = 60) -> list[dict[str, Any]]:
    runner = runner or _default_runner
    cells: list[dict[str, Any]] = []
    for wave in plan_waves(workloads, arms):
        wave_cells = [_dispatch_cell(runner, c, repo, ref) for c in wave]
        if poll:
            _wait_for_wave(runner, wave_cells, repo, poll_interval)
        cells.extend(wave_cells)
    return cells


def _wait_for_wave(runner: Runner, cells: list[dict[str, Any]], repo: str,
                   interval: int) -> None:
    pending = {c["run_id"] for c in cells if c.get("run_id")}
    while pending:
        time.sleep(interval)
        done = set()
        for rid in pending:
            out = runner(["run", "view", str(rid), "--repo", repo,
                          "--json", "status"])
            if json.loads(out or "{}").get("status") == "completed":
                done.add(rid)
        pending -= done


def write_manifest(cells: list[dict[str, Any]], path) -> None:
    from pathlib import Path
    Path(path).write_text(json.dumps({"cells": cells}, indent=2),
                          encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_matrix.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-router-bench/scripts/matrix.py \
        skills/threadlight-router-bench/tests/test_matrix.py
git commit -m "feat(router-bench): add 2-wave matrix orchestrator

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 6: `validate` subcommand (`router_bench.py`)

**Files:**
- Modify: `skills/threadlight-router-bench/scripts/router_bench.py`
- Modify: `skills/threadlight-router-bench/tests/test_cli.py` (add one test)

- [ ] **Step 1: Write the failing test** (append to `test_cli.py`)

```python
def test_validate_ingest_builds_scorecard(tmp_path, monkeypatch):
    import router_bench, json
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"cells": [
        {"arm": "mini", "workload": "returns-triage", "run_id": 1},
        {"arm": "strong", "workload": "returns-triage", "run_id": 2},
    ]}), encoding="utf-8")

    # Stub the per-cell scorer so the test stays offline.
    monkeypatch.setattr(router_bench, "_score_cell", lambda cell, **k: {
        "arm": cell["arm"], "phases_ok": True, "rounds": 100,
        "rubric": 0.9, "cost_usd": 1.0})
    rc = router_bench.main(["validate", "--ingest", str(manifest),
                            "--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "router-validation.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_cli.py::test_validate_ingest_builds_scorecard -v`
Expected: FAIL (`validate` not a valid choice / `_score_cell` missing)

- [ ] **Step 3: Implement the `validate` subcommand**

In `router_bench.py`, add `_score_cell`, `run_validate`, a `_cmd_validate`, and wire the subparser:

```python
def _score_cell(cell: dict, repo: str = "aiappsgbb/threadlight-skills",
                resource_id: str | None = None,
                runner=None) -> dict:
    """Harvest one matrix cell into scorecard-arm shape. Network-bound;
    stubbed in tests."""
    import harvest, rubric as rubric_mod, metrics, score, json
    from pathlib import Path
    import tempfile
    run_id = cell["run_id"]
    jobs = harvest.fetch_jobs(run_id, repo, runner=runner)
    phases = harvest.parse_phase_parity(jobs)
    phases_ok = all(v == "success" for v in phases.values())
    with tempfile.TemporaryDirectory() as td:
        bundle = Path(td)
        harvest.download_run(run_id, bundle, runner=runner)
        phase_logs = list(bundle.rglob("phase-*.log"))
        rounds = harvest.count_rounds(phase_logs)["total"]
        specs = harvest.find_specs_dir(bundle)
        # rubric doc is attached by the caller from the workload pack:
        rubric_doc = cell.get("_rubric") or {"checks": []}
        rubric_res = rubric_mod.score_rubric(specs or bundle, rubric_doc)
    cost = cell.get("_cost_usd", 0.0)
    return {"arm": cell["arm"], "phases_ok": phases_ok, "rounds": rounds,
            "rubric": rubric_res["score"], "cost_usd": cost}


def run_validate(manifest_path: str, out_dir: str,
                 repo: str = "aiappsgbb/threadlight-skills",
                 resource_id: str | None = None, runner=None) -> int:
    import json, collections
    from pathlib import Path
    import score, report, rubric as rubric_mod
    cells = json.loads(Path(manifest_path).read_text(encoding="utf-8"))["cells"]
    by_wl = collections.defaultdict(list)
    for cell in cells:
        # attach the workload's rubric so _score_cell can score quality
        pack = Path(".github/workloads") / cell["workload"] / "rubric.yml"
        if pack.is_file():
            cell["_rubric"] = rubric_mod.load_rubric(pack)
        by_wl[cell["workload"]].append(_score_cell(cell, repo=repo,
                                                    resource_id=resource_id,
                                                    runner=runner))
    cards = [score.validation_scorecard(wl, arms)
             for wl, arms in by_wl.items()]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "router-validation.json").write_text(
        json.dumps(cards, indent=2), encoding="utf-8")
    (out / "router-validation.md").write_text(
        report.render_validation_matrix(cards), encoding="utf-8")
    print(f"[router-bench] wrote router-validation.(json|md) to {out}")
    return 0


def _cmd_validate(args, runner=None) -> int:
    if args.ingest:
        return run_validate(args.ingest, args.out, repo=args.repo,
                            resource_id=args.resource, runner=runner)
    # dispatch path
    import matrix, json
    from pathlib import Path
    arms = [
        {"arm": "mini", "model_deployment": "gpt-5.4-mini", "wire_api": "responses"},
        {"arm": "router", "model_deployment": "model-router", "wire_api": "completions"},
        {"arm": "strong", "model_deployment": "gpt-5.4", "wire_api": "responses"},
    ]
    cells = matrix.dispatch_matrix(args.workloads, arms, repo=args.repo,
                                   ref=args.ref)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    matrix.write_manifest(cells, Path(args.out) / "matrix-manifest.json")
    print(f"[router-bench] dispatched {len(cells)} runs; manifest in {args.out}")
    return 0
```

Wire into `build_parser()` (alongside `learn`/`bench`):

```python
    v = sub.add_parser("validate", help="run/ingest the router validation matrix")
    v.add_argument("--ingest", help="score an existing matrix-manifest.json")
    v.add_argument("--dispatch", action="store_true",
                   help="dispatch a fresh matrix")
    v.add_argument("--workloads", nargs="+",
                   default=["returns-triage", "fsi-kyc-aml"])
    v.add_argument("--ref", default="unsafecode-automatic-fiesta")
    v.add_argument("--repo", default="aiappsgbb/threadlight-skills")
    v.add_argument("--resource", help="Azure AI Services resource id for cost")
    v.add_argument("--out", default="router-validation-out")
```

And in `main()` dispatch:

```python
    if args.command == "validate":
        return _cmd_validate(args, runner)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd skills/threadlight-router-bench && python3 -m pytest tests/test_cli.py -v`
Expected: PASS (existing CLI tests + the new one)

- [ ] **Step 5: Run the full suite**

Run: `cd skills/threadlight-router-bench && python3 -m pytest -q`
Expected: PASS (all prior 30 + new tests)

- [ ] **Step 6: Commit**

```bash
git add skills/threadlight-router-bench/scripts/router_bench.py \
        skills/threadlight-router-bench/tests/test_cli.py
git commit -m "feat(router-bench): add validate subcommand (dispatch + ingest)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 7: Workload packs

**Files:**
- Create: `.github/workloads/returns-triage/{phases.yml,rubric.yml,meta.yml}`
- Create: `.github/workloads/fsi-kyc-aml/{phases.yml,rubric.yml,meta.yml}`

- [ ] **Step 1: Extract today's returns-triage prompts**

Read the inline phase prompts from `.github/workflows/threadlight-e2e-foundry.yml`
(design/local-test/deploy/invoke heredocs, around lines 446-520+) and copy them
**verbatim** into `returns-triage/phases.yml`:

```yaml
# .github/workloads/returns-triage/phases.yml
workload: returns-triage
phases:
  design:
    prompt: |
      Use the threadlight-design skill in Fast-PoC mode to design a retail
      returns-triage process in ${E2E_WORKSPACE}/returns-triage. ...
      # (verbatim copy of the existing design prompt)
    gates:
      - "specs/SPEC.md exists and mentions 'returns'"
      - "AGENTS.md exists"
      - "specs/sample-data/ contains a non-empty JSON file (>10 bytes)"
      - "tests/killer-prompts.md exists with at least 2 prompts"
  local-test: { prompt: "...", gates: [] }
  deploy:     { prompt: "...", gates: [] }
  invoke:     { prompt: "...", gates: [] }
```

- [ ] **Step 2: returns-triage rubric** (low bar mirroring existing gates)

```yaml
# .github/workloads/returns-triage/rubric.yml
checks:
  - id: mentions-returns
    weight: 1
    contains: "returns"
  - id: has-sample-data
    weight: 1
    any_of: ["sample-data", "sample_data"]
  - id: killer-prompts
    weight: 1
    contains: "killer"
```

```yaml
# .github/workloads/returns-triage/meta.yml
workload: returns-triage
difficulty: simple
hard_points: []
```

- [ ] **Step 3: Author the fsi-kyc-aml pack**

```yaml
# .github/workloads/fsi-kyc-aml/phases.yml
workload: fsi-kyc-aml
phases:
  design:
    prompt: |
      Use the threadlight-design skill in Fast-PoC mode to design a regulated
      KYC/AML customer-onboarding process in ${E2E_WORKSPACE}/kyc-aml. Consult
      the FSI KYC/AML domain primer. The design MUST explicitly address all of:
        - the AML 5-7 year record-retention requirement vs the GDPR right to
          erasure (reconcile the tension, do not ignore it);
        - beneficial ownership: identify all >=25% owners (10% for high-risk EU
          states) including nested entity-owns-entity ownership;
        - SAR filing within 30 days WITH the tipping-off prohibition (the
          customer must never be notified of a SAR);
        - CTR filing for cash >= $10,000 AND structuring detection for split
          transactions under the threshold;
        - EDD escalation for high-risk customers with a senior-approval gate;
        - distinct US (BSA/FinCEN) and EU (AMLD) thresholds (do not hard-code one).
      Produce specs/SPEC.md, AGENTS.md, specs/sample-data/*.json, and
      tests/killer-prompts.md (>=2 prompts that probe the hard requirements).
    gates:
      - "specs/SPEC.md exists and mentions 'KYC' or 'AML'"
      - "AGENTS.md exists"
      - "specs/sample-data/ contains a non-empty JSON file (>10 bytes)"
      - "tests/killer-prompts.md exists with at least 2 prompts"
  local-test:
    prompt: |
      Use threadlight-local-test Pattern 0 to boot the KYC/AML PoC in
      ${E2E_WORKSPACE}/kyc-aml and confirm it answers a sample onboarding query.
    gates: []
  deploy:
    prompt: |
      Use threadlight-deploy to deploy the KYC/AML PoC from
      ${E2E_WORKSPACE}/kyc-aml via azd up.
    gates: []
  invoke:
    prompt: |
      Invoke the deployed KYC/AML agent with the two killer prompts in
      ${E2E_WORKSPACE}/kyc-aml/tests/killer-prompts.md and capture transcripts.
    gates: []
```

```yaml
# .github/workloads/fsi-kyc-aml/rubric.yml
checks:
  - id: retention-tension
    weight: 1
    all_of: ["retention", "gdpr"]
  - id: beneficial-ownership
    weight: 1
    regex: "25\\s*%"
  - id: tipping-off
    weight: 1
    contains: "tipping-off"
  - id: structuring-ctr
    weight: 1
    all_of: ["10,000", "structuring"]
  - id: edd-approval
    weight: 1
    all_of: ["enhanced due diligence", "senior"]
  - id: multi-jurisdiction
    weight: 1
    all_of: ["bsa", "amld"]
```

```yaml
# .github/workloads/fsi-kyc-aml/meta.yml
workload: fsi-kyc-aml
difficulty: complex
hard_points:
  - retention-tension
  - beneficial-ownership
  - tipping-off
  - structuring-ctr
  - edd-approval
  - multi-jurisdiction
```

- [ ] **Step 4: Validate YAML parses**

Run: `python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workloads/**/*.yml', recursive=True)]; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add .github/workloads/
git commit -m "feat(e2e): add returns-triage + fsi-kyc-aml workload packs

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 8: Parameterize the e2e workflow with `workload`

**Files:**
- Modify: `.github/workflows/threadlight-e2e-foundry.yml`

- [ ] **Step 1: Add the `workload` input** (in `workflow_dispatch.inputs`, after `wire_api`)

```yaml
      workload:
        description: "Scenario pack to drive. 'returns-triage' = today's simple retail gate (default, unchanged). 'fsi-kyc-aml' = complex regulated KYC/AML."
        required: false
        default: "returns-triage"
        type: choice
        options:
          - returns-triage
          - fsi-kyc-aml
```

- [ ] **Step 2: Load the selected pack in the phase steps**

Replace each inline phase prompt with a read from the pack. Add a step that
exports the pack dir, and change each `run-copilot-phase.sh` invocation to read
the prompt from the pack via a tiny extractor:

```yaml
      - name: Resolve workload pack
        if: inputs.mode != 'smoke-only'
        run: |
          PACK="${{ github.workspace }}/.github/workloads/${{ inputs.workload }}"
          test -f "$PACK/phases.yml" || { echo "::error::missing pack $PACK"; exit 1; }
          echo "WORKLOAD_PACK=$PACK" >> "$GITHUB_ENV"
          # PoC dir name per workload
          case "${{ inputs.workload }}" in
            returns-triage) echo "PILOT_SUBDIR=returns-triage" >> "$GITHUB_ENV" ;;
            fsi-kyc-aml)    echo "PILOT_SUBDIR=kyc-aml"        >> "$GITHUB_ENV" ;;
          esac
```

Add a prompt extractor helper (next to `run-copilot-phase.sh`):

```yaml
      - name: Write pack prompt extractor
        if: inputs.mode != 'smoke-only'
        run: |
          cat > /tmp/pack-prompt.py <<'PY'
          import sys, os, yaml
          pack = os.environ["WORKLOAD_PACK"]
          phase = sys.argv[1]
          doc = yaml.safe_load(open(f"{pack}/phases.yml"))
          prompt = doc["phases"][phase]["prompt"]
          # expand ${E2E_WORKSPACE}
          print(os.path.expandvars(prompt))
          PY
```

Then each phase step becomes (example for design):

```yaml
      - name: Phase - design
        if: inputs.mode != 'smoke-only'
        env:
          COPILOT_PROVIDER_MODEL_ID: ${{ inputs.model_deployment }}
        run: |
          python3 /tmp/pack-prompt.py design \
            | /tmp/run-copilot-phase.sh design /tmp/phase-design.log
```

> Keep the existing gate-assertion steps but make their paths use
> `${E2E_WORKSPACE}/${PILOT_SUBDIR}` instead of the hard-coded
> `returns-triage`. For `returns-triage`, `PILOT_SUBDIR=returns-triage` so
> behavior is identical to today.

- [ ] **Step 3: Upload copilot logs for exact rounds**

In the artifact-upload step (or add one), include the copilot log dir:

```yaml
      - name: Upload copilot logs
        if: always() && inputs.mode != 'smoke-only'
        uses: actions/upload-artifact@v4
        with:
          name: copilot-logs-${{ github.run_id }}
          path: /tmp/copilot-logs/
          if-no-files-found: ignore
```

- [ ] **Step 4: Lint the workflow YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/threadlight-e2e-foundry.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 5: Smoke-validate the default path is unchanged**

Dispatch a `smoke-only` run on the default workload to confirm no regression:

```bash
gh workflow run threadlight-e2e-foundry.yml --repo aiappsgbb/threadlight-skills \
  --ref unsafecode-automatic-fiesta -f mode=smoke-only
# poll: gh run list --workflow threadlight-e2e-foundry.yml --limit 1
```
Expected: smoke-only run SUCCESS (registry discovery gate, ~30s).

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/threadlight-e2e-foundry.yml
git commit -m "feat(e2e): add workload input + pack loader + copilot-logs artifact

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 9: Infra — gpt-5.4 strong-arm deployment

**Files:**
- Create: `scripts/ci/foundry-strong-arm.sh`

- [ ] **Step 1: Write the idempotent create+verify script**

```bash
#!/usr/bin/env bash
# Create the gpt-5.4 strong-arm deployment on aif-shared-gbb-ci (idempotent).
set -euo pipefail
ACC=aif-shared-gbb-ci
RG=rg-shared-gbb-ci
NAME=gpt-5.4
MODEL=gpt-5.4
VERSION=2026-03-05         # verified catalog version (westus3/swedencentral)

if az cognitiveservices account deployment show \
     --name "$ACC" -g "$RG" --deployment-name "$NAME" >/dev/null 2>&1; then
  echo "deployment $NAME already exists"; exit 0
fi

echo "creating $NAME ($MODEL $VERSION)..."
az cognitiveservices account deployment create \
  --name "$ACC" -g "$RG" \
  --deployment-name "$NAME" \
  --model-name "$MODEL" --model-version "$VERSION" --model-format OpenAI \
  --sku-name GlobalStandard --sku-capacity 100

az cognitiveservices account deployment show \
  --name "$ACC" -g "$RG" --deployment-name "$NAME" \
  --query "{name:name, model:properties.model.name, ver:properties.model.version, cap:sku.capacity}" -o table
```

- [ ] **Step 2: Quota pre-check** (run before the script; document fallback)

Run:
```bash
az cognitiveservices usage list --location westus3 \
  --query "[?contains(name.value, 'GlobalStandard')].{name:name.value, used:currentValue, limit:limit}" -o table
```
If the gpt-5.4 GlobalStandard limit is 0 or fully used: surface clearly and skip
the strong arm (the matrix proceeds with mini + router; `validation_scorecard`
tolerates a missing strong arm — rounds-factor checks are skipped when strong is
absent).

- [ ] **Step 3: Run the script**

Run: `bash scripts/ci/foundry-strong-arm.sh`
Expected: a table row showing `gpt-5.4 / gpt-5.4 / 2026-03-05 / 100`.

- [ ] **Step 4: Commit**

```bash
git add scripts/ci/foundry-strong-arm.sh
git commit -m "chore(ci): idempotent gpt-5.4 strong-arm deployment script

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 10: Infra — router subset record/set/restore

**Files:**
- Create: `scripts/ci/router-subset.sh`

- [ ] **Step 1: Write the script** (uses the verified REST shape)

```bash
#!/usr/bin/env bash
# Record / set / restore the model-router custom subset on aif-shared-gbb-ci.
# Usage: router-subset.sh record|set|restore
set -euo pipefail
SUB=2c745a8f-9d37-45e3-8506-80797e89735e
RG=rg-shared-gbb-ci
ACC=aif-shared-gbb-ci
DEP=model-router
API=2025-10-01-preview
URL="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$ACC/deployments/$DEP?api-version=$API"
SNAP=/tmp/model-router-routing.snapshot.json
TOKEN() { az account get-access-token --resource https://management.azure.com --query accessToken -o tsv; }

case "${1:-}" in
  record)
    curl -s -H "Authorization: Bearer $(TOKEN)" "$URL" \
      | python3 -c "import sys,json;d=json.load(sys.stdin);json.dump(d.get('properties',{}).get('routing'),open('$SNAP','w'))"
    echo "recorded routing -> $SNAP"; cat "$SNAP"; echo ;;
  set)
    BODY=$(cat <<JSON
{ "sku": {"name":"GlobalStandard","capacity":1000},
  "properties": {
    "model": {"format":"OpenAI","name":"model-router","version":"2025-11-18"},
    "routing": { "mode":"balanced", "models": [
      {"format":"OpenAI","name":"gpt-5.4","version":"2026-03-05"},
      {"format":"OpenAI","name":"gpt-5.4-mini","version":"2026-03-17"}
    ]}}}
JSON
)
    curl -s -X PUT -H "Authorization: Bearer $(TOKEN)" \
      -H "Content-Type: application/json" -d "$BODY" "$URL" \
      | python3 -c "import sys,json;print(json.dumps(json.load(sys.stdin).get('properties',{}).get('routing'),indent=2))"
    echo "subset set to {gpt-5.4, gpt-5.4-mini}; propagation up to 5 min" ;;
  restore)
    # current account has NO routing block by default -> PUT without routing
    if [ -s "$SNAP" ] && [ "$(cat "$SNAP")" != "null" ]; then
      ROUTING=$(cat "$SNAP")
      BODY="{\"sku\":{\"name\":\"GlobalStandard\",\"capacity\":1000},\"properties\":{\"model\":{\"format\":\"OpenAI\",\"name\":\"model-router\",\"version\":\"2025-11-18\"},\"routing\":$ROUTING}}"
    else
      BODY='{"sku":{"name":"GlobalStandard","capacity":1000},"properties":{"model":{"format":"OpenAI","name":"model-router","version":"2025-11-18"}}}'
    fi
    curl -s -X PUT -H "Authorization: Bearer $(TOKEN)" \
      -H "Content-Type: application/json" -d "$BODY" "$URL" >/dev/null
    echo "restored prior routing (default full pool)" ;;
  *) echo "usage: $0 record|set|restore"; exit 2 ;;
esac
```

- [ ] **Step 2: Dry-run record (no mutation)**

Run: `bash scripts/ci/router-subset.sh record`
Expected: prints `null` (no routing block today) and writes the snapshot.

- [ ] **Step 3: Commit**

```bash
git add scripts/ci/router-subset.sh
git commit -m "chore(ci): record/set/restore model-router subset script

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 11: SKILL.md — document `validate` mode

**Files:**
- Modify: `skills/threadlight-router-bench/SKILL.md`

- [ ] **Step 1: Add a `validate` section** documenting:
  - the 6-run matrix (3 arms × 2 workloads), the 2-wave cost-clean orchestration,
  - the infra prerequisites (`foundry-strong-arm.sh`, `router-subset.sh set`/`restore`),
  - `python3 scripts/router_bench.py validate --dispatch` and `--ingest <manifest>`,
  - the 4 axes + verdict thresholds (rubric ≥ 0.8, rounds ≤ 1.5× strong),
  - the restore-the-subset guardrail and the n=1 caveat.

- [ ] **Step 2: Enforce the description-length guard**

Run: `python3 scripts/ci/check-skill-description-length.py skills/threadlight-router-bench/SKILL.md`
Expected: pass (≤1024 chars). If the front-matter `description` grew, trim it.

- [ ] **Step 3: Commit**

```bash
git add skills/threadlight-router-bench/SKILL.md
git commit -m "docs(router-bench): document validate matrix mode

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 12: Full suite + push + PR update

- [ ] **Step 1: Run the entire skill test suite**

Run: `cd skills/threadlight-router-bench && python3 -m pytest -q`
Expected: PASS (prior 30 + new rubric/rounds/validation/matrix/cli tests).

- [ ] **Step 2: Push and update PR #60** (or open a follow-up PR)

```bash
git push
gh pr view 60 --repo aiappsgbb/threadlight-skills --json url -q .url
```

- [ ] **Step 3: Confirm CI green** on the push (python-pytest incl. router-bench step).

---

## Task 13: Execute the validation matrix (the actual experiment)

> This task spends real Azure resources (~$6-25 tokens + infra, ~1.5h). Run
> deliberately, with the user's go-ahead.

- [ ] **Step 1: Stand up infra**

```bash
bash scripts/ci/foundry-strong-arm.sh          # gpt-5.4 deployment
bash scripts/ci/router-subset.sh record        # snapshot current routing
bash scripts/ci/router-subset.sh set           # {gpt-5.4, gpt-5.4-mini}
sleep 300                                       # subset propagation
```

- [ ] **Step 2: Dispatch the matrix**

```bash
python3 skills/threadlight-router-bench/scripts/router_bench.py validate \
  --dispatch --workloads returns-triage fsi-kyc-aml \
  --ref unsafecode-automatic-fiesta --out /tmp/router-validation
```
Expected: `matrix-manifest.json` with 6 cells (run IDs).

- [ ] **Step 3: Wait for completion, then ingest + score**

```bash
RID="/subscriptions/2c745a8f-9d37-45e3-8506-80797e89735e/resourceGroups/rg-shared-gbb-ci/providers/Microsoft.CognitiveServices/accounts/aif-shared-gbb-ci"
python3 skills/threadlight-router-bench/scripts/router_bench.py validate \
  --ingest /tmp/router-validation/matrix-manifest.json \
  --resource "$RID" --out /tmp/router-validation
cat /tmp/router-validation/router-validation.md
```

- [ ] **Step 4: Restore the shared router subset (always)**

```bash
bash scripts/ci/router-subset.sh restore
```

- [ ] **Step 5: Commit the findings** as a worked example under the skill:

```bash
mkdir -p skills/threadlight-router-bench/references/findings
cp /tmp/router-validation/router-validation.{json,md} \
   skills/threadlight-router-bench/references/findings/
git add skills/threadlight-router-bench/references/findings/
git commit -m "docs(router-bench): record router-validation matrix findings

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

- [ ] **Step 6: Report the verdict to the user** — per-workload (simple vs
  complex): does mini keep up, does the router close the gap, at what cost — with
  the n=1 caveat and any "re-run recommended" flags for close cells.

---

## Self-Review Notes

- **Spec coverage:** §3 matrix → Tasks 7-10,13; §4 axes → Tasks 1-4; §5 packs →
  Task 7; §6 infra → Tasks 9-10; §7 orchestration → Task 5; §8 skill extensions →
  Tasks 1-6,11; §9 testing → Tasks 1-6; §10 guardrails → Tasks 5 (wave guard),
  10 (restore), 8 (default-path safety).
- **No placeholders:** all model versions/REST bodies are the verified values;
  the one runtime-resolved item (latest router run ID) is polled, not hard-coded.
- **Type consistency:** arm dict shape `{arm, phases_ok, rounds, rubric,
  cost_usd}` is identical across `validation_scorecard`, `render_validation_matrix`,
  `_score_cell`, and the tests. Schema `threadlight-router-validation/v1` used
  consistently.
- **Known follow-up:** in Task 6 `_score_cell`, cost (`_cost_usd`) is attached by
  the caller from Azure Monitor per arm window; wire `metrics.fetch_metrics` +
  `score.cost_of` there during implementation (the manifest carries each cell's
  started/updated window from `gh run view`).
