# ADR: Per-evidence freshness timestamps for `threadlight-production-ready`

- **Date:** 2026-06-10
- **Status:** Proposed
- **Tracking issue:** [aiappsgbb/threadlight-skills#22](https://github.com/aiappsgbb/threadlight-skills/issues/22)
- **Implements:** none (this is the first cut)
- **Supersedes:** none

## Context

`threadlight-production-ready` v1.0 ships run-level freshness only:

- One `safe_check_reference.checked_at` for the post-deploy manifest it
  was anchored against.
- One `manifest.checked_at` (= `_utc_now()` at JSON emit time) for the
  whole report.

In a single-session pilot (CLI invocation under an hour, reviewer reads
the report the same afternoon), this is enough. In a **multi-day pilot**
— or even a single CLI run that takes hours because live probes walk
50+ Azure resources with throttling — a Tier-1 network probe at hour 0
and a Tier-5 Citadel probe at hour 22 both land in the same evidence
register with no way to tell them apart. A CISO reviewer cannot tell
which evidence is stale.

This is a cross-skill design pass because the same staleness question
could in principle apply to `threadlight-safe-check` post-deploy
manifests too. This ADR scopes only the production-ready side of the
schema rev.

**Scope clarification (within-run staleness only).** This change
addresses staleness *within a single CLI invocation* whose live probes
span hours or days. It does **not** preserve evidence across multiple
CLI runs (no historical aggregation). Each `production_ready.py`
invocation still produces a self-contained manifest covering only the
probes it ran during that invocation. Cross-run evidence rollup is a
separate feature, out of scope.

## Findings from the code (what's already in `main`)

Reviewed `skills/threadlight-production-ready/scripts/production_ready.py`
@ commit `416d2ad`:

1. `EvidenceEntry.captured_at: str = ""` **already exists** as a field
   on the dataclass (line 290).
2. There are **14** `EvidenceEntry(...)` constructor call sites inside
   `_check_*_live` functions, and all 14 stamp
   `captured_at=_utc_now()` at the same site that materializes the
   evidence row. (Verified by `grep -c 'EvidenceEntry(' ...` and
   `grep -c 'captured_at=_utc_now' ...`.)
3. The evidence register table in the rendered report (line ~2291)
   does **not** include a `Collected` column — the data is present in
   JSON but invisible in the markdown.
4. The executive summary (line ~2117) uses `manifest['checked_at']`
   (= report emit time) and has no notion of evidence age.
5. Static-mode runs emit **no** evidence rows; only live probes
   produce `EvidenceEntry` instances.
6. `SKILL.md` § "evidence_register" doc example uses the field name
   `ran_at` — a third, undocumented name for the same concept.
7. `DEFAULT_FRESHNESS_HOURS = 24` already exists at the module level
   and is reused by `--freshness-hours`.

So the data-layer schema rev for this issue is **already done**. The
work is rendering, surfacing, and naming consistency.

## Decisions

### D1. Granularity: per-evidence (formalize status quo)

Per-evidence wins. Per-pillar would be a regression — it would require
**removing** an already-populated field. There's no token-bloat or
storage argument: a typical multi-pillar run has 20–50 evidence rows,
so adding a `Collected` column to the markdown costs ~1.5 KB of report
text.

> **Decision:** every `EvidenceEntry` keeps its own `captured_at`. No
> pillar-level rollup is computed or stored — the closest thing is the
> aggregate `evidence_freshness` block introduced in D6.

### D2. Field name: keep `captured_at` (do not rename to `collected_at`)

The issue body uses `collected_at` as a suggested name. The code
already emits `captured_at` in the JSON manifest (populated at 14
live-probe call sites under schema version 1.0). The `SKILL.md` doc
example, however, currently shows the wrong name (`ran_at`). So this
is really a doc-vs-code mismatch, not a missing field.

> **Decision:** canonical field name is `captured_at`. This change is
> therefore a doc correction (fix `SKILL.md`'s `ran_at`) plus
> additive surfacing (render in markdown + new `evidence_freshness`
> block). No actual JSON field is being renamed.
>
> Reasons not to rename to `collected_at`:
> 1. The JSON already emits `captured_at`; renaming would be a true
>    schema break requiring a `schema_version` bump.
> 2. Aligns the JSON field with the code variable used at every
>    write site.
>
> Trade-off: the issue's literal `collected_at` is not used. The
> threadlight-production-ready skill is in v0.x (advisory, no
> external consumers we know of), so a future rename is cheap; this
> ADR just doesn't pay that cost now.

### D3. Static-mode evidence: emit no rows (status quo, documented)

In current code, only `_check_*_live` functions append to the evidence
list. Static-mode runs come out with `evidence_register: []`. The
issue asks: do static-mode evidence rows inherit run start, or null?

The honest answer in current architecture: static mode has no evidence
rows to stamp.

> **Decision:** static-mode runs emit `evidence_register: []`. The
> `captured_at` invariant is "always present and non-empty when an
> entry exists". `SKILL.md` documents this explicitly.
>
> Forward-compat note: if a future PR adds static-fingerprint evidence
> (e.g., a row recording "scanned 142 .bicep files, sha256 …"), it
> would use `captured_at = manifest.checked_at` (the run-end stamp,
> since static analysis is naturally tied to the run boundary). That
> work is out of scope here.

### D4. `threadlight-safe-check` per-resource `checked_at`: deferred

The issue lists this as a stretch goal. Speaking to it would require
modifying `skills/threadlight-safe-check/scripts/safe_check.py`,
revving its post-deploy manifest shape, and updating the
production-ready pre-flight that reads `safe_check_reference`.

> **Decision:** out of scope for this PR. Production-ready stamps its
> own probes only. The existing run-level `safe_check_reference.checked_at`
> stays as the single safe-check anchor. If we add safe-check
> per-resource stamps later, they slot in as a sibling field
> (`safe_check_reference.per_resource_checked_at: {…}`) — additive,
> non-breaking.
>
> Rationale: safe-check's invariant is "did the deploy succeed";
> production-ready's invariant is "is the deployed thing safe to
> ship". They have different cadences (safe-check usually runs once
> at end of `azd up`; production-ready can run repeatedly). Coupling
> their schemas in one PR creates a worse blast radius than splitting.

### D5. Rendered column placement and format

The `Collected` column goes into the existing Evidence register table
in **Appendix § 10** of the markdown report. Column order:

```
| Ref | Pillar | Tier | Collected           | Command | Result | Notes |
|-----|--------|------|---------------------|---------|--------|-------|
```

Format: full ISO 8601 UTC (`2026-06-09T22:52:13Z`) — matches the
existing `_utc_now()` output. No truncation, so consumers can round-trip
the value back through `datetime.strptime`.

### D6. Executive summary staleness banner

Threshold: when `(manifest.checked_at − oldest_captured_at) >
freshness_hours`, the executive summary gets an extra bullet:

```
- **Oldest evidence:** 2026-06-09T22:52Z (28h before report) — exceeds
  freshness window (24h). Some evidence may be stale.
```

When `<= freshness_hours`, no bullet is added (no clutter on the common
case).

Threshold flag: **reuse the existing `--freshness-hours`** (currently
used for safe-check pre-flight). Rationale: 24h is the right
human-meaningful window for both gates, and exposing a second flag for
the same number invites mismatch. If a user passes `--freshness-hours
72`, both the safe-check pre-flight tolerance and the evidence
staleness banner loosen consistently. `SKILL.md` will be updated to
document the dual semantic.

> **Coupling caveat.** Reusing one flag means an operator who loosens
> safe-check pre-flight tolerance also loosens the evidence-staleness
> banner. The two intents are not always the same. We accept the
> coupling for v0.2 because (a) the flag is rarely used (it's an
> escape hatch), (b) splitting it pre-emptively is YAGNI, and (c) the
> staleness condition is also recorded as the explicit boolean
> `evidence_freshness.stale` in the JSON manifest so a strict
> consumer can re-evaluate against its own threshold. If operator
> feedback bites, split into `--safe-check-freshness-hours` +
> `--evidence-freshness-hours` in v0.3. A smoke test
> (#freshness-hours-coupling below) pins the current shared-flag
> behavior so the regression is visible if we ever split.

**Boundary:** the comparison is **strict greater than** (`>`). An
evidence row exactly `freshness_hours` old is **not** stale. This
matches the existing safe-check behavior (`accept_stale=False` rejects
*older than* `freshness_hours`, accepts equal).

Manifest side: add a new top-level block:

```jsonc
"evidence_freshness": {
  "oldest_captured_at": "2026-06-09T22:52:13Z",  // null if no evidence
  "newest_captured_at": "2026-06-10T05:14:02Z",  // null if no evidence
  "span_hours": 6,                                // null if no evidence
  "stale": false,                                 // (now - oldest) > freshness_hours
  "threshold_hours": 24
}
```

Always present in the JSON manifest (additive, schema-stable). For
static-mode or no-evidence runs, all timestamp fields are `null` and
`stale: false`.

### D7. No new finding ID for stale evidence

A stale-evidence condition is a meta-observation about *this report*,
not a check the operator can fix in the pilot. Adding a `RDY-` finding
would inflate the scorecard with non-actionable rows.

> **Decision:** stale evidence is surfaced only in the executive
> summary bullet (D6) and in the manifest's `evidence_freshness.stale`
> boolean. No pillar finding.

### D8. Schema version: stay at 1.0

All changes are additive at the JSON level:
- `evidence_freshness` is a brand-new top-level block.
- `evidence_register[*].captured_at` already exists in v1.0 (it was
  populated but undocumented).

Per `SKILL.md` § Versioning: "Breaking changes to the JSON manifest
schema are gated behind a `schema_version` bump." Additive ⇒ no bump.

> **Decision:** `schema_version` stays at `"1.0"`. `VERSION` (tool
> version) bumps from `"0.1.0"` to `"0.2.0"` to reflect the
> rendering + exec-summary delta.

## Implementation checklist (handed to writing-plans next)

1. **Code — no shape change** to `EvidenceEntry` (field already exists).
2. **Code — `_build_manifest`**:
   - Compute `checked_at = _utc_now()` **once** at function start;
     pass the same string into the freshness math. Do not call
     `_utc_now()` again inside the freshness helper — single source of
     truth prevents flake on exact-boundary tests.
   - Compute the `evidence_freshness` block from
     `[parse(e.captured_at) for e in evidence if parseable]`. If
     `evidence` is non-empty but **zero** rows are parseable, surface a
     loud warning (`"freshness could not be evaluated for N evidence
     rows"`) and emit `oldest/newest/span = null, stale = false`.
3. **Code — `_render_report`**:
   - Add `Collected` column to Evidence register table.
   - Add staleness bullet to executive summary when
     `evidence_freshness.stale` is `true`.
4. **Docs — `SKILL.md`**:
   - Replace `ran_at` with `captured_at` in the JSON example.
   - Document the new `evidence_freshness` block.
   - Document the dual semantic of `--freshness-hours` (with the
     coupling caveat from D6).
   - Add a sentence: "Static-mode runs emit `evidence_register: []`."
5. **Docs — `references/pillars/*.md`**: no per-pillar change needed —
   the freshness field is uniform across pillars. Add one paragraph in
   the SKILL.md "Evidence" section instead. (Defends YAGNI: 13 file
   edits with identical content is noise.)
6. **Fixtures — `references/fixtures/sample-pilot/`**: nothing to
   change for static-mode (no evidence to stamp). If a live-mode
   fixture exists, regenerate it.
7. **Smoke + boundary tests** (all in
   `tests/` alongside the existing fixture — keep stdlib-only,
   matching the skill's posture):
   - **static-mode**: `--static` run → exit 0; manifest has
     `evidence_freshness` with all timestamp fields `null`,
     `stale: false`, `threshold_hours: 24`.
   - **fresh-evidence**: synthesized evidence list with `captured_at`
     5 minutes before `checked_at` → `stale: false`, **no** banner
     bullet in markdown.
   - **stale-evidence**: synthesized evidence list with one row 30h
     before `checked_at` → `stale: true`, banner bullet present.
   - **exact-boundary**: synthesized row at exactly 24h00m00s before
     `checked_at` → `stale: false` (strict `>` per D6).
   - **unparseable-only**: evidence rows whose `captured_at` won't
     parse → `oldest/newest = null`, `stale: false`, a loud warning in
     `warnings[]`.
   - **mixed parseable**: 2 parseable + 1 unparseable → freshness
     computed from the 2 parseable; warning enumerates the skipped 1.
   - **custom flag**: `--freshness-hours 72` with a 30h-old row →
     `stale: false`, `threshold_hours: 72` in manifest.
   - **#freshness-hours-coupling**: pin the documented dual semantic.
     Run with `--freshness-hours 72` against a synthesized stale
     safe-check manifest (>24h, <72h) AND stale evidence (>24h, <72h)
     → safe-check pre-flight passes AND `evidence_freshness.stale =
     false`. Locks in the coupling so a future split is a visible
     diff.
   - **clock-skew**: evidence row with `captured_at` *after*
     `checked_at` (future timestamp from skew) → `span_hours` is
     clamped to `0`, `stale: false`, warning entry. Don't crash, don't
     emit a negative number.
   - **renderer↔manifest agreement**: assert the markdown banner is
     present iff `evidence_freshness.stale == true` (catches a
     report/manifest divergence regression).
8. **Tool version**: bump `VERSION` 0.1.0 → 0.2.0.

## Out of scope (explicit)

- `threadlight-safe-check` per-resource `checked_at` (D4).
- Rename of `captured_at` → `collected_at` (D2).
- Per-evidence TTL or auto-expiry (issue scope).
- Changes to `verification_coverage` math (issue scope).
- AGT v4 deep checks ([#23](https://github.com/aiappsgbb/threadlight-skills/issues/23)).
- Per-pillar evidence rollup statistics.
- Adding a `RDY-` finding for stale evidence (D7).

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Consumer parsing breaks on new top-level `evidence_freshness` block | Low | Additive only; existing keys unchanged. Schema version stays 1.0. |
| `captured_at` field name irritates the issue author | Medium | Called out in D2. Trivial to flip in a follow-up; v0.x has no known external consumers. |
| Unparseable `captured_at` in third-party-modified manifests trips the freshness math | Low | Skip-and-warn pattern. Test fixture covers it (unparseable-only + mixed). |
| `--freshness-hours` overload confuses operators or hides stale evidence when an operator only meant to loosen safe-check tolerance | Medium | SKILL.md update calls out the coupling caveat; freshness-hours-coupling test pins behaviour; raw `oldest/newest/span` always present in manifest so strict consumers can re-evaluate against their own threshold. Split into two flags in v0.3 if feedback bites. |
| Multi-day pilot reviewer reads the report days after CLI run, sees "fresh" banner because `manifest.checked_at` is recent | Medium | The freshness check uses `manifest.checked_at`, not "now-when-reader-opens-the-PDF". This is the right invariant — the report itself is the artefact, not the reader's session. Documented in SKILL.md. |
| Strict JSON-schema consumers reject the new top-level `evidence_freshness` block despite the additive intent | Low | No published schema today; if one is published later it will be schema-version-pinned. SKILL.md `versioning` section already commits to schema-bumps only for breakage. |
| Clock skew between probe host and report host produces a `captured_at` *after* `checked_at` (negative age) | Low | Clamp `span_hours` and freshness delta to `max(0, …)`; never report `stale: true` from a negative delta; surface a warning. Covered by clock-skew test. |
| Long-running probe: `captured_at` is stamped after probe completion, so the age understates the wall-clock time the probe data was actually queried | Low | Documented in SKILL.md as a known precision caveat; for staleness on the order of hours this rounding error (seconds-to-minutes) is irrelevant. Not worth a probe-start timestamp in v0.2. |

## Open questions for reviewers

1. **D2 / field name** — accept `captured_at` (recommended), or insist
   on `collected_at` and pay the schema-bump cost?
2. **D6 / threshold flag** — reuse `--freshness-hours`
   (recommended), or split into `--safe-check-freshness-hours` +
   `--evidence-freshness-hours`?
3. **D5 / column format** — full ISO 8601 (recommended), or compact
   `YYYY-MM-DD HH:MMZ` for table density?
