"""Guidance contract for the optional cost-actuals subphase (Task 15).

`threadlight-consumption-iq` can now collect **reconciled Azure cost actuals**
(`run --all --with-actuals`). That capability is dangerous to wire naively into
`threadlight-auto`: Cost Management usage data refreshes on its own cadence, so
a freshly deployed pilot cannot have a settled window, and an orchestrator that
"just waits for the numbers" would sleep, poll, or hold the whole chain hostage
to billing ingestion.

So the decision this file protects is *documentation-shaped on purpose*:

  1. `orchestrator.py` and `STAGES` do not change at all. Actuals are an
     optional **subphase** of the existing `cost_projection` stage — never a new
     resumability stage, never a new state key the state machine must drive.
  2. The prose an agent reads has to say, in the actuals section itself, that
     actuals are opt-in, that the default first run never collects them, that
     exit 3 / exit 5 are advisory and the chain continues, and that nothing
     polls or sleeps for Cost Management ingestion.

Both halves are asserted against the *section* that owns the topic rather than
against the whole file, so a stray mention of "opt-in" three screens away in an
unrelated table cannot satisfy the contract.

A follow-up pass (still Task 15) closes four documentation-accuracy gaps that
the first draft got wrong or left implicit, all without touching
`orchestrator.py` or `consumption_iq.py`:

  1. The `cost_projection` stage's resumability *skip* (a fresh forecast is
     reused) is orthogonal to the actuals subphase: an explicit operator
     request must still run `actuals` → `reconcile` even when the projection
     itself is skipped — never conflate "skip re-running the forecast" with
     "skip the reconciliation nobody asked to skip."
  2. `run --all --with-actuals` is one process; an exit 3 from it must be
     disambiguated by the *exact* stderr prefix `consumption_iq.py` prints,
     never assumed from the exit code alone, and a projection-side pricing
     failure must never be conflated with a reconciliation-side evidence
     failure.
  3. The CLI validates every `--with-actuals` precondition **before** it runs
     the projection at all (`_resolve_scope_or_exit`); a malformed combined
     invocation therefore exits 2 for the whole process and produces *no*
     cost-projection artefacts on that call. The old "additive, never defers
     the projection" claim was wrong on its face — the fix is prevalidation
     plus an immediate fallback re-run of the plain command, not a claim that
     the flag can't interfere.
  4. `KPI-003` reads `specs/cost-reconciliation-manifest.json` but never
     relays its self-reported figure — the number is re-derived from the
     digest-pinned `specs/cost-actuals-manifest.json`. Both facts must be
     true of the *same* paragraph, not scattered across the section.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AUTO_SKILL = REPO / "skills" / "threadlight-auto" / "SKILL.md"
ORCHESTRATOR = REPO / "skills" / "threadlight-auto" / "references" / "orchestrator.py"
STATE_SCHEMA = REPO / "skills" / "threadlight-auto" / "references" / "state-schema.md"
PROD_SKILL = REPO / "skills" / "threadlight-production-ready" / "SKILL.md"
PROD_SCRIPT = REPO / "skills" / "threadlight-production-ready" / "scripts" / "production_ready.py"
CONSUMPTION_IQ = (
    REPO / "skills" / "threadlight-consumption-iq" / "scripts" / "consumption_iq.py"
)

AUTO_VERSION = "1.2.0"
PROD_VERSION = "0.11.0"

# The stage list is a contract, not an implementation detail: production-ready,
# the Canvas control plane, and the state schema all key off these names.
EXPECTED_STAGES = [
    "preflight",
    "design",
    "deploy",
    "safe_check",
    "cost_projection",
    "invoke",
    "evals",
    "redteam",
    "govern",
]


# ---------------------------------------------------------------------------
# helpers — section-scoped reading, so assertions cannot be satisfied by a
# coincidental token somewhere else in a 300-line skill file.
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{2,4})\s+(.*)$", re.M)


def _sections(text: str) -> list[tuple[str, str]]:
    """Return `(heading, body)` for every `##`..`####` section in `text`."""
    out: list[tuple[str, str]] = []
    matches = list(_HEADING.finditer(text))
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        out.append((match.group(2).strip(), text[match.end():end]))
    return out


def _section(path: Path, *needles: str) -> str:
    """Body of the one section whose heading contains all `needles`."""
    text = path.read_text(encoding="utf-8")
    hits = [
        body
        for heading, body in _sections(text)
        if all(n.casefold() in heading.casefold() for n in needles)
    ]
    assert hits, f"{path.name}: no section heading matching {needles!r}"
    assert len(hits) == 1, f"{path.name}: {len(hits)} sections match {needles!r}; expected 1"
    return hits[0]


def _missing(body: str, phrases: tuple[str, ...]) -> list[str]:
    low = body.casefold()
    return [p for p in phrases if p.casefold() not in low]


def _paragraph_mentioning(body: str, needle: str) -> str:
    """The one blank-line-delimited paragraph in `body` that contains `needle`.

    Paragraph-scoped rather than sentence-scoped (splitting on `.` is brittle
    against markdown abbreviations, decimals and code spans) — this still
    forces two claims to live next to each other instead of being satisfied by
    a stray, unrelated mention elsewhere in a long section.
    """
    paragraphs = body.split("\n\n")
    hits = [p for p in paragraphs if needle in p]
    assert hits, f"no paragraph mentions {needle!r}"
    assert len(hits) == 1, f"{len(hits)} paragraphs mention {needle!r}; expected 1"
    return hits[0]


def _actuals_section() -> str:
    return _section(AUTO_SKILL, "actuals")


def _load_orchestrator():
    spec = importlib.util.spec_from_file_location(
        "threadlight_auto_orchestrator_guidance", str(ORCHESTRATOR)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["threadlight_auto_orchestrator_guidance"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. the auto guidance itself
# ---------------------------------------------------------------------------

def test_cost_actuals_are_opt_in_advisory_and_never_polled() -> None:
    """The plan's own token list, asserted against the actuals section."""
    body = _actuals_section()
    required = (
        "opt-in",
        "--with-actuals",
        "exit 5",
        "not-verified",
        "continue",
        "do not poll",
        "cost management",
    )
    missing = _missing(body, required)
    assert not missing, f"threadlight-auto cost-actuals guidance missing: {missing}"


