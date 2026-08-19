"""Guard the deploy-phase shell-cleanup instruction in every workload pack.

Evidence (full E2E run #32290332688, fsi-kyc-aml, Phase 3 "deploy"): the agent
reported the deployment succeeded/active at 19:43:11 (teardown later proved
the deployment existed and deleted cleanly — provisioning genuinely worked),
then the step produced no further activity until the step timeout fired at
19:50:37. The log showed an async/long-running shell (`Deploy after RBAC
grant`), polling/read-shell-output calls, and earlier `Stop shell` calls for
*other* sessions — but no final `Stop shell` cleanup after the success was
verified. A lingering shell tool session kept the Copilot CLI process alive
long after the agent's actual work was done, and the workflow's step timeout
is what eventually killed the job — not a real deployment failure.

The fix is a single instruction appended to each workload's `deploy` prompt,
directly after its `Final state required:` block, telling the agent to wait
for its own shell commands to exit and to call the `Stop shell` tool on any
still-running session (never to kill arbitrary system processes) before
sending its final response. This module is the regression guard for that
instruction: it does not touch the `design`, `local-test`, or `invoke`
phases, which never left a shell running in the incident and don't need the
instruction.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKLOADS_DIR = REPO_ROOT / ".github" / "workloads"

FINAL_STATE_MARKER = "Final state required:"
# The line that closes out the prompt on an unrecoverable error. The cleanup
# instruction must land before this "failure close" so a hung shell is
# stopped even on the happy path, not just when something goes wrong.
FAILURE_CLOSE_MARKER = "If anything fails"


def _discover_workload_packs() -> list[Path]:
    if not WORKLOADS_DIR.is_dir():
        return []
    return sorted(
        p for p in WORKLOADS_DIR.iterdir() if (p / "phases.yml").is_file()
    )


WORKLOAD_PACKS = _discover_workload_packs()


def _load_phases(pack_dir: Path) -> dict:
    doc = yaml.safe_load((pack_dir / "phases.yml").read_text(encoding="utf-8"))
    return doc["phases"]


def _deploy_prompt(pack_dir: Path) -> str:
    return _load_phases(pack_dir)["deploy"]["prompt"]


def test_workload_packs_discovered():
    """Sanity check: the fixture-discovery glob actually found real packs."""
    names = {p.name for p in WORKLOAD_PACKS}
    assert {"returns-triage", "fsi-kyc-aml"}.issubset(names), (
        f"expected both known workload packs on disk, found {names}"
    )


@pytest.mark.parametrize(
    "pack_dir", WORKLOAD_PACKS, ids=lambda p: p.name
)
def test_deploy_prompt_has_shell_cleanup_instruction(pack_dir: Path):
    """The deploy prompt must tell the agent to close its own shells.

    Root cause of the run #32290332688 timeout: deployment succeeded, but no
    instruction ever told the agent to stop the shells it had started, so a
    lingering `Stop shell`-eligible session kept Copilot CLI alive until the
    step timeout fired. Each required keyword group below is a load-bearing
    piece of that fix; dropping any one of them reproduces a plausible dodge
    (e.g. telling the agent to "wait" but never to "stop" anything).
    """
    prompt = _deploy_prompt(pack_dir)
    lower = prompt.lower()

    assert "final response" in lower, (
        "cleanup instruction must anchor on sending the final response, "
        "otherwise the agent may stop shells mid-task instead of at the end"
    )
    assert re.search(r"\bwait\b", lower), "must instruct the agent to wait"
    assert re.search(r"\bexit\b", lower), (
        "must instruct the agent to wait for shells to exit"
    )
    assert re.search(r"\bstop\b", lower) and re.search(r"\bshell\b", lower), (
        "must instruct the agent to stop shell session(s) — i.e. call the "
        "Stop shell tool — not just to 'wait'"
    )
    assert re.search(r"\basync\b", lower) or re.search(r"\bbackground\b", lower), (
        "must call out async/background shells specifically (e.g. azd up, "
        "activation pollers) since those are exactly what lingered"
    )
    assert "poll" in lower, (
        "must call out polling commands specifically, since the incident's "
        "lingering session was an activation/status poller"
    )
    assert "workflow" in lower and re.search(r"time[\s-]?out", lower), (
        "must name the actual failure mode (a workflow/step timeout) so the "
        "agent understands *why* leaving a shell running is dangerous"
    )
    # Requirement: tell the agent to use the Stop shell tool on its own
    # lingering tool sessions, never to kill arbitrary system processes.
    assert "system process" in lower or "kill arbitrary" in lower, (
        "must scope the instruction to the agent's own shell tool sessions, "
        "explicitly ruling out killing arbitrary system processes"
    )


@pytest.mark.parametrize(
    "pack_dir", WORKLOAD_PACKS, ids=lambda p: p.name
)
def test_cleanup_instruction_between_final_state_and_failure_close(pack_dir: Path):
    """Positional guard: cleanup must sit after `Final state required:` and
    before the failure-close line, so it fires on the happy (success) path
    rather than only being reachable via the error branch."""
    prompt = _deploy_prompt(pack_dir)

    final_state_idx = prompt.index(FINAL_STATE_MARKER)
    failure_close_idx = prompt.index(FAILURE_CLOSE_MARKER)
    assert final_state_idx < failure_close_idx, (
        "test fixture assumption broken: failure-close line no longer "
        "follows the Final state required: block"
    )

    cleanup_idx = prompt.lower().index("final response")
    assert final_state_idx < cleanup_idx < failure_close_idx, (
        "shell-cleanup instruction must appear after 'Final state required:' "
        "and before the failure-close instruction, so it always runs on the "
        "success path instead of being skipped"
    )


@pytest.mark.parametrize(
    "phase_name", ["design", "local-test", "invoke"]
)
@pytest.mark.parametrize(
    "pack_dir", WORKLOAD_PACKS, ids=lambda p: p.name
)
def test_non_deploy_phases_unmodified_by_this_fix(pack_dir: Path, phase_name: str):
    """The design/local-test/invoke phases never left a shell running in the
    incident and don't need the cleanup instruction — this fix is scoped to
    `deploy` only. This just guards that those phases still parse and still
    have a non-empty prompt (i.e. this fix didn't clobber them)."""
    phases = _load_phases(pack_dir)
    assert phase_name in phases, f"phase {phase_name!r} missing from pack"
    assert phases[phase_name]["prompt"].strip(), (
        f"phase {phase_name!r} prompt unexpectedly empty"
    )
