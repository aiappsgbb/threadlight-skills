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
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AUTO_SKILL = REPO / "skills" / "threadlight-auto" / "SKILL.md"
ORCHESTRATOR = REPO / "skills" / "threadlight-auto" / "references" / "orchestrator.py"
PROD_SKILL = REPO / "skills" / "threadlight-production-ready" / "SKILL.md"
PROD_SCRIPT = REPO / "skills" / "threadlight-production-ready" / "scripts" / "production_ready.py"

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
    src = ORCHESTRATOR.read_text(encoding="utf-8").casefold()
    for token in ("--with-actuals", "with_actuals", "cost_actuals", "cost-actuals", "reconcile"):
        assert token not in src, f"orchestrator.py grew actuals wiring: {token!r}"


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