def test_projection_still_runs_exactly_as_today() -> None:
    """Projection is unconditional; the flag is additive, never a replacement."""
    body = _actuals_section()
    missing = _missing(body, ("scripts/consumption_iq.py run --all", "unchanged"))
    assert not missing, f"projection-unchanged guidance missing: {missing}"
    # The stage table still names the plain projection command.
    stages = _section(AUTO_SKILL, "Sub-stages")
    assert "`scripts/consumption_iq.py run --all`" in stages


def test_actuals_precondition_failure_prevalidates_and_falls_back_to_plain_run() -> None:
    """Task 15 pt.3: the CLI validates the actuals scope *before* it runs the
    projection at all (`_resolve_scope_or_exit`), so one malformed combined
    invocation exits 2 for the whole process and writes no cost-projection
    artefacts on that call. The guidance must prevalidate first and fall back
    to the plain command — it must never claim the flag simply "can't defer"
    the projection, because a malformed one demonstrably does."""
    body = _actuals_section()
    missing = _missing(
        body,
        (
            "_resolve_scope_or_exit",
            "before it runs the projection",
            "no cost-projection artefacts",
            "run --all",
        ),
    )
    assert not missing, f"prevalidate/fallback guidance missing: {missing}"
    low = body.casefold()
    assert "exit 2" in low
    assert "prevalidat" in low
    # The disproven "additive, therefore cannot possibly interfere" claim must
    # not survive in an unqualified form.
    for overclaim in ("never defers the projection", "can never defer", "cannot possibly interfere"):
        assert overclaim not in low, f"guidance still makes the disproven claim: {overclaim!r}"


def test_default_first_deploy_never_collects_actuals() -> None:
    body = _actuals_section().casefold()
    assert "default" in body
    assert "never" in body
    assert "first" in body, "must say a first-time deploy has no settled window"


def test_actuals_require_explicit_operator_request_and_a_settled_window() -> None:
    body = _actuals_section()
    missing = _missing(
        body,
        (
            "explicit",
            "--start",
            "--end",
            "--subscription",
            "--resource-group",
            "azd env",
            "az account show",
            "tenant",
            "never guess",
            "skip",
        ),
    )
    assert not missing, f"actuals precondition guidance missing: {missing}"


def test_exit_semantics_are_documented_and_advisory() -> None:
    body = _actuals_section()
    missing = _missing(
        body,
        (
            "exit 0",
            "exit 3",
            "exit 5",
            "cost-reconciliation: not-verified",
            "already written",
            "advisory",
            "invoke",
            "evals",
            "red-team",
            "govern",
        ),
    )
    assert not missing, f"exit-code guidance missing: {missing}"


