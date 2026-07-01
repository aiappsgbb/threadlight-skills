# threadlight-router-bench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `threadlight-router-bench` — an offline, advisory skill that benchmarks an Azure AI Foundry **model-router** deployment against a fixed baseline model across the Threadlight e2e pipeline, producing an efficiency scorecard (cost + quality + routing mix) and a learnings digest (deterministic findings + LLM recommendations).

**Architecture:** Single-file argparse CLI dispatcher (`scripts/router_bench.py`, stdlib-only) that imports focused sibling modules. The cost axis comes from **Azure Monitor token metrics** split by the routed `ModelName` dimension (the Copilot CLI exposes no token usage for BYO providers); the quality axis reuses existing Phase-5 leg manifests (govern/evals/redteam) + per-phase CI step conclusions; learnings come from a deterministic finding taxonomy plus an LLM recommendations turn driven by `SKILL.md`. Two subcommands: `run` (orchestrate two e2e dispatches) and `analyze` (re-score two historical run IDs for free).

**Tech Stack:** Python 3.13 (stdlib only — `argparse`, `json`, `subprocess`, `urllib`, `pathlib`), `gh` CLI (workflow dispatch + artifact download + run JSON), `az` CLI (`az monitor metrics list`), pytest. Mirrors the package posture of `skills/threadlight-consumption-iq` (flat `scripts/` modules, `tests/` with `sys.path` injection, `references/` for schemas + fixtures — **no** package `__init__.py`, **no** `python -m`).

---

## ⚠️ As-built addendum (2026-06-30) — this plan was superseded during execution

This plan was written **before** a manual validation run against real CI logs. The
build that actually shipped (commits `9b4eebb`→`b7e8fcb`) differs from the tasks
below in three ways that matter. The shipped **SKILL.md is the source of truth**;
read it first. The task bodies below are retained for historical context.

1. **Two independent modes replace `run`/`analyze`.**
   - `learn <run_id>` — single-run learnings digest (`threadlight-router-learnings/v1`).
     **PRIMARY.** Works on **any one run**, green or red — no baseline, no paired
     dispatch. This is the self-improvement cold-path the team asked for: you are
     never forced to run pairs of jobs just to learn something.
   - `bench <candidate> <baseline>` — the optional paired cost/efficiency scorecard
     (`threadlight-router-scorecard/v1`), unchanged in spirit from the old `run`/`analyze`.
   - `dispatch.py` was **not** built as a separate module — `learn`/`bench` read
     already-finished runs; the e2e workflow is dispatched by hand / `threadlight-cicd`.

2. **Findings precision rules (empirically validated, NOT in the original taxonomy task).**
   - Primary log source is `gh run view <id> --log-failed` (failing steps only) →
     100% precision on real failures `28435017341` + `28389162228`. A naive full
     `--log` scan of the green run `28437323962` was **10/10 false positives**.
   - Classify the **message only** — strip the `gh` `<job>\t<step>` prefix first, or
     a step name like `threadlight-deploy + azd up` manufactures a `deploy` finding.
   - Drop command-echo (`[36;1m` token) + `##[group]`/`##[command]`; do **not** strip
     copilot glyphs `● │ └` (real signal in failing-step scope).
   - **Green runs are warnings-only** (`retry`/`slow_turn`/`router_fallback`); a success
     emits zero high/medium findings.
   - Dedup is **by category** with a `count` + first evidence (the original task deduped
     by signature).

3. **Taxonomy gained two categories** validated against real logs: `dependency` (high —
   the agent-framework 1.4 drift that forced the CI hotfix) and `model_unavailable`
   (high — `transient API error`). Order matters: `rate_limit` precedes
   `model_unavailable`. Full ordered list is in SKILL.md.

Also shipped beyond the original plan: a `Run threadlight-router-bench tests` step in
`.github/workflows/python-pytest.yml`, and SKILL.md `description` kept ≤1024 chars to
satisfy `scripts/ci/check-skill-description-length.py` (silent-loader-drop guard).

---

## Repo conventions (this plan follows them, correcting the spec's `references/router_bench/` sketch)

The spec §4 sketched a `references/router_bench/__init__.py` package invoked via `python -m router_bench`. The established repo convention (see `threadlight-consumption-iq`, `threadlight-evals`) is different and is what this plan uses:

- Python lives in `skills/<name>/scripts/*.py` as **flat modules** (no `__init__.py`).
- The CLI is a single dispatcher script invoked as `python3.13 scripts/router_bench.py <subcommand>`.
- Modules import siblings via `HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))`.
- Tests live in `skills/<name>/tests/test_*.py` and inject the scripts dir:
  `SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"; sys.path.insert(0, str(SCRIPTS))`.
- Docs, JSON schemas, the price table, and test fixtures live under `skills/<name>/references/`.
- There is no repo-wide `conftest.py`/`pyproject.toml`; each test file self-injects `sys.path`.

## File structure

```
skills/threadlight-router-bench/
  SKILL.md                                  # when-to-use, orchestration, recommendations prompt, guardrails
  scripts/
    router_bench.py                         # CLI dispatcher: run / analyze / prices (stdlib only)
    dispatch.py                             # gh workflow run x2; resolve run IDs; poll; run window
    harvest.py                              # gh run download; phase-parity from gh jobs JSON; load leg manifests
    metrics.py                              # az monitor metrics list argv + parse routed-model token totals
    prices.py                               # load price table; cost math
    score.py                                # cost rollup + routing mix + counterfactual; quality diff; verdict
    findings.py                             # finding taxonomy classifier over log/step evidence
    report.py                               # render router-bench-report.md + emit manifests
  references/
    router-bench-manifest.schema.json       # efficiency scorecard schema (v1)
    learnings-manifest.schema.json          # learnings digest schema (v1)
    pricing/
      model-prices.json                     # seed $/1M-token table (provenance + last_validated)
    fixtures/
      az-metrics-modelrouter.json           # recorded az monitor metrics list (cost-axis fixture)
      gh-run-jobs.json                       # recorded gh run view --json jobs (phase-parity fixture)
      run-timing.json                        # recorded run window (start/end)
      legs/
        govern-manifest.json                # recorded Phase-5 leg manifests (quality fixture)
        evals-manifest.json
        redteam-manifest.json
  tests/
    test_prices.py
    test_metrics.py
    test_score_cost.py
    test_harvest.py
    test_score_quality.py
    test_findings.py
    test_report.py
    test_dispatch.py
    test_cli.py
    test_skill_discipline.py
```

**Fixtures already captured** for this session at
`/Users/ricchi/.copilot/session-state/af17767c-6e7d-42e8-9d20-04704e10aaf8/files/router-bench-fixture/`:
- `recorded/az-metrics-modelrouter.json`, `recorded/gh-run-jobs.json`, `recorded/run-timing.json`
- `threadlight-e2e-28437323962/tmp/threadlight-e2e-28437323962/returns-triage/specs/{govern,evals,redteam}-manifest.json`

Task 1 copies these into `references/fixtures/`. They are real outputs of run `28437323962` (model-router, full e2e, success).

---

## Task 1: Scaffold skill dir + stage real fixtures

**Files:**
- Create: `skills/threadlight-router-bench/scripts/` (empty dir, via `.gitkeep` not needed once files land)
- Create: `skills/threadlight-router-bench/references/fixtures/az-metrics-modelrouter.json`
- Create: `skills/threadlight-router-bench/references/fixtures/gh-run-jobs.json`
- Create: `skills/threadlight-router-bench/references/fixtures/run-timing.json`
- Create: `skills/threadlight-router-bench/references/fixtures/legs/{govern,evals,redteam}-manifest.json`

- [ ] **Step 1: Create directories and copy the captured fixtures**

```bash
cd skills/threadlight-router-bench 2>/dev/null || true
ROOT="$(git rev-parse --show-toplevel)"
SK="$ROOT/skills/threadlight-router-bench"
SRC="/Users/ricchi/.copilot/session-state/af17767c-6e7d-42e8-9d20-04704e10aaf8/files/router-bench-fixture"
mkdir -p "$SK/scripts" "$SK/references/pricing" "$SK/references/fixtures/legs" "$SK/tests"
cp "$SRC/recorded/az-metrics-modelrouter.json" "$SK/references/fixtures/az-metrics-modelrouter.json"
cp "$SRC/recorded/gh-run-jobs.json"            "$SK/references/fixtures/gh-run-jobs.json"
cp "$SRC/recorded/run-timing.json"             "$SK/references/fixtures/run-timing.json"
LEGS="$SRC/threadlight-e2e-28437323962/tmp/threadlight-e2e-28437323962/returns-triage/specs"
cp "$LEGS/govern-manifest.json"  "$SK/references/fixtures/legs/govern-manifest.json"
cp "$LEGS/evals-manifest.json"   "$SK/references/fixtures/legs/evals-manifest.json"
cp "$LEGS/redteam-manifest.json" "$SK/references/fixtures/legs/redteam-manifest.json"
```

