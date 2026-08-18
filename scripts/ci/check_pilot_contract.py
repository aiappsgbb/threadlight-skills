#!/usr/bin/env python3
"""Check the design -> deploy artifact contract for a generated pilot.

## Why this exists

`threadlight-design` writes a pilot; `threadlight-local-test` wires it up;
`threadlight-deploy` consumes it. The *shape* of what crosses those boundaries
is a contract: SPEC.md must have § 13, sample-data must be real JSON,
`.env.local` must carry a resolved endpoint, Phase 1.5 must record a posture.

Until now that contract lived only as ~130 lines of inline bash inside
`.github/workflows/threadlight-e2e-foundry.yml`. That has three problems:

1. It runs only in a manually-dispatched workflow that costs ~$1 and ~90
   minutes, so in practice it ran once every few weeks.
2. It cannot be run locally, so a contributor cannot check their change before
   pushing.
3. It only ever sees a *freshly generated* pilot, so drift between the shipped
   reference pilot (`examples/returns-triage-governed`) and the contract goes
   unnoticed.

This script is the same contract, expressed once, runnable anywhere, in
milliseconds, for free. pytest runs it against the shipped example on every PR.

## Profiles are not optional decoration

The workflow drives a *Fast-PoC demo-sandbox* pilot. The shipped example is a
*governed customer-pilot*. They legitimately differ:

- Fast-PoC skips SPEC Step 1.5, so it must emit a silent-defaults callout in
  § 13. A governed pilot collected that context for real and must not.
- The workflow's pilot has `deployment_target: demo-sandbox`; the shipped one
  has `customer-pilot`.
- `.env.local` holds a live endpoint and is `.gitignore`d, so it exists in the
  workflow's workspace and never in the repo.

A naive extraction that hardcoded the workflow's expectations would fail
immediately on the shipped example. Hence `--profile` and `--stage`: the
structural checks are shared, the variable parts are declared by the caller.

Exit 0 when every requested check passes, 1 otherwise. Every failure prints the
rule id, so a failure is greppable rather than a wall of text.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Artifacts that must exist and be non-empty, per stage. Keyed by stage so the
# caller can check the design contract before deploy has had a chance to run.
REQUIRED_FILES: dict[str, tuple[str, ...]] = {
    "design": ("specs/SPEC.md", "AGENTS.md", "tests/killer-prompts.md"),
    "pattern0": (".env.local",),
    "deploy": ("specs/deployment-posture.md",),
}

# Placeholder tokens that mean the agent templated a file but never resolved it.
PLACEHOLDER_PATTERNS = (
    r"<your-aoai-resource>",
    r"<your-deployment-name>",
    r"<placeholder>",
)

VALID_DEPLOYMENT_TARGETS = ("demo-sandbox", "customer-pilot", "production-bound")

# Semantic markers the Fast-PoC silent-defaults callout must carry. Requiring
# several distinct markers stops a stray "Fast-PoC" mention elsewhere in § 13
# from passing the check by accident.
FAST_POC_MARKERS: tuple[tuple[str, str], ...] = (
    (r"fast.?poc", "'Fast-PoC' marker"),
    (r"not collected", "'not collected' phrase"),
    (r"neutral.+defaults|demo defaults", "'neutral/demo defaults' phrase"),
)

# Matches any top-level numbered SPEC heading, including lettered subsections
# such as `## 13b.` (which share section 13's leading integer).
_TOP_LEVEL_HEADING = re.compile(r"^##[ \t]+(\d+)[.\w]*\.", re.MULTILINE)

# The five markers a well-formed SPEC section 14 (Value Model) must carry.
# Numeric values are deliberately not validated here — an incomplete policy
# is a valid design state (it becomes `not-verified` downstream); only the
# presence of the shape is checked.
VALUE_MODEL_MARKERS: tuple[str, ...] = (
    "value_model:",
    "maturity_policy:",
    "success_event:",
    "baseline:",
    "accounting:",
)


class Failures:
    """Collects failures so one run reports every problem, not just the first."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def add(self, rule: str, message: str) -> None:
        self.items.append((rule, message))

    def __bool__(self) -> bool:
        return bool(self.items)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def extract_section(spec_text: str, number: int) -> str | None:
    """Return the body of top-level SPEC section `number`, or None if absent.

    Scans every later top-level numbered heading (`## N.` or a lettered
    subsection such as `## 13b.`) in document order and stops at the first
    one whose leading integer is strictly greater than `number`. A heading
    with an equal or lower integer — even one that appears out of order,
    such as a stray `## 12.` inside section 13's body — does NOT stop the
    section: only a strictly greater number is a boundary. A lettered
    subsection such as `## 13b.` shares section 13's leading integer (13),
    so it is never a boundary either and stays inside the section. A
    *decimal* sub-numbering such as `## 13.1 ...`, by contrast, is not this
    section's start heading at all: the heading regex requires whitespace
    or end-of-line right after the numbered dot, so `13.1` never matches
    section `13`'s start.

    Known limitation: headings inside a fenced code block (``` ``` ```)
    are not parsed specially here and can still act as section boundaries.
    A generated SPEC contract must therefore never place a top-level
    numbered heading (`## N. ...`) inside a fence.
    """
    if isinstance(number, bool) or not isinstance(number, int):
        raise TypeError(f"section number must be a real int, got {type(number).__name__}")

    start = re.search(
        rf"^##[ \t]+{number}\.(?=[ \t]|$)[^\n]*$",
        spec_text,
        flags=re.MULTILINE,
    )
    if start is None:
        return None

    tail = spec_text[start.end():]
    for later in _TOP_LEVEL_HEADING.finditer(tail):
        if int(later.group(1)) > number:
            return tail[: later.start()]
    return tail


