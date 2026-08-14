# Judge calibration — making `pass_rate` a number you can defend

Most evaluator scores in an agent pilot are produced by a model grading another
model. That is fine. What is not fine is shipping the resulting `pass_rate` as
evidence without saying **what the judge was given, what scale it used, and how
close it lands to a human**.

This matters more in threadlight than in a standalone eval run, because
`metrics.pass_rate` does not stop at the evals leg:

```
evals-manifest.json → pillar 6 → scorecard outcome-KPI column
                              → EU AI Act evidence pack (Art 15, accuracy)
```

An uncalibrated judge does not just produce a soft number — it produces a soft
number that gets promoted into a readiness verdict and a regulator-facing file.

## The failure mode: reference-free judging

A judge asked *"is this answer good?"* with no ground truth available is grading
plausibility, not correctness. It systematically **over-credits confident,
well-formed, wrong answers** — exactly the failure agents produce most often.

The same judge, same model, same rubric, given the expected answer alongside the
candidate, flips a large share of its own prior verdicts. The score moves
because the task changed: from *"does this read well?"* to *"does this match?"*

Consequence: **a reference-free `pass_rate` is not comparable to a
reference-anchored one, and neither is comparable across judge model versions.**
Trending them on one chart is a measurement error, not a quality signal.

## The recipe

### R1 — Anchor every judged row to a reference

Every row that a judge grades carries `expected` (see `dataset-shape.md`), and
the judge prompt must actually receive it. A dataset that *contains* `expected`
while the grader template never interpolates it is still reference-free judging.

Rows where no single correct answer exists (open-ended summarization, tone) do
not get a reference — they get a **rubric with explicit anchors** instead (R2),
and they are counted separately from reference-anchored rows.

### R2 — Anchor the rubric, prefer few levels

A bare 1–5 scale invites the judge to cluster on 3 and 4, which destroys
discrimination and makes thresholds arbitrary. Two fixes, in order of
preference:

1. **Binary with explicit criteria** — `pass` requires *all* listed conditions.
2. **Few levels, each with a written anchor** — never a naked number.

```yaml
# ✅ anchored, binary
criterion: grounded_in_tool_output
pass_when:
  - every factual claim traces to a row in tool_outputs
  - no entity appears that is absent from tool_outputs
  - refuses or escalates when tool_outputs are empty
fail_when:
  - any claim is unsupported, even if correct in the real world
```

```yaml
# ❌ unanchored — do not ship this as a gate
criterion: groundedness
scale: 1-5
```

The gate reads the binary outcome. Keep a numeric score if you want a trend
line, but **the gate must not be a bare mean of an unanchored scale**.

### R3 — Measure judge–human agreement once, on a seed set

Before a judge's output becomes a gate, label a small seed set by hand
(30–50 rows is usually enough to expose a broken rubric) and record how often
the judge agrees with the human label.

Record the agreement number next to the rubric. It is the only honest answer to
*"why should I believe this score?"* — and it is the number that moves when you
improve the rubric. Rubric revisions are validated by re-running the seed set,
not by inspection.

If agreement is poor, the rubric is the defect. Rewrite the anchors before
touching the agent.

### R4 — Pin the judge, and treat a judge swap as a swap

The judge model is a **dependency of your measurement**, not neutral
infrastructure. Record its model name and version alongside the rubric.

Changing the judge model, its version, or its rubric changes the meaning of
`pass_rate` across the whole run history. That is exactly the class of change
the F3 champion–challenger gate exists for — see `ab-comparison.md` — except
here the thing under test is the *evaluator*, not the agent:

- Re-score a frozen set of already-graded runs with the new judge.
- Compare verdict-level agreement with the old judge, not just mean score.
- If verdicts move materially, the trend line is broken: annotate the run
  history at the swap point rather than pretending the series is continuous.

### R5 — The generator does not grade itself

A skill must not be the evaluator of its own output, and an agent asked to
assess its own work reliably praises it. Judging runs as a separate step, with
its own prompt and its own context — never as a final "now check your work"
turn inside the same chain that produced the answer.

## Mapping an external 1–5 score into a threadlight gate

Upstream evaluators — including Foundry's own and those used by adjacent
migration tooling — commonly emit 1–5 floats (`relevance: 4.2`). Do not pipe
that mean straight into `min_pass_rate`. Convert explicitly:

| Step | Rule |
|---|---|
| 1. Declare the cut | Write the pass boundary down, per criterion (e.g. `relevance >= 4`). |
| 2. Binarize per row | Each row becomes pass/fail at that cut. |
| 3. Aggregate | `pass_rate` = fraction of rows passing **all** required criteria. |
| 4. Keep the mean as a trend only | Report it; never gate on it. |

Rationale: a mean of 4.2 is compatible with a fifth of your rows failing badly.
The gate must be sensitive to that; the mean is not.

## What the validator checks — and what it does not

Honest boundary, so this doc is not mistaken for enforcement:

| Concern | Checked by `evals_check.py`? |
|---|---|
| Dataset rows carry `tool_calls` + `tool_outputs` (`EVAL-003`) | ✅ yes |
| A threshold is declared somewhere (`EVAL-004`) | ✅ yes |
| Rows carry `expected` | ❌ no — reviewer's judgement |
| Judge prompt actually receives the reference | ❌ no |
| Rubric anchors exist and are binary | ❌ no |
| Judge–human agreement was measured | ❌ no |
| Judge model/version pinned | ❌ no |

`dataset_shape_ok` can be `pass` on a dataset with **zero** reference answers.
Treat the checklist above as review criteria when a pilot's `pass_rate` is about
to be quoted in a readiness verdict or an evidence pack.