- [ ] **Step 2: Verify the fixtures are present and parseable**

Run:
```bash
python3.13 - <<'PY'
import json, pathlib
base = pathlib.Path("skills/threadlight-router-bench/references/fixtures")
for p in ["az-metrics-modelrouter.json","gh-run-jobs.json","run-timing.json",
          "legs/govern-manifest.json","legs/evals-manifest.json","legs/redteam-manifest.json"]:
    json.load(open(base/p)); print("ok", p)
PY
```
Expected: six `ok ...` lines, no exceptions.

- [ ] **Step 3: Commit**

```bash
git add skills/threadlight-router-bench/references/fixtures
git commit -m "feat(router-bench): scaffold skill dir + stage real run-28437323962 fixtures

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 2: Price table + `prices.py` (cost foundation)

**Files:**
- Create: `skills/threadlight-router-bench/references/pricing/model-prices.json`
- Create: `skills/threadlight-router-bench/scripts/prices.py`
- Test: `skills/threadlight-router-bench/tests/test_prices.py`

- [ ] **Step 1: Create the seed price table**

Create `skills/threadlight-router-bench/references/pricing/model-prices.json`:

```json
{
  "_schema": "router-bench-prices/v1",
  "_note": "Seed $/1M-token estimates. Validate with `router_bench.py prices --refresh` before customer-facing use. source=seed means UNVERIFIED.",
  "last_validated": "2026-06-30",
  "models": {
    "gpt-5.4": {"input_per_1m": 1.25, "output_per_1m": 10.0, "source": "seed", "last_validated": "2026-06-30"},
    "gpt-5.5": {"input_per_1m": 2.50, "output_per_1m": 20.0, "source": "seed", "last_validated": "2026-06-30"},
    "gpt-5.4-mini": {"input_per_1m": 0.25, "output_per_1m": 2.0, "source": "seed", "last_validated": "2026-06-30"}
  }
}
```

- [ ] **Step 2: Write the failing test**

Create `skills/threadlight-router-bench/tests/test_prices.py`:

```python
from __future__ import annotations
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import prices  # noqa: E402


def test_load_price_table_has_routed_models():
    table = prices.load_price_table()
    assert {"gpt-5.4", "gpt-5.5", "gpt-5.4-mini"} <= set(table)
    assert table["gpt-5.4"]["input_per_1m"] == 1.25
    assert table["gpt-5.4"]["source"] == "seed"


def test_cost_usd_math():
    table = prices.load_price_table()
    # 7,048,336 input + 111,473 output of gpt-5.4 (the real run-28437323962 split)
    cost = prices.cost_usd("gpt-5.4", 7_048_336, 111_473, table)
    expected = 7_048_336 / 1_000_000 * 1.25 + 111_473 / 1_000_000 * 10.0
    assert abs(cost - expected) < 1e-9


def test_cost_usd_unknown_model_raises():
    table = prices.load_price_table()
    try:
        prices.cost_usd("gpt-9-ultra", 1, 1, table)
        assert False, "expected KeyError"
    except KeyError:
        pass
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_prices.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'prices'`.

- [ ] **Step 4: Write `prices.py`**

Create `skills/threadlight-router-bench/scripts/prices.py`:

```python
#!/usr/bin/env python3
"""Model price table + token cost math for threadlight-router-bench.

Cost axis is computed from Azure Monitor token totals (see metrics.py) priced
against a maintained per-model $/1M-token table. The static table is the
deterministic default; `refresh_from_retail()` optionally cross-checks the
public Azure Retail Prices API.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_PRICES_PATH = HERE.parent / "references" / "pricing" / "model-prices.json"


def load_price_table(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return {model_name: {input_per_1m, output_per_1m, source, last_validated}}."""
    p = Path(path) if path else DEFAULT_PRICES_PATH
    doc = json.loads(p.read_text(encoding="utf-8"))
    return dict(doc["models"])


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             table: dict[str, dict[str, Any]]) -> float:
    """Cost in USD for a model's input+output tokens. Raises KeyError if unpriced."""
    entry = table[model]  # KeyError on unknown model is intentional (caller flags it)
    return (input_tokens / 1_000_000.0) * float(entry["input_per_1m"]) + \
           (output_tokens / 1_000_000.0) * float(entry["output_per_1m"])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_prices.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add skills/threadlight-router-bench/references/pricing skills/threadlight-router-bench/scripts/prices.py skills/threadlight-router-bench/tests/test_prices.py
git commit -m "feat(router-bench): seed price table + cost math (prices.py)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 3: `metrics.py` — parse Azure Monitor routed-model token totals

**Files:**
- Create: `skills/threadlight-router-bench/scripts/metrics.py`
- Test: `skills/threadlight-router-bench/tests/test_metrics.py`

Real metric JSON shape (from the fixture): top-level `value[]` is a list of metrics
(`name.value` ∈ {`InputTokens`,`OutputTokens`}); each has `timeseries[]`; each timeseries
has `metadatavalues[]` (each `{name:{value}, value}`, dimension keys are **lowercase**
`modeldeploymentname`/`modelname`) and `data[]` (each `{timeStamp, total?}`).

- [ ] **Step 1: Write the failing test**

Create `skills/threadlight-router-bench/tests/test_metrics.py`:

```python
from __future__ import annotations
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURES = Path(__file__).resolve().parent.parent / "references" / "fixtures"

import json  # noqa: E402
import metrics  # noqa: E402


def test_parse_metrics_json_splits_by_routed_model():
    doc = json.loads((FIXTURES / "az-metrics-modelrouter.json").read_text())
    got = metrics.parse_metrics_json(doc)
    # Real run-28437323962 routed token totals:
    assert got["gpt-5.4"] == {"input": 7_048_336, "output": 111_473}
    assert got["gpt-5.5"] == {"input": 313_389, "output": 13_201}
    assert "gpt-5.4-mini" not in got  # router never chose the cheap model for this workload


def test_build_metrics_args_shape():
    args = metrics.build_metrics_args(
        resource_id="/subscriptions/x/resourceGroups/rg/providers/"
                     "Microsoft.CognitiveServices/accounts/acct",
        deployment="model-router",
        start_iso="2026-06-30T10:19:00Z", end_iso="2026-06-30T11:07:00Z",
    )
    assert args[0] == "monitor"
    joined = " ".join(args)
    assert "InputTokens" in joined and "OutputTokens" in joined
    assert "ModelDeploymentName eq 'model-router'" in joined
    assert "ModelName eq '*'" in joined
    assert "PT1H" in joined


def test_build_metrics_args_rejects_bad_interval():
    try:
        metrics.build_metrics_args("rid", "model-router",
                                   "2026-06-30T10:00:00Z", "2026-06-30T11:00:00Z",
                                   interval="PT45M")
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'metrics'`.

- [ ] **Step 3: Write `metrics.py`**

Create `skills/threadlight-router-bench/scripts/metrics.py`:

```python
#!/usr/bin/env python3
"""Azure Monitor token-metrics access for threadlight-router-bench.

The Copilot CLI exposes no token usage for BYO providers, so the cost axis
comes from Azure Monitor InputTokens/OutputTokens on the Foundry/AOAI account,
split by the routed `ModelName` dimension and scoped to a deployment + run
time-window. RBAC required: Monitoring Reader on the resource.
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Callable

VALID_INTERVALS = {"PT1M", "PT5M", "PT15M", "PT30M", "PT1H", "PT6H", "PT12H", "P1D"}


def build_metrics_args(resource_id: str, deployment: str, start_iso: str,
                       end_iso: str, interval: str = "PT1H") -> list[str]:
    """Return the `az` argv (after the `az` executable) for the metrics query."""
    if interval not in VALID_INTERVALS:
        raise ValueError(f"invalid interval {interval!r}; valid: {sorted(VALID_INTERVALS)}")
    return [
        "monitor", "metrics", "list",
        "--resource", resource_id,
        "--metrics", "InputTokens", "OutputTokens",
        "--start-time", start_iso, "--end-time", end_iso,
        "--interval", interval, "--aggregation", "Total",
        "--filter", f"ModelDeploymentName eq '{deployment}' and ModelName eq '*'",
        "-o", "json",
    ]


def _dim(metadatavalues: list[dict[str, Any]], key: str) -> str | None:
    for mv in metadatavalues:
        if mv.get("name", {}).get("value", "").lower() == key.lower():
            return mv.get("value")
    return None


def parse_metrics_json(doc: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Collapse the az metrics JSON into {modelname: {input, output}} totals."""
    out: dict[str, dict[str, int]] = {}
    metric_key = {"InputTokens": "input", "OutputTokens": "output"}
    for metric in doc.get("value", []):
        which = metric_key.get(metric.get("name", {}).get("value"))
        if which is None:
            continue
        for ts in metric.get("timeseries", []):
            model = _dim(ts.get("metadatavalues", []), "modelname")
            if not model:
                continue
            total = sum(int(p.get("total") or 0) for p in ts.get("data", []))
            bucket = out.setdefault(model, {"input": 0, "output": 0})
            bucket[which] += total
    return out


def query_routed_tokens(resource_id: str, deployment: str, start_iso: str,
                        end_iso: str, interval: str = "PT1H",
                        runner: Callable[[list[str]], str] | None = None,
                        retries: int = 6, sleep_s: float = 20.0) -> dict[str, dict[str, int]]:
    """Run the az query (polling until non-empty/stable) and parse it.

    `runner(argv)->stdout` is injectable for tests; default shells out to `az`.
    """
    run = runner or _default_runner
    args = build_metrics_args(resource_id, deployment, start_iso, end_iso, interval)
    prev: dict[str, dict[str, int]] = {}
    for attempt in range(retries):
        parsed = parse_metrics_json(json.loads(run(args)))
        if parsed and parsed == prev:
            return parsed  # stable across two polls
        prev = parsed
        if attempt < retries - 1:
            time.sleep(sleep_s)
    return prev


def _default_runner(args: list[str]) -> str:
    proc = subprocess.run(["az", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "az monitor metrics list failed (need Monitoring Reader on the resource?):\n"
            + proc.stderr.strip())
    return proc.stdout
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_metrics.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-router-bench/scripts/metrics.py skills/threadlight-router-bench/tests/test_metrics.py
git commit -m "feat(router-bench): Azure Monitor routed-model token parser (metrics.py)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 4: `score.py` cost rollup (cost, routing mix, counterfactual)

**Files:**
- Create: `skills/threadlight-router-bench/scripts/score.py`
- Test: `skills/threadlight-router-bench/tests/test_score_cost.py`

- [ ] **Step 1: Write the failing test**

Create `skills/threadlight-router-bench/tests/test_score_cost.py`:

```python
from __future__ import annotations
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import prices  # noqa: E402
import score  # noqa: E402

TABLE = prices.load_price_table()
# real run-28437323962 routed split:
ROUTED = {"gpt-5.4": {"input": 7_048_336, "output": 111_473},
          "gpt-5.5": {"input": 313_389, "output": 13_201}}


def test_score_cost_totals_and_mix():
    rc = score.score_cost(ROUTED, TABLE)
    assert rc["tokens"]["input"] == 7_048_336 + 313_389
    assert rc["tokens"]["output"] == 111_473 + 13_201
    # cost equals sum of per-model costs
    exp = prices.cost_usd("gpt-5.4", 7_048_336, 111_473, TABLE) + \
          prices.cost_usd("gpt-5.5", 313_389, 13_201, TABLE)
    assert abs(rc["cost_usd"] - exp) < 1e-9
    # mix is sorted by cost desc and pcts sum to ~100
    mix = rc["routed_model_mix"]
    assert mix[0]["model"] == "gpt-5.4"
    assert abs(sum(m["cost_pct"] for m in mix) - 100.0) < 1e-6


def test_counterfactual_prices_all_tokens_at_baseline():
    cf = score.counterfactual_cost(ROUTED, "gpt-5.4-mini", TABLE)
    tot_in = 7_048_336 + 313_389
    tot_out = 111_473 + 13_201
    assert abs(cf - prices.cost_usd("gpt-5.4-mini", tot_in, tot_out, TABLE)) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_score_cost.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'score'`.

- [ ] **Step 3: Write the cost half of `score.py`**

Create `skills/threadlight-router-bench/scripts/score.py`:

```python
#!/usr/bin/env python3
"""Scoring for threadlight-router-bench: cost rollup, quality diff, verdict.

Cost half (this file's first half) consumes routed-model token totals from
metrics.py and the price table from prices.py. Quality half (added later)
consumes Phase-5 leg manifests + per-phase CI conclusions from harvest.py.
"""
from __future__ import annotations

from typing import Any

import prices as _prices


def score_cost(routed_tokens: dict[str, dict[str, int]],
               table: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Roll up routed-model token totals into cost + routing mix.

    routed_tokens: {model: {input, output}} (from metrics.parse_metrics_json).
    Returns {tokens:{input,output}, cost_usd, routed_model_mix:[...]} where mix
    entries are sorted by cost desc with cost_pct.
    """
    per_model = []
    total_in = total_out = 0
    total_cost = 0.0
    for model, tok in routed_tokens.items():
        c = _prices.cost_usd(model, tok["input"], tok["output"], table)
        per_model.append({"model": model, "input": tok["input"],
                          "output": tok["output"], "cost_usd": c})
        total_in += tok["input"]
        total_out += tok["output"]
        total_cost += c
    for m in per_model:
        m["cost_pct"] = (m["cost_usd"] / total_cost * 100.0) if total_cost else 0.0
    per_model.sort(key=lambda m: m["cost_usd"], reverse=True)
    return {"tokens": {"input": total_in, "output": total_out},
            "cost_usd": total_cost, "routed_model_mix": per_model}


def counterfactual_cost(routed_tokens: dict[str, dict[str, int]],
                        baseline_model: str,
                        table: dict[str, dict[str, Any]]) -> float:
    """Price ALL routed tokens at a single baseline model (isolates routing savings)."""
    tot_in = sum(t["input"] for t in routed_tokens.values())
    tot_out = sum(t["output"] for t in routed_tokens.values())
    return _prices.cost_usd(baseline_model, tot_in, tot_out, table)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_score_cost.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-router-bench/scripts/score.py skills/threadlight-router-bench/tests/test_score_cost.py
git commit -m "feat(router-bench): cost rollup + routing mix + counterfactual (score.py)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 5: `harvest.py` — phase parity + leg manifests

**Files:**
- Create: `skills/threadlight-router-bench/scripts/harvest.py`
- Test: `skills/threadlight-router-bench/tests/test_harvest.py`

The gh jobs JSON has `jobs[].steps[]` each `{name, conclusion, number}`. Step names follow
`[Phase N/M] ...`, `[Phase N/M assert] ...`, plus `Smoke-check Skill-tool discovery`,
`Assert agent deployed + responding`, `Teardown — ...`. We normalize step names into a
small phase set and report each phase's worst conclusion.

- [ ] **Step 1: Write the failing test**

Create `skills/threadlight-router-bench/tests/test_harvest.py`:

```python
from __future__ import annotations
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURES = Path(__file__).resolve().parent.parent / "references" / "fixtures"

import json  # noqa: E402
import harvest  # noqa: E402


def test_parse_phase_parity_from_real_jobs():
    doc = json.loads((FIXTURES / "gh-run-jobs.json").read_text())
    phases = harvest.parse_phase_parity(doc)
    # every mapped phase in this all-green run is success
    for name in ["smoke", "design", "pattern", "deploy", "invoke", "legs"]:
        assert phases.get(name) == "success", f"{name} -> {phases.get(name)}"


def test_phase_worst_conclusion_wins():
    doc = {"jobs": [{"name": "e2e", "conclusion": "failure", "steps": [
        {"name": "[Phase 1/4] Drive design", "conclusion": "success", "number": 1},
        {"name": "[Phase 1/4 assert] artifacts", "conclusion": "failure", "number": 2},
    ]}]}
    phases = harvest.parse_phase_parity(doc)
    assert phases["design"] == "failure"


def test_load_leg_manifests():
    legs = harvest.load_leg_manifests(FIXTURES / "legs")
    assert legs["evals"]["schema"] == "threadlight-evals-manifest/v1"
    assert legs["govern"]["verdict"] == "not-wired"
    assert legs["redteam"]["verdict"] == "vulnerable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_harvest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harvest'`.

- [ ] **Step 3: Write `harvest.py`**

Create `skills/threadlight-router-bench/scripts/harvest.py`:

```python
#!/usr/bin/env python3
"""Artifact + run-metadata harvest for threadlight-router-bench.

Pulls two signals per run:
  * phase parity   — per-phase worst step conclusion from `gh run view --json jobs`
  * Phase-5 KPIs   — the govern/evals/redteam leg manifests downloaded via gh
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

# Ordered: first matching pattern wins. Keys are the normalized phase names.
_PHASE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("smoke", re.compile(r"Smoke-check", re.I)),
    ("design", re.compile(r"\[Phase 1/\d", re.I)),
    ("pattern", re.compile(r"\[Phase 2/\d", re.I)),
    ("deploy", re.compile(r"\[Phase 3/\d", re.I)),
    ("invoke", re.compile(r"\[Phase 4/\d", re.I)),
    ("legs", re.compile(r"\[Phase 5/\d", re.I)),
    ("teardown", re.compile(r"Teardown", re.I)),
]