def test_exit3_is_disambiguated_by_exact_stderr_prefixes_from_the_cli() -> None:
    """Task 15 pt.2: `run --all --with-actuals` is one process, so exit 3 alone
    cannot tell the agent whether the *projection* step failed (pricing) or the
    *actuals/reconciliation* step failed (evidence). The docs must inspect the
    stderr prefix `consumption_iq.py` actually prints — asserted here as an
    exact `print(f"...")` snippet read back out of the source, so the guidance
    cannot silently drift from the code that emits the message — and must
    never conflate the two states."""
    src = CONSUMPTION_IQ.read_text(encoding="utf-8")
    exact_prints = (
        'print(f"pricing unavailable: {exc}", file=sys.stderr)',
        'print(f"cost evidence unavailable: {exc}", file=sys.stderr)',
        'print(f"cost evidence unusable: {exc}", file=sys.stderr)',
        'print(f"token evidence unusable: {exc}", file=sys.stderr)',
        'print(f"cost history conflict: {exc}", file=sys.stderr)',
        'print(f"artefact rejected before publication: {exc}", file=sys.stderr)',
    )
    for snippet in exact_prints:
        assert snippet in src, (
            f"consumption_iq.py no longer prints {snippet!r} — "
            "update this test AND the SKILL.md disambiguation table together"
        )

    body = _actuals_section()
    missing = _missing(
        body,
        (
            "pricing unavailable:",
            "cost evidence unavailable:",
            "cost evidence unusable:",
            "token evidence unusable:",
            "cost history conflict:",
            "artefact rejected before publication:",
            "cost-projection: degraded-no-pricing",
            "cost-reconciliation: degraded-source",
        ),
    )
    assert not missing, f"exit-3 disambiguation table missing: {missing}"

    low = body.casefold()
    # A guessed prefix that does not match the CLI must never be documented.
    assert "actuals source unavailable:" not in low
    # The pricing (projection-side) failure must never be recorded as a
    # reconciliation-side status, and vice versa — assert the two states are
    # not glued into a single sentence/value.
    assert "pricing unavailable: cost-reconciliation" not in low
    assert "cost evidence unavailable: cost-projection" not in low


def test_guidance_never_tells_the_agent_to_wait_for_billing_ingestion() -> None:
    body = _actuals_section().casefold()
    forbidden = (
        "poll until",
        "poll every",
        "sleep until",
        "sleep for",
        "retry loop",
        "retry until",
        "wait for the actuals",
        "wait until the window",
        "block the chain",
    )
    present = [p for p in forbidden if p in body]
    assert not present, f"actuals guidance tells the agent to wait/poll: {present}"


def test_guidance_says_the_shape_was_proven_live() -> None:
    """Task 12 probed the real API — the prose must not call it unvalidated."""
    body = _actuals_section()
    assert "live-actuals-probe.md" in body
    low = body.casefold()
    for claim in ("unvalidated", "unproven", "never been run against"):
        assert claim not in low, f"actuals guidance still calls the shape {claim!r}"


def test_actuals_are_a_subphase_not_a_stage() -> None:
    body = _actuals_section()
    missing = _missing(body, ("subphase", "cost_projection"))
    assert not missing, f"subphase framing missing: {missing}"
    low = body.casefold()
    assert "not a new stage" in low or "never a new stage" in low
    # The resumption table must not grow an actuals row.
    resumption = _section(AUTO_SKILL, "Resumption")
    assert "actuals" not in resumption.casefold()


def test_explicit_actuals_still_run_when_the_projection_stage_is_skipped() -> None:
    """Task 15 pt.1: `_check_cost_projection`'s resumability `skip` decision
    means "the fresh forecast is reused, don't re-run `run --all`" — it says
    nothing about the actuals subphase. An explicit operator request (rule 3)
    must still run `actuals` → `reconcile` on a resumed run even though the
    projection step itself was skipped; the two decisions are orthogonal and
    must never be conflated."""
    body = _actuals_section()
    missing = _missing(
        body,
        (
            "_check_cost_projection",
            "still runs the subphase",
            "scripts/consumption_iq.py actuals",
            "scripts/consumption_iq.py reconcile",
            "nothing left to re-project",
        ),
    )
    assert not missing, f"skip-vs-subphase disambiguation missing: {missing}"
    # The resumption table (a different section) must still carry no actuals
    # row — this new guidance lives only in the actuals section.
    resumption = _section(AUTO_SKILL, "Resumption")
    assert "actuals" not in resumption.casefold()


# ---------------------------------------------------------------------------
# 2. the state machine is untouched
# ---------------------------------------------------------------------------

def test_stages_unchanged_and_carry_no_actuals_stage() -> None:
    orch = _load_orchestrator()
    assert orch.STAGES == EXPECTED_STAGES
    assert "cost_actuals" not in orch.STAGES
    assert sorted(orch.STAGE_PROBES) == sorted(EXPECTED_STAGES)