def extract_section_13(spec_text: str) -> str | None:
    """Return the body of SPEC § 13, or None if the section is absent.

    § 13 is the last numbered section in the speckit template before § 14
    (Value Model), so the section normally runs up to § 14. We still stop at
    any later top-level numbered heading in case the template grows further,
    and we deliberately do *not* stop at lettered subsections like
    `## 13b.`, which belong to § 13.
    """
    return extract_section(spec_text, 13)


def check_design(
    pilot: Path, profile: str, fail: Failures, require_value_model: bool = False
) -> None:
    spec_path = pilot / "specs" / "SPEC.md"

    sample_dir = pilot / "specs" / "sample-data"
    if not sample_dir.is_dir():
        fail.add("design.sample-data.missing", f"no {sample_dir.relative_to(pilot)} directory")
    else:
        # The workflow only checked `-size +10c`. A 200-byte file of broken JSON
        # would pass that and then blow up at run time, so parse it properly.
        good: list[str] = []
        for candidate in sorted(sample_dir.glob("*.json")):
            raw = _read(candidate)
            if len(raw.strip()) <= 10:
                continue
            try:
                json.loads(raw)
            except json.JSONDecodeError as exc:
                fail.add(
                    "design.sample-data.invalid-json",
                    f"{candidate.relative_to(pilot)} is not valid JSON: {exc}",
                )
                continue
            good.append(candidate.name)
        if not good:
            fail.add(
                "design.sample-data.empty",
                f"no non-trivial valid JSON in {sample_dir.relative_to(pilot)}",
            )

    if not spec_path.is_file():
        return  # already reported by the required-files check

    spec_text = _read(spec_path)
    section13 = extract_section_13(spec_text)
    if section13 is None:
        fail.add(
            "design.spec.no-section-13",
            "SPEC.md § 13 (Assumptions & Open Questions) not found — "
            "threadlight-design >= 1.7.0 must emit it",
        )
        return

    # Section 14 (Value Model) is checked here — before the fast-poc-only
    # early return below — so a governed profile is validated too. Absence
    # is opt-in (a pilot authored before this design must keep passing
    # unless the caller asks for it via --require-value-model); a *present*
    # section 14 is always shape-checked, flag or not, because shipping a
    # half-written section 14 is asserting the new contract.
    section14 = extract_section(spec_text, 14)
    if section14 is None:
        if require_value_model:
            fail.add(
                "design.spec.no-section-14",
                "SPEC.md section 14 Value Model is missing",
            )
    else:
        # Each marker is a YAML key (e.g. `value_model:`) that must start a
        # non-comment line, after leading indentation: a marker that only
        # ever appears inside a `#`-prefixed comment — such as the probe
        # text `# removed value_model:` this file's own tests inject — is a
        # commented-out mention, not a live key, and must never satisfy the
        # check. A plain substring test cannot tell the two apart, so anchor
        # the match to line-start instead.
        missing = [
            marker
            for marker in VALUE_MODEL_MARKERS
            if not re.search(rf"^[ \t]*{re.escape(marker)}", section14, re.MULTILINE)
        ]
        if missing:
            fail.add(
                "design.spec.value-model-shape",
                "SPEC section 14 is missing: " + ", ".join(missing),
            )

    if profile != "fast-poc":
        return

    flat = " ".join(section13.split())
    for pattern, label in FAST_POC_MARKERS:
        if not re.search(pattern, flat, re.IGNORECASE):
            fail.add(
                "design.spec.fast-poc-callout",
                f"SPEC § 13 silent-defaults callout missing {label}",
            )