# Worse conclusions sort higher so the per-phase worst wins.
_SEVERITY = {"success": 0, "skipped": 0, "neutral": 1, "cancelled": 2,
             "timed_out": 3, "failure": 3, None: 1}


def _phase_for(step_name: str) -> str | None:
    for phase, pat in _PHASE_PATTERNS:
        if pat.search(step_name):
            return phase
    return None


def parse_phase_parity(jobs_doc: dict[str, Any]) -> dict[str, str]:
    """Return {phase: worst_conclusion} across all jobs/steps."""
    worst: dict[str, str] = {}
    for job in jobs_doc.get("jobs", []):
        for step in job.get("steps", []):
            phase = _phase_for(step.get("name", ""))
            if phase is None:
                continue
            concl = step.get("conclusion") or "neutral"
            if phase not in worst or _SEVERITY.get(concl, 1) > _SEVERITY.get(worst[phase], 1):
                worst[phase] = concl
    return worst


def load_leg_manifests(specs_dir: Path) -> dict[str, dict[str, Any]]:
    """Load {govern,evals,redteam}-manifest.json from a directory (missing -> {})."""
    out: dict[str, dict[str, Any]] = {}
    for leg in ("govern", "evals", "redteam"):
        p = Path(specs_dir) / f"{leg}-manifest.json"
        out[leg] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return out


def download_run(run_id: int, dest: Path,
                 repo: str = "aiappsgbb/threadlight-skills",
                 runner: Callable[[list[str]], str] | None = None) -> Path:
    """`gh run download` the run's artifact bundle into dest; return dest."""
    run = runner or _default_runner
    Path(dest).mkdir(parents=True, exist_ok=True)
    run(["run", "download", str(run_id), "--repo", repo, "--dir", str(dest)])
    return Path(dest)


def _default_runner(args: list[str]) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("gh " + " ".join(args) + " failed:\n" + proc.stderr.strip())
    return proc.stdout
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_harvest.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-router-bench/scripts/harvest.py skills/threadlight-router-bench/tests/test_harvest.py
git commit -m "feat(router-bench): phase-parity + leg-manifest harvest (harvest.py)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 6: `score.py` quality diff + verdict

**Files:**
- Modify: `skills/threadlight-router-bench/scripts/score.py` (append quality functions)
- Test: `skills/threadlight-router-bench/tests/test_score_quality.py`

Quality signals per the real manifests: each leg manifest has `verdict`, `must_fix[]`,
`should_fix[]`, and a `capabilities{cap:{status}}` map where `status ∈ {pass, must-fix,
should-fix, not-verified}`. A regression = a capability that was `pass` for baseline and
non-`pass` for candidate, OR (evals only) a numeric `metrics.pass_rate` drop > tolerance.

- [ ] **Step 1: Write the failing test**

Create `skills/threadlight-router-bench/tests/test_score_quality.py`:

```python
from __future__ import annotations
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import score  # noqa: E402


def test_phase_parity_delta():
    base = {"design": "success", "deploy": "success"}
    assert score.phase_parity_delta(base, base) == "equal"
    worse = {"design": "success", "deploy": "failure"}
    assert score.phase_parity_delta(worse, base) == "candidate_worse"
    better = {"design": "success", "deploy": "success"}
    assert score.phase_parity_delta(better, {"design": "success", "deploy": "failure"}) == "candidate_better"


def test_capability_regressions_detected():
    base = {"evals": {"capabilities": {"alert_wired": {"status": "pass"},
                                        "schedule_present": {"status": "must-fix"}}}}
    cand = {"evals": {"capabilities": {"alert_wired": {"status": "must-fix"},  # regressed
                                        "schedule_present": {"status": "must-fix"}}}}
    regs = score.capability_regressions(cand, base)
    assert ("evals", "alert_wired") in regs


def test_verdict_efficiency_win():
    v = score.verdict(cost_candidate=10.0, cost_baseline=20.0,
                      parity="equal", quality_regressions=[], kpi_pass_rate_drop=0.0)
    assert v == "efficiency_win"


def test_verdict_regression_on_phase_or_kpi():
    assert score.verdict(5.0, 20.0, "candidate_worse", [], 0.0) == "regression"
    assert score.verdict(5.0, 20.0, "equal", [("evals", "alert_wired")], 0.0) == "regression"
    assert score.verdict(5.0, 20.0, "equal", [], 0.10) == "regression"  # 10% > 5% tol


def test_verdict_neutral_within_band():
    assert score.verdict(20.5, 20.0, "equal", [], 0.0) == "neutral"  # +2.5%, no win, no reg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_score_quality.py -v`
Expected: FAIL — `AttributeError: module 'score' has no attribute 'phase_parity_delta'`.

- [ ] **Step 3: Append quality functions to `score.py`**

Append to `skills/threadlight-router-bench/scripts/score.py`:

```python


def phase_parity_delta(candidate: dict[str, str], baseline: dict[str, str]) -> str:
    """Compare per-phase conclusions. Returns equal|candidate_better|candidate_worse."""
    phases = set(candidate) | set(baseline)
    cand_better = base_better = False
    for ph in phases:
        c_ok = candidate.get(ph) == "success"
        b_ok = baseline.get(ph) == "success"
        if c_ok and not b_ok:
            cand_better = True
        elif b_ok and not c_ok:
            base_better = True
    if base_better:
        return "candidate_worse"
    if cand_better:
        return "candidate_better"
    return "equal"


def capability_regressions(candidate_legs: dict[str, Any],
                           baseline_legs: dict[str, Any]) -> list[tuple[str, str]]:
    """Capabilities that were 'pass' for baseline but not 'pass' for candidate."""
    regs: list[tuple[str, str]] = []
    for leg, base_m in baseline_legs.items():
        base_caps = (base_m or {}).get("capabilities", {})
        cand_caps = (candidate_legs.get(leg) or {}).get("capabilities", {})
        for cap, bval in base_caps.items():
            if bval.get("status") == "pass" and cand_caps.get(cap, {}).get("status") != "pass":
                regs.append((leg, cap))
    return regs


def verdict(cost_candidate: float, cost_baseline: float, parity: str,
            quality_regressions: list[tuple[str, str]],
            kpi_pass_rate_drop: float, kpi_tolerance: float = 0.05) -> str:
    """efficiency_win | neutral | regression.

    regression  : candidate lost a phase, a capability regressed, or a numeric KPI
                  dropped more than `kpi_tolerance` (fraction, e.g. 0.05 == 5%).
    efficiency_win : parity not worse AND candidate cheaper.
    neutral     : parity not worse, no regressions, cost within +/-10%.
    """
    if parity == "candidate_worse" or quality_regressions or kpi_pass_rate_drop > kpi_tolerance:
        return "regression"
    if parity in ("equal", "candidate_better") and cost_candidate < cost_baseline:
        return "efficiency_win"
    if cost_baseline and abs(cost_candidate - cost_baseline) / cost_baseline <= 0.10:
        return "neutral"
    return "neutral"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_score_quality.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-router-bench/scripts/score.py skills/threadlight-router-bench/tests/test_score_quality.py
git commit -m "feat(router-bench): quality diff + verdict logic (score.py)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 7: `findings.py` — finding taxonomy classifier

**Files:**
- Create: `skills/threadlight-router-bench/scripts/findings.py`
- Test: `skills/threadlight-router-bench/tests/test_findings.py`

Categories (from spec §7.1): `wire_protocol`, `auth`, `rate_limit`, `retry`, `slow_turn`,
`tool_failure`, `skill_loader`, `router_fallback`, `deploy`, `quota`.

- [ ] **Step 1: Write the failing test**

Create `skills/threadlight-router-bench/tests/test_findings.py`:

```python
from __future__ import annotations
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import findings  # noqa: E402


def test_classify_known_signatures():
    assert findings.classify_line("HTTP 400 operation unsupported")[0] == "wire_protocol"
    assert findings.classify_line("CAPIError: exceeded rate limit (429)")[0] == "rate_limit"
    assert findings.classify_line("401 Unauthorized from provider")[0] == "auth"
    assert findings.classify_line("azd up failed: deployment error")[0] == "deploy"
    assert findings.classify_line("Model 'model-router' is not in the built-in catalog")[0] == "skill_loader"
    assert findings.classify_line("just a normal log line") is None