def test_cost_projection_still_sits_between_safe_check_and_invoke() -> None:
    orch = _load_orchestrator()
    stages = orch.STAGES
    assert stages.index("safe_check") < stages.index("cost_projection") < stages.index("invoke")


def test_orchestrator_source_has_no_actuals_wiring() -> None:
    """Task 15 pt.6: ban the actuals-wiring tokens specifically, not the
    generic word "reconcile" — banning a common English word makes this test
    brittle against unrelated future prose (e.g. a resumability comment about
    "reconciling" state) without adding any real protection, since the
    concrete tokens below are what an actual wiring regression would add."""
    src = ORCHESTRATOR.read_text(encoding="utf-8").casefold()
    for token in ("--with-actuals", "with_actuals", "cost_actuals", "cost-actuals", "cost-reconciliation"):
        assert token not in src, f"orchestrator.py grew actuals wiring: {token!r}"


def test_state_schema_documents_cost_projection_fields_the_orchestrator_reads() -> None:
    """Task 15 pt.5: `_check_cost_projection` reads `cost_projection.last_deploy_at`
    and `cost_projection.passed_at` straight out of `.threadlight/auto-state.json`
    — that's real, not invented, and state-schema.md should say so. It
    deliberately does NOT assert a fixed shape for the agent-written
    `cost-reconciliation` sub-status: that key is written by guidance
    (SKILL.md), never read by the orchestrator, and documenting it here would
    invent a schema the code does not actually enforce."""
    schema = STATE_SCHEMA.read_text(encoding="utf-8")
    for token in ("cost_projection", "last_deploy_at", "passed_at"):
        assert token in schema, f"state-schema.md missing {token!r}"


# ---------------------------------------------------------------------------
# 3. production-ready guidance + lockstep versions
# ---------------------------------------------------------------------------

def test_production_ready_documents_forecast_always_actuals_opt_in() -> None:
    body = _section(PROD_SKILL, "Cost evidence")
    missing = _missing(
        body,
        (
            "COST-005",
            "COST-006",
            "COST-007",
            "COST-101",
            "COST-102",
            "COST-103",
            "KPI-003",
            "specs/cost-manifest.json",
            "opt-in",
            "not-verified",
            "--require-value-model",
            "exit 5",
        ),
    )
    assert not missing, f"production-ready cost-evidence guidance missing: {missing}"
    low = body.casefold()
    assert "always" in low, "forecast evidence must be described as always preserved"
    for word in ("sha-256", "fresh", "maturity"):
        assert word in low, f"strictness guidance missing: {word!r}"


def test_kpi003_reads_reconciliation_but_rederives_against_pinned_actuals() -> None:
    """Task 15 pt.4: KPI-003 reads `specs/cost-reconciliation-manifest.json`,
    but the number it reports is never that document's self-reported figure —
    it is re-derived from the digest-pinned `specs/cost-actuals-manifest.json`
    (see `production_ready.py::_read_cost_per_interaction`). Both halves of
    that claim must live in the same paragraph, not be scattered across the
    section where one could be true without the other."""
    body = _section(PROD_SKILL, "Cost evidence")
    paragraph = _paragraph_mentioning(body, "KPI-003")
    missing = _missing(
        paragraph,
        (
            "specs/cost-reconciliation-manifest.json",
            "re-derived",
            "digest-pinned",
            "specs/cost-actuals-manifest.json",
        ),
    )
    assert not missing, f"KPI-003 read-vs-rederive wording imprecise: {missing}"


def test_production_ready_legacy_pilots_keep_passing() -> None:
    body = _section(PROD_SKILL, "Cost evidence").casefold()
    assert "legacy" in body
    assert "section 14" in body or "§ 14" in body


def test_skill_versions_are_bumped_in_lockstep() -> None:
    assert f'version: "{AUTO_VERSION}"' in AUTO_SKILL.read_text(encoding="utf-8")
    assert f'version: "{PROD_VERSION}"' in PROD_SKILL.read_text(encoding="utf-8")
    assert f'VERSION = "{PROD_VERSION}"' in PROD_SCRIPT.read_text(encoding="utf-8")


def test_auto_description_stays_loadable() -> None:
    """The loader silently drops a skill whose description exceeds 1024 chars."""
    text = AUTO_SKILL.read_text(encoding="utf-8")
    front = text.split("---", 2)[1]
    block = front.split("description:", 1)[1].split("\nmetadata:", 1)[0]
    description = " ".join(line.strip() for line in block.splitlines() if line.strip())
    assert len(description) <= 1024, f"auto description is {len(description)} chars"