def check_pattern0(pilot: Path, fail: Failures) -> None:
    env_path = pilot / ".env.local"
    if not env_path.is_file():
        return  # already reported by the required-files check

    text = _read(env_path)

    for pattern in PLACEHOLDER_PATTERNS:
        if re.search(pattern, text):
            fail.add(
                "pattern0.env.placeholder",
                f".env.local still contains the unresolved placeholder {pattern}",
            )

    lines = [line.strip() for line in text.splitlines()]

    if "LLM_BACKEND=aoai" not in lines:
        fail.add("pattern0.env.backend", ".env.local missing exact line 'LLM_BACKEND=aoai'")

    if not any(re.fullmatch(r"AZURE_OPENAI_ENDPOINT=https://.+", line) for line in lines):
        fail.add(
            "pattern0.env.endpoint",
            ".env.local missing a well-formed AZURE_OPENAI_ENDPOINT=https://... line",
        )

    if not any(re.fullmatch(r"AZURE_OPENAI_DEPLOYMENT=.+", line) for line in lines):
        fail.add("pattern0.env.deployment", ".env.local missing a non-empty AZURE_OPENAI_DEPLOYMENT")


def check_deploy(pilot: Path, expected_target: str | None, fail: Failures) -> None:
    posture = pilot / "specs" / "deployment-posture.md"
    if not posture.is_file():
        return  # already reported by the required-files check

    text = _read(posture)
    match = re.search(r"^deployment_target:\s*([A-Za-z0-9-]+)", text, re.MULTILINE)
    if match is None:
        fail.add(
            "deploy.posture.no-target",
            "deployment-posture.md has no 'deployment_target:' line — "
            "threadlight-deploy Phase 1.5 did not record a decision",
        )
        return

    target = match.group(1)
    if target not in VALID_DEPLOYMENT_TARGETS:
        fail.add(
            "deploy.posture.unknown-target",
            f"deployment_target '{target}' is not one of {', '.join(VALID_DEPLOYMENT_TARGETS)}",
        )
    elif expected_target is not None and target != expected_target:
        fail.add(
            "deploy.posture.wrong-target",
            f"deployment_target is '{target}', expected '{expected_target}'",
        )


def run_checks(
    pilot: Path,
    stages: list[str],
    profile: str,
    expected_target: str | None,
    require_value_model: bool = False,
) -> Failures:
    fail = Failures()

    if not pilot.is_dir():
        fail.add("pilot.missing", f"pilot directory does not exist: {pilot}")
        return fail

    for stage in stages:
        for rel in REQUIRED_FILES[stage]:
            path = pilot / rel
            if not path.is_file() or path.stat().st_size == 0:
                fail.add(f"{stage}.file.missing", f"missing or empty: {rel}")

    if "design" in stages:
        check_design(pilot, profile, fail, require_value_model=require_value_model)
    if "pattern0" in stages:
        check_pattern0(pilot, fail)
    if "deploy" in stages:
        check_deploy(pilot, expected_target, fail)

    return fail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the design -> deploy artifact contract for a pilot.",
    )
    parser.add_argument("pilot_dir", type=Path, help="root of the generated pilot")
    parser.add_argument(
        "--stage",
        action="append",
        dest="stages",
        choices=sorted(REQUIRED_FILES),
        help="stage to check; repeatable. Defaults to design + deploy.",
    )
    parser.add_argument(
        "--profile",
        default="governed",
        choices=("governed", "fast-poc"),
        help="fast-poc additionally requires the SPEC § 13 silent-defaults callout",
    )
    parser.add_argument(
        "--expect-deployment-target",
        default=None,
        choices=VALID_DEPLOYMENT_TARGETS,
        help="assert the recorded posture target matches exactly",
    )
    parser.add_argument(
        "--require-value-model",
        action="store_true",
        help=(
            "fail if SPEC.md section 14 (Value Model) is absent. Opt-in for now "
            "so pilots authored before this contract keep passing unchanged; "
            "intended to become the default once legacy pilots have migrated. "
            "A section 14 that IS present is always shape-checked regardless "
            "of this flag."
        ),
    )
    args = parser.parse_args(argv)

    stages = args.stages or ["design", "deploy"]
    pilot = args.pilot_dir

    fail = run_checks(
        pilot,
        stages,
        args.profile,
        args.expect_deployment_target,
        require_value_model=args.require_value_model,
    )

    if fail:
        print(f"Pilot contract FAILED for {pilot} ({len(fail.items)} problem(s)):", file=sys.stderr)
        for rule, message in fail.items:
            print(f"  [{rule}] {message}", file=sys.stderr)
        return 1

    print(f"Pilot contract OK for {pilot} (stages: {', '.join(stages)}, profile: {args.profile})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