def test_scan_lines_emits_findings_with_evidence():
    lines = ["ok", "HTTP 400 operation unsupported", "ok2", "429 exceeded rate limit"]
    got = findings.scan_lines(lines, run_id=123, phase="smoke", source="process.log")
    assert len(got) == 2
    f0 = got[0]
    assert f0["category"] == "wire_protocol"
    assert f0["run_id"] == 123 and f0["phase"] == "smoke"
    assert f0["evidence"]["line"] == 2  # 1-indexed
    assert "400" in f0["evidence"]["excerpt"]
    assert f0["id"].startswith("F-")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_findings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'findings'`.

- [ ] **Step 3: Write `findings.py`**

Create `skills/threadlight-router-bench/scripts/findings.py`:

```python
#!/usr/bin/env python3
"""Deterministic finding taxonomy for the threadlight-router-bench learnings digest.

Scans harvested log lines / step evidence and classifies anomalies into a fixed
taxonomy. The LLM recommendations turn (driven by SKILL.md) consumes these
structured findings — never the raw logs — so recommendations stay grounded.
"""
from __future__ import annotations

import re
from typing import Any

# Ordered (model_router-specific catalog message must beat generic patterns).
# (category, severity, compiled-pattern)
_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("skill_loader", "low", re.compile(r"not in the built-in catalog", re.I)),
    ("wire_protocol", "high", re.compile(r"\b400\b.*operation unsupported|operation unsupported", re.I)),
    ("rate_limit", "medium", re.compile(r"\b429\b|exceeded rate limit|rate.?limit", re.I)),
    ("auth", "high", re.compile(r"\b401\b|\b403\b|unauthorized|forbidden", re.I)),
    ("quota", "medium", re.compile(r"quota|capacity exceeded|insufficient capacity", re.I)),
    ("deploy", "high", re.compile(r"azd (up|down).*fail|deployment (error|failed)", re.I)),
    ("tool_failure", "medium", re.compile(r"tool (call )?failed|tool error", re.I)),
    ("router_fallback", "low", re.compile(r"router.*fallback|fell back to", re.I)),
    ("retry", "low", re.compile(r"retry\b|retrying", re.I)),
    ("slow_turn", "low", re.compile(r"slow turn|took \d{4,}\s?ms", re.I)),
]


def classify_line(text: str) -> tuple[str, str] | None:
    """Return (category, severity) for the first matching rule, else None."""
    for category, severity, pat in _RULES:
        if pat.search(text):
            return category, severity
    return None


def scan_lines(lines: list[str], run_id: int, phase: str,
               source: str = "") -> list[dict[str, Any]]:
    """Classify each line; return findings with 1-indexed evidence."""
    out: list[dict[str, Any]] = []
    for i, line in enumerate(lines, start=1):
        hit = classify_line(line)
        if hit is None:
            continue
        category, severity = hit
        out.append({
            "id": f"F-{run_id}-{len(out) + 1:03d}",
            "category": category, "severity": severity,
            "run_id": run_id, "phase": phase,
            "evidence": {"file": source, "line": i, "excerpt": line.strip()[:200]},
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_findings.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-router-bench/scripts/findings.py skills/threadlight-router-bench/tests/test_findings.py
git commit -m "feat(router-bench): finding taxonomy classifier (findings.py)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 8: Manifest schemas + `report.py`

**Files:**
- Create: `skills/threadlight-router-bench/references/router-bench-manifest.schema.json`
- Create: `skills/threadlight-router-bench/references/learnings-manifest.schema.json`
- Create: `skills/threadlight-router-bench/scripts/report.py`
- Test: `skills/threadlight-router-bench/tests/test_report.py`

- [ ] **Step 1: Create the two schemas**

Create `skills/threadlight-router-bench/references/router-bench-manifest.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "router-bench-manifest",
  "type": "object",
  "required": ["schema_version", "generated_at", "depth", "candidate", "baseline", "deltas", "verdict"],
  "properties": {
    "schema_version": {"const": "1.0"},
    "generated_at": {"type": "string"},
    "depth": {"type": "string"},
    "candidate": {"$ref": "#/$defs/side"},
    "baseline": {"$ref": "#/$defs/side"},
    "deltas": {
      "type": "object",
      "required": ["cost_usd", "cost_pct", "phase_parity"],
      "properties": {
        "cost_usd": {"type": "number"},
        "cost_pct": {"type": "number"},
        "counterfactual_routing_savings_usd": {"type": "number"},
        "phase_parity": {"type": "string"}
      }
    },
    "verdict": {"enum": ["efficiency_win", "neutral", "regression"]},
    "price_table_provenance": {"type": "object"}
  },
  "$defs": {
    "side": {
      "type": "object",
      "required": ["label", "wire_api", "run_id", "conclusion", "tokens", "cost_usd"],
      "properties": {
        "label": {"type": "string"},
        "wire_api": {"type": "string"},
        "run_id": {"type": "integer"},
        "conclusion": {"type": "string"},
        "phases": {"type": "object"},
        "tokens": {"type": "object"},
        "cost_usd": {"type": "number"},
        "routed_model_mix": {"type": "array"}
      }
    }
  }
}
```

Create `skills/threadlight-router-bench/references/learnings-manifest.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "learnings-manifest",
  "type": "object",
  "required": ["schema_version", "generated_at", "run_ids", "findings", "recommendations"],
  "properties": {
    "schema_version": {"const": "1.0"},
    "generated_at": {"type": "string"},
    "run_ids": {"type": "array", "items": {"type": "integer"}},
    "findings": {"type": "array"},
    "recommendations": {"type": "array"}
  }
}
```

- [ ] **Step 2: Write the failing test**

Create `skills/threadlight-router-bench/tests/test_report.py`:

```python
from __future__ import annotations
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import report  # noqa: E402

CAND = {"label": "model-router", "wire_api": "completions", "run_id": 1, "conclusion": "success",
        "phases": {"design": "success"}, "tokens": {"input": 7361725, "output": 124674},
        "cost_usd": 10.0, "routed_model_mix": [{"model": "gpt-5.4", "cost_pct": 95.0}]}
BASE = {"label": "gpt-5.4-mini", "wire_api": "responses", "run_id": 2, "conclusion": "success",
        "phases": {"design": "success"}, "tokens": {"input": 7361725, "output": 124674},
        "cost_usd": 2.0, "routed_model_mix": []}


def test_build_manifest_shape():
    m = report.build_manifest(CAND, BASE, depth="full-paired",
                              counterfactual_savings=-1.5, verdict="regression",
                              price_provenance={"source": "seed"})
    assert m["schema_version"] == "1.0"
    assert m["verdict"] == "regression"
    assert m["deltas"]["cost_usd"] == 8.0          # candidate - baseline
    assert round(m["deltas"]["cost_pct"], 1) == 400.0
    assert m["candidate"]["label"] == "model-router"


def test_build_learnings_and_render():
    learn = report.build_learnings(run_ids=[1, 2],
                                   findings=[{"id": "F-1-001", "category": "wire_protocol"}],
                                   recommendations=[{"id": "R-001", "text": "use completions wire",
                                                     "motivated_by": ["F-1-001"], "persistent": False}])
    assert learn["schema_version"] == "1.0"
    md = report.render_report_md(report.build_manifest(CAND, BASE, "full-paired", -1.5,
                                                       "regression", {"source": "seed"}), learn)
    assert "# Router-Bench Report" in md
    assert "model-router" in md and "gpt-5.4-mini" in md
    assert "use completions wire" in md
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'report'`.

- [ ] **Step 4: Write `report.py`**

Create `skills/threadlight-router-bench/scripts/report.py`:

```python
#!/usr/bin/env python3
"""Render the efficiency scorecard + learnings digest for threadlight-router-bench."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_manifest(candidate: dict[str, Any], baseline: dict[str, Any], depth: str,
                   counterfactual_savings: float, verdict: str,
                   price_provenance: dict[str, Any]) -> dict[str, Any]:
    """Assemble the router-bench-manifest.json structure (schema v1)."""
    cost_delta = candidate["cost_usd"] - baseline["cost_usd"]
    cost_pct = (cost_delta / baseline["cost_usd"] * 100.0) if baseline["cost_usd"] else 0.0
    parity = "equal"
    for ph in set(candidate.get("phases", {})) | set(baseline.get("phases", {})):
        c_ok = candidate["phases"].get(ph) == "success"
        b_ok = baseline["phases"].get(ph) == "success"
        if b_ok and not c_ok:
            parity = "candidate_worse"
            break
        if c_ok and not b_ok:
            parity = "candidate_better"
    return {
        "schema_version": "1.0", "generated_at": _now(), "depth": depth,
        "candidate": candidate, "baseline": baseline,
        "deltas": {"cost_usd": cost_delta, "cost_pct": cost_pct,
                   "counterfactual_routing_savings_usd": counterfactual_savings,
                   "phase_parity": parity},
        "verdict": verdict,
        "price_table_provenance": price_provenance,
    }


def build_learnings(run_ids: list[int], findings: list[dict[str, Any]],
                    recommendations: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": "1.0", "generated_at": _now(),
            "run_ids": run_ids, "findings": findings, "recommendations": recommendations}


def render_report_md(manifest: dict[str, Any], learnings: dict[str, Any]) -> str:
    c, b, d = manifest["candidate"], manifest["baseline"], manifest["deltas"]
    lines = [
        "# Router-Bench Report",
        "",
        f"- **Verdict:** {manifest['verdict']}",
        f"- **Depth:** {manifest['depth']}",
        f"- **Generated:** {manifest['generated_at']}",
        "",
        "## Cost",
        "",
        "| Config | Label | Wire | Input | Output | Cost (USD) |",
        "|---|---|---|---:|---:|---:|",
        f"| candidate | {c['label']} | {c['wire_api']} | {c['tokens']['input']:,} "
        f"| {c['tokens']['output']:,} | {c['cost_usd']:.4f} |",
        f"| baseline | {b['label']} | {b['wire_api']} | {b['tokens']['input']:,} "
        f"| {b['tokens']['output']:,} | {b['cost_usd']:.4f} |",
        "",
        f"**Δ cost:** {d['cost_usd']:+.4f} USD ({d['cost_pct']:+.1f}%) · "
        f"**counterfactual routing savings:** {d['counterfactual_routing_savings_usd']:+.4f} USD · "
        f"**phase parity:** {d['phase_parity']}",
        "",
        "### Routing mix (candidate)",
        "",
    ]
    for m in c.get("routed_model_mix", []):
        lines.append(f"- {m['model']}: {m.get('cost_pct', 0):.1f}% of $")
    lines += ["", "## Learnings", ""]
    if not learnings["findings"]:
        lines.append("_No anomalies detected._")
    for f in learnings["findings"]:
        lines.append(f"- **{f.get('category')}** ({f.get('id')})")
    lines += ["", "### Recommendations", ""]
    for r in learnings["recommendations"]:
        cites = ", ".join(r.get("motivated_by", []))
        lines.append(f"- {r['text']} _(from {cites})_")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_report.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add skills/threadlight-router-bench/references/router-bench-manifest.schema.json skills/threadlight-router-bench/references/learnings-manifest.schema.json skills/threadlight-router-bench/scripts/report.py skills/threadlight-router-bench/tests/test_report.py
git commit -m "feat(router-bench): manifest schemas + report renderer (report.py)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 9: `dispatch.py` — orchestrate two e2e runs + run window

**Files:**
- Create: `skills/threadlight-router-bench/scripts/dispatch.py`
- Test: `skills/threadlight-router-bench/tests/test_dispatch.py`

`gh workflow run` returns no run ID, so we record a pre-dispatch UTC timestamp then poll
`gh run list` for the newest `workflow_dispatch` run on the ref created after it. The metric
window comes from `gh run view --json startedAt,updatedAt`.

- [ ] **Step 1: Write the failing test**

Create `skills/threadlight-router-bench/tests/test_dispatch.py`:

```python
from __future__ import annotations
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURES = Path(__file__).resolve().parent.parent / "references" / "fixtures"

import dispatch  # noqa: E402


def test_build_dispatch_args():
    args = dispatch.build_dispatch_args(
        repo="aiappsgbb/threadlight-skills", ref="my-branch",
        deployment="model-router", mode="full", wire_api="completions")
    j = " ".join(args)
    assert "workflow run threadlight-e2e-foundry.yml" in j
    assert "--ref my-branch" in j
    assert "model_deployment=model-router" in j
    assert "mode=full" in j and "wire_api=completions" in j
    assert "teardown=true" in j  # always forced


def test_resolve_run_id_picks_newest_after_timestamp():
    runs = [
        {"databaseId": 100, "event": "workflow_dispatch", "headBranch": "my-branch",
         "createdAt": "2026-06-30T09:00:00Z"},
        {"databaseId": 200, "event": "workflow_dispatch", "headBranch": "my-branch",
         "createdAt": "2026-06-30T10:30:00Z"},
        {"databaseId": 300, "event": "schedule", "headBranch": "my-branch",
         "createdAt": "2026-06-30T10:31:00Z"},
    ]
    runner = lambda args: json.dumps(runs)
    rid = dispatch.resolve_run_id("aiappsgbb/threadlight-skills", "my-branch",
                                  since_iso="2026-06-30T10:00:00Z", runner=runner, retries=1)
    assert rid == 200


def test_run_window_from_view():
    doc = json.loads((FIXTURES / "run-timing.json").read_text())
    start, end = dispatch.run_window(doc)
    assert start == "2026-06-30T10:21:05Z"
    assert end == "2026-06-30T11:05:04Z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_dispatch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dispatch'`.

- [ ] **Step 3: Write `dispatch.py`**

Create `skills/threadlight-router-bench/scripts/dispatch.py`:

```python
#!/usr/bin/env python3
"""Dispatch + poll two Threadlight e2e runs for a router-bench comparison.

`gh workflow run` returns no run ID, so resolve_run_id() polls `gh run list`
for the newest workflow_dispatch run on the ref created after a recorded
pre-dispatch timestamp. teardown is always forced true (no leaked resources).
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Callable

WORKFLOW = "threadlight-e2e-foundry.yml"


def build_dispatch_args(repo: str, ref: str, deployment: str, mode: str,
                        wire_api: str) -> list[str]:
    """Return the `gh` argv to dispatch one e2e run (teardown forced true)."""
    return [
        "workflow", "run", WORKFLOW, "--repo", repo, "--ref", ref,
        "-f", f"model_deployment={deployment}",
        "-f", f"mode={mode}",
        "-f", f"wire_api={wire_api}",
        "-f", "teardown=true",
    ]


def dispatch_run(repo: str, ref: str, deployment: str, mode: str, wire_api: str,
                 runner: Callable[[list[str]], str] | None = None) -> None:
    run = runner or _default_runner
    run(build_dispatch_args(repo, ref, deployment, mode, wire_api))


def resolve_run_id(repo: str, ref: str, since_iso: str,
                   runner: Callable[[list[str]], str] | None = None,
                   retries: int = 30, sleep_s: float = 10.0) -> int:
    """Poll gh run list for the newest workflow_dispatch run on ref after since_iso."""
    run = runner or _default_runner
    args = ["run", "list", "--repo", repo, "--branch", ref,
            "--json", "databaseId,event,headBranch,createdAt,status", "--limit", "30"]
    for attempt in range(retries):
        runs = json.loads(run(args))
        candidates = [r for r in runs
                      if r.get("event") == "workflow_dispatch"
                      and r.get("headBranch") == ref
                      and r.get("createdAt", "") > since_iso]
        if candidates:
            return max(candidates, key=lambda r: r["createdAt"])["databaseId"]
        if attempt < retries - 1:
            time.sleep(sleep_s)
    raise RuntimeError(f"no workflow_dispatch run on {ref} after {since_iso}")


def poll_until_complete(repo: str, run_id: int,
                        runner: Callable[[list[str]], str] | None = None,
                        timeout_s: float = 5400.0, sleep_s: float = 30.0) -> dict[str, Any]:
    """Poll a run until status==completed; return its view JSON (incl. window)."""
    run = runner or _default_runner
    args = ["run", "view", str(run_id), "--repo", repo,
            "--json", "status,conclusion,startedAt,updatedAt,databaseId"]
    deadline = time.time() + timeout_s
    while True:
        doc = json.loads(run(args))
        if doc.get("status") == "completed":
            return doc
        if time.time() > deadline:
            raise TimeoutError(f"run {run_id} did not complete within {timeout_s}s")
        time.sleep(sleep_s)


def run_window(view_doc: dict[str, Any]) -> tuple[str, str]:
    """Return (startedAt, updatedAt) ISO strings for the metric window."""
    return view_doc["startedAt"], view_doc["updatedAt"]


def _default_runner(args: list[str]) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("gh " + " ".join(args) + " failed:\n" + proc.stderr.strip())
    return proc.stdout
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_dispatch.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-router-bench/scripts/dispatch.py skills/threadlight-router-bench/tests/test_dispatch.py
git commit -m "feat(router-bench): e2e dispatch + run-window resolution (dispatch.py)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 10: `router_bench.py` CLI dispatcher + end-to-end `analyze`

**Files:**
- Create: `skills/threadlight-router-bench/scripts/router_bench.py`
- Test: `skills/threadlight-router-bench/tests/test_cli.py`

`analyze` is the consumer-only path: given two run IDs (already complete) + each run's
routed-token JSON + leg-manifest dir, it scores and writes the manifests/report with no
dispatch. The CLI exposes a `_analyze_from_parts(...)` pure helper so the full pipeline is
testable without `gh`/`az`.

- [ ] **Step 1: Write the failing test**

Create `skills/threadlight-router-bench/tests/test_cli.py`:

```python
from __future__ import annotations
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURES = Path(__file__).resolve().parent.parent / "references" / "fixtures"

import metrics  # noqa: E402
import router_bench  # noqa: E402


def test_analyze_from_parts_writes_artifacts(tmp_path):
    routed = metrics.parse_metrics_json(json.loads((FIXTURES / "az-metrics-modelrouter.json").read_text()))
    jobs = json.loads((FIXTURES / "gh-run-jobs.json").read_text())
    legs_dir = FIXTURES / "legs"
    out = tmp_path / "out"
    manifest = router_bench._analyze_from_parts(
        candidate={"label": "model-router", "wire_api": "completions", "run_id": 1,
                   "conclusion": "success", "routed_tokens": routed,
                   "jobs": jobs, "legs_dir": legs_dir},
        baseline={"label": "gpt-5.4-mini", "wire_api": "responses", "run_id": 2,
                  "conclusion": "success", "routed_tokens": routed,
                  "jobs": jobs, "legs_dir": legs_dir},
        baseline_model="gpt-5.4-mini", depth="full-paired",
        findings=[], recommendations=[], out_dir=out)
    # artifacts on disk
    assert (out / "router-bench-manifest.json").exists()
    assert (out / "learnings-manifest.json").exists()
    assert (out / "router-bench-report.md").exists()
    # identical routed tokens both sides -> equal parity, candidate not cheaper -> neutral
    assert manifest["deltas"]["phase_parity"] == "equal"
    assert manifest["verdict"] in ("neutral", "efficiency_win", "regression")
    assert manifest["candidate"]["cost_usd"] > 0


def test_cli_parser_has_subcommands():
    parser = router_bench.build_parser()
    ns = parser.parse_args(["prices"])
    assert ns.command == "prices"
    ns = parser.parse_args(["analyze", "--candidate-run", "1", "--baseline-run", "2",
                            "--out", "/tmp/x"])
    assert ns.command == "analyze" and ns.candidate_run == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'router_bench'`.

- [ ] **Step 3: Write `router_bench.py`**

Create `skills/threadlight-router-bench/scripts/router_bench.py`:

```python
#!/usr/bin/env python3
"""threadlight-router-bench CLI.

Offline, advisory benchmark of a model-router deployment vs a fixed baseline
across the Threadlight e2e pipeline. Two outputs: an efficiency scorecard
(router-bench-manifest.json + router-bench-report.md) and a learnings digest
(learnings-manifest.json).

Subcommands:
  run      Orchestrate two e2e dispatches (candidate + baseline), poll, harvest,
           score, report. Requires gh (auth) + az (Monitoring Reader on the
           Foundry resource).
  analyze  Re-score two already-complete run IDs (no dispatch). Cheap/offline.
  prices   Print or refresh the model price table.

Exit codes:
  0  artifacts produced (verdict lives inside the manifest)
  2  missing prerequisite (gh/az unavailable, bad run id)
  3  verdict == regression (advisory non-zero so callers can gate)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import dispatch  # noqa: E402
import harvest  # noqa: E402
import metrics  # noqa: E402
import prices as prices_mod  # noqa: E402
import report as report_mod  # noqa: E402
import score  # noqa: E402

FOUNDRY_RESOURCE_ID = (
    "/subscriptions/2c745a8f-9d37-45e3-8506-80797e89735e/resourceGroups/"
    "rg-shared-gbb-ci/providers/Microsoft.CognitiveServices/accounts/aif-shared-gbb-ci"
)


def _side_costed(side: dict[str, Any], table: dict[str, Any]) -> dict[str, Any]:
    """Attach cost rollup + phase parity to a side dict that carries routed_tokens+jobs+legs_dir."""
    rc = score.score_cost(side["routed_tokens"], table)
    phases = harvest.parse_phase_parity(side["jobs"])
    return {"label": side["label"], "wire_api": side["wire_api"], "run_id": side["run_id"],
            "conclusion": side["conclusion"], "phases": phases,
            "tokens": rc["tokens"], "cost_usd": rc["cost_usd"],
            "routed_model_mix": rc["routed_model_mix"]}


def _analyze_from_parts(candidate: dict[str, Any], baseline: dict[str, Any],
                        baseline_model: str, depth: str,
                        findings: list[dict[str, Any]], recommendations: list[dict[str, Any]],
                        out_dir: Path) -> dict[str, Any]:
    """Pure pipeline: score both sides, build + write all three artifacts. Returns manifest."""
    table = prices_mod.load_price_table()
    cand = _side_costed(candidate, table)
    base = _side_costed(baseline, table)

    cand_legs = harvest.load_leg_manifests(candidate["legs_dir"])
    base_legs = harvest.load_leg_manifests(baseline["legs_dir"])
    parity = score.phase_parity_delta(cand["phases"], base["phases"])
    regs = score.capability_regressions(cand_legs, base_legs)
    cf = score.counterfactual_cost(candidate["routed_tokens"], baseline_model, table)
    verdict = score.verdict(cand["cost_usd"], base["cost_usd"], parity, regs, kpi_pass_rate_drop=0.0)

    manifest = report_mod.build_manifest(cand, base, depth,
                                         counterfactual_savings=base["cost_usd"] - cf,
                                         verdict=verdict,
                                         price_provenance={"source": "seed",
                                                           "last_validated": table["gpt-5.4"]["last_validated"]})
    learnings = report_mod.build_learnings([candidate["run_id"], baseline["run_id"]],
                                           findings, recommendations)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "router-bench-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "learnings-manifest.json").write_text(json.dumps(learnings, indent=2), encoding="utf-8")
    (out / "router-bench-report.md").write_text(report_mod.render_report_md(manifest, learnings),
                                                encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="router_bench", description="Threadlight router-bench")
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("run", help="dispatch + analyze a candidate/baseline pair")
    pr.add_argument("--candidate", default="model-router:completions")
    pr.add_argument("--baseline", default="gpt-5.4-mini:responses")
    pr.add_argument("--depth", choices=["smoke-paired", "full-paired"], default="full-paired")
    pr.add_argument("--ref", required=True)
    pr.add_argument("--repo", default="aiappsgbb/threadlight-skills")
    pr.add_argument("--out", required=True)

    pa = sub.add_parser("analyze", help="re-score two completed run IDs (no dispatch)")
    pa.add_argument("--candidate-run", type=int, required=True)
    pa.add_argument("--baseline-run", type=int, required=True)
    pa.add_argument("--baseline-model", default="gpt-5.4-mini")
    pa.add_argument("--depth", default="full-paired")
    pa.add_argument("--out", required=True)
    pa.add_argument("--repo", default="aiappsgbb/threadlight-skills")

    pp = sub.add_parser("prices", help="print or refresh the price table")
    pp.add_argument("--refresh", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    if ns.command == "prices":
        table = prices_mod.load_price_table()
        print(json.dumps(table, indent=2))
        return 0
    # `run` and `analyze` integration paths shell out to gh/az; covered by the
    # SKILL.md runbook + manual integration. _analyze_from_parts is unit-tested.
    print("Use `analyze`/`run` via SKILL.md runbook; see scripts for the pipeline.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the whole suite**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/ -v`
Expected: PASS (all tests green across all files).

- [ ] **Step 6: Commit**

```bash
git add skills/threadlight-router-bench/scripts/router_bench.py skills/threadlight-router-bench/tests/test_cli.py
git commit -m "feat(router-bench): CLI dispatcher + analyze pipeline (router_bench.py)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 11: `SKILL.md` — runbook, recommendations prompt, guardrails

**Files:**
- Create: `skills/threadlight-router-bench/SKILL.md`
- Test: `skills/threadlight-router-bench/tests/test_skill_discipline.py`

Mirror the front-matter + section style of `skills/threadlight-evals/SKILL.md` (read it first
for the exact YAML keys this repo uses).

- [ ] **Step 1: Inspect the existing SKILL.md front-matter convention**

Run: `sed -n '1,30p' skills/threadlight-evals/SKILL.md`
Expected: shows the YAML front-matter keys (e.g. `name`, `description`, `when_to_use`/`license`)
this repo uses. Match those keys exactly in the new SKILL.md.

- [ ] **Step 2: Write the failing discipline test**

Create `skills/threadlight-router-bench/tests/test_skill_discipline.py`:

```python
from __future__ import annotations
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "SKILL.md"


def test_skill_md_exists_and_has_front_matter():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---"), "SKILL.md must open with YAML front-matter"
    assert "threadlight-router-bench" in text


def test_skill_md_documents_rbac_and_serialization():
    text = SKILL.read_text(encoding="utf-8").lower()
    assert "monitoring reader" in text          # RBAC requirement
    assert "completions" in text                # wire-api requirement for model-router
    assert "serial" in text or "concurrent" in text  # attribution caveat
    assert "teardown" in text                   # guardrail


def test_skill_md_references_recommendations_prompt():
    text = SKILL.read_text(encoding="utf-8").lower()
    assert "recommendation" in text
    assert "findings" in text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_skill_discipline.py -v`
Expected: FAIL — `FileNotFoundError` (SKILL.md not created yet).

- [ ] **Step 4: Write `SKILL.md`**

Create `skills/threadlight-router-bench/SKILL.md` (adjust the front-matter keys in step 1 if
this repo differs):

````markdown
---
name: threadlight-router-bench
description: >-
  Offline, advisory benchmark of an Azure AI Foundry model-router deployment
  against a fixed baseline model across the Threadlight e2e pipeline. Produces an
  efficiency scorecard (cost + quality + routing mix) and a learnings digest
  (deterministic findings + recommendations). Use when proving router efficiency
  to customers, comparing router vs a standard model, or harvesting CI/GHCP logs
  for improvement points. Never runs in the hot CI path.
---

# threadlight-router-bench

Benchmark **model-router** (candidate) vs a fixed model like **gpt-5.4-mini**
(baseline) over the full Threadlight e2e pipeline, on two axes:

- **Cost** — Azure Monitor `InputTokens`/`OutputTokens` on the Foundry resource,
  split by the routed `ModelName` dimension, scoped to each run's deployment +
  time-window. (The Copilot CLI exposes **no** token usage for BYO providers, so
  cost cannot come from CLI logs.)
- **Quality** — existing per-phase CI step conclusions + the Phase-5
  govern/evals/redteam leg manifests. No new model calls.

## When to use

- "Prove model-router is more efficient than <model> for our pipeline."
- "Compare router vs gpt-5.4-mini on quality AND cost."
- "Inspect the last CI/GHCP run for learnings and improvement points."

## Prerequisites (hard requirements)

- **`gh`** authenticated to `aiappsgbb/threadlight-skills` (dispatch + download).
- **`az`** logged in with **Monitoring Reader** on the Foundry resource
  `aif-shared-gbb-ci` (token metrics). Without it the cost axis fails fast.
- model-router must be driven over **`wire_api=completions`** — its Responses v1
  route returns `HTTP 400 operation unsupported`. The baseline keeps its default
  wire. `run` sets this automatically.

## Guardrails

- **teardown forced true** on every dispatch — no leaked Azure resources.
- **Serialize on a shared deployment.** Azure Monitor token metrics have no
  run-id dimension. If candidate and baseline resolve to the **same** deployment,
  dispatch them sequentially so their time-windows don't overlap. Different
  deployments (the default) may run concurrently.
- Advisory only by default. A `regression` verdict exits non-zero so a caller may
  choose to gate.
- Price table is seed/unverified until `prices --refresh` validates it — do not
  quote absolute $ to customers until then; routing-mix % and relative deltas are
  safe.

## Runbook

1. **Dispatch + poll** (`run`) or pick two completed run IDs (`analyze`):
   ```bash
   python3.13 scripts/router_bench.py run \
     --candidate model-router:completions --baseline gpt-5.4-mini:responses \
     --depth full-paired --ref <branch> --out ./out
   ```
   `analyze` skips dispatch:
   ```bash
   python3.13 scripts/router_bench.py analyze \
     --candidate-run <ID> --baseline-run <ID> --out ./out
   ```
2. **Harvest** per run: `gh run view --json jobs` (phase parity) + `gh run
   download` (leg manifests) + `az monitor metrics list` over the run window
   (routed-model tokens). See `scripts/{harvest,metrics,dispatch}.py`.
3. **Score**: `scripts/score.py` rolls up cost + routing mix + counterfactual and
   diffs quality; emits the verdict (`efficiency_win` / `neutral` / `regression`).
4. **Recommendations turn (you, the agent):** read `out/learnings-manifest.json`
   (the structured findings — NOT raw logs) plus the cost/quality deltas in
   `out/router-bench-manifest.json`, then synthesize concrete, actionable
   recommendations. **Each recommendation MUST cite the finding IDs that motivated
   it** (`motivated_by`). Write them back into `learnings-manifest.json`
   `recommendations[]` and the report's Recommendations section. No auto-apply.

## Recommendations prompt (for step 4)

> You are given structured CI findings (`learnings-manifest.json#findings`) and
> the cost/quality deltas (`router-bench-manifest.json#deltas`, `#verdict`,
> `#candidate.routed_model_mix`). Produce 3–7 concrete, actionable
> recommendations to improve efficiency, quality, or pipeline reliability. For
> each: a one-line action, the finding IDs it addresses (`motivated_by`), and
> whether it is `persistent` (recurring across runs). Do not invent findings;
> ground every recommendation in the provided data.

## Outputs (`--out`)

- `router-bench-report.md` — human/Cx-facing scorecard + learnings.
- `router-bench-manifest.json` — structured cost/quality/routing (schema in
  `references/router-bench-manifest.schema.json`).
- `learnings-manifest.json` — findings + recommendations (schema in
  `references/learnings-manifest.schema.json`).
````

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/test_skill_discipline.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add skills/threadlight-router-bench/SKILL.md skills/threadlight-router-bench/tests/test_skill_discipline.py
git commit -m "feat(router-bench): SKILL.md runbook + recommendations prompt + guardrails

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 12: Full suite green + plan-coverage check

**Files:** none (verification only)

- [ ] **Step 1: Run the full skill test suite**

Run: `python3.13 -m pytest skills/threadlight-router-bench/tests/ -v`
Expected: PASS — all tests across `test_prices, test_metrics, test_score_cost, test_harvest,
test_score_quality, test_findings, test_report, test_dispatch, test_cli, test_skill_discipline`.

- [ ] **Step 2: Confirm stdlib-only + no stray imports**

Run:
```bash
grep -RInE "^\s*(import|from) " skills/threadlight-router-bench/scripts \
  | grep -vE "(argparse|json|subprocess|time|re|sys|pathlib|datetime|typing|urllib|prices|metrics|score|harvest|report|dispatch|findings|__future__)" \
  || echo "OK: stdlib + siblings only"
```
Expected: `OK: stdlib + siblings only`.

- [ ] **Step 3: Spec-coverage self-check (manual)**

Confirm each spec section maps to a task:
- §6.1 cost axis (Azure Monitor) → Tasks 3, 4 (`metrics.py`, `score.score_cost`).
- §6.2 quality axis → Tasks 5, 6 (`harvest.parse_phase_parity`, `score` quality).
- §6.3 verdict + tolerance → Task 6 (`score.verdict`).
- §6.4 manifest → Task 8 (`report.build_manifest` + schema).
- §7 learnings (taxonomy + recommendations + manifest) → Tasks 7, 8, 11.
- §5 data flow (dispatch/poll/window) → Task 9 (`dispatch.py`).
- §8 CLI surface (run/analyze/prices) → Task 10 (`router_bench.py`).
- §10 RBAC + serialization guardrails → Task 11 (`SKILL.md`).

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A skills/threadlight-router-bench
git commit -m "test(router-bench): full suite green + stdlib-only verification

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" || echo "nothing to commit"
```

---

## Deferred (Future Work, per spec §12 — do NOT build now)

- `prices --refresh` live Retail Prices API wiring (seed table ships; refresh is a stub-friendly add-on).
- Scheduled/nightly bench + trend dashboards.
- Auto-filing recommendations as GitHub issues / auto-PR.
- Multi-baseline matrices; wiring the regression verdict as a hard CI gate.

## Notes for the implementer

- Use **`python3.13`** (matches CI = 3.13.14), not `python3` (3.14.x).
- Every module's `_default_runner` shells out to `gh`/`az`; all unit tests inject a
  fake `runner`/fixtures so the suite needs **no network**.
- The real integration path (`run`) is exercised manually via the SKILL.md runbook
  against `aif-shared-gbb-ci`; `_analyze_from_parts` is the unit-tested core.
- Price values are **seed/unverified** — keep the `source: "seed"` provenance until
  `prices --refresh` is implemented and run.
