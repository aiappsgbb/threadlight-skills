"""Guards the `azd ai agent` command surface the deploy path depends on.

`threadlight-deploy` drives a beta azd extension that ships often. When a
subcommand is renamed or removed upstream, nothing in this repo notices: the
failure surfaces at deploy time, in front of a customer, as an unknown command.
That is exactly how ``azd ai agent validate`` — a *mandatory* validation gate in
SKILL.md Step 6 — came to reference a subcommand that no longer exists.

These tests make ``references/azd-cli-contract.md`` machine-checked rather than
merely written down: its § 1 table is the single source of truth for which
subcommands exist, and every ``azd ai agent <cmd>`` reference anywhere in the
repo must resolve against it.

Read-only: nothing here writes to the checkout or touches the network.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
REPO_ROOT = TEST_DIR.parents[2]
CONTRACT = SKILL_DIR / "references" / "azd-cli-contract.md"

# Prose that reads like a command but isn't one, e.g. "install the azd ai agent
# extension". Each entry is an explicit, reviewed exemption — not a catch-all.
PROSE_EXEMPTIONS = {"extension"}

# Files that legitimately discuss a removed subcommand in order to warn about
# it. Without this, documenting the breakage would trip the very check that
# found it.
DOCUMENTS_REMOVED_COMMANDS = {
    SKILL_DIR / "references" / "azd-cli-contract.md",
    SKILL_DIR / "SKILL.md",
    REPO_ROOT / "CHANGELOG.md",  # a changelog must be able to name what it removed
    Path(__file__).resolve(),  # this file names the removed command to test for it
}

SEARCH_SUFFIXES = {".md", ".yml", ".yaml", ".sh", ".py"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}

COMMAND_RE = re.compile(r"azd ai agent ([a-z][a-z-]*)")


def _iter_repo_files():
    for path in REPO_ROOT.rglob("*"):
        if path.suffix not in SEARCH_SUFFIXES or not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _known_commands() -> set[str]:
    """Parse the § 1 command-surface table out of the contract file."""
    text = CONTRACT.read_text(encoding="utf-8")
    commands: set[str] = set()
    for row in re.finditer(r"^\|\s*`([^`]+)`(?:\s*/\s*`([^`]+)`)?\s*\|", text, re.M):
        for cell in row.groups():
            if cell:
                commands.add(cell.strip())
    return commands


def test_contract_file_exists():
    assert CONTRACT.is_file(), (
        "references/azd-cli-contract.md is the source of truth for the azd "
        "command surface; without it the deploy path falls back to fetching "
        "shapes off the internet at deploy time."
    )


def test_contract_declares_a_plausible_command_surface():
    commands = _known_commands()
    # Anchor on the handful the deploy path actually drives. If a refactor of
    # the table drops these, the parse is wrong and every other test here would
    # silently pass by knowing nothing.
    for essential in ("init", "show", "invoke", "doctor"):
        assert essential in commands, (
            f"`{essential}` missing from the § 1 table in {CONTRACT.name} — "
            "either upstream removed it (a real finding) or the table format "
            "changed and this parser needs updating."
        )


def test_no_repo_reference_to_an_unknown_subcommand():
    """Every `azd ai agent <cmd>` in the repo must exist per the contract."""
    known = _known_commands()
    offenders: list[str] = []

    for path in _iter_repo_files():
        if path in DOCUMENTS_REMOVED_COMMANDS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in COMMAND_RE.finditer(line):
                sub = match.group(1)
                if sub in PROSE_EXEMPTIONS or sub in known:
                    continue
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: azd ai agent {sub}")

    assert not offenders, (
        "These reference an `azd ai agent` subcommand that is not in the "
        f"pinned command surface ({CONTRACT.name} § 1). This breaks at deploy "
        "time as `unknown command`:\n  " + "\n  ".join(offenders)
    )


def test_validate_subcommand_is_not_reintroduced():
    """`validate` was removed upstream; `doctor` replaced it.

    Kept as a named regression test because this one already shipped broken
    once, in a mandatory Step 6 gate.
    """
    assert "validate" not in _known_commands(), (
        "`validate` is listed as a live command again. It does not exist in "
        "azure.ai.agents 1.0.0-beta.10 — verify against `azd ai agent --help` "
        "before re-adding it."
    )

    offenders: list[str] = []
    for path in _iter_repo_files():
        if path in DOCUMENTS_REMOVED_COMMANDS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if "azd ai agent validate" in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")

    assert not offenders, (
        "`azd ai agent validate` is back. Use `azd ai agent doctor` "
        "(exit 0 = passed, 1 = failed, 2 = all skipped):\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("flag", ["--runtime", "--entry-point", "--deploy-mode"])
def test_contract_pins_the_non_interactive_init_flags(flag):
    """`--no-prompt` fails rather than guessing, so these must be documented.

    Guessing `--runtime` was the first thing to fail in the 2026-08-16 E2E run.
    """
    text = CONTRACT.read_text(encoding="utf-8")
    assert flag in text, (
        f"{flag} is required for a non-interactive `azd ai agent init` but is "
        f"not documented in {CONTRACT.name}. An automated deploy will guess it."
    )


def test_contract_records_the_toolchain_it_was_captured_from():
    """A pinned contract with no provenance cannot be revalidated."""
    text = CONTRACT.read_text(encoding="utf-8")
    for marker in ("azd_version", "extension_version", "captured_at"):
        assert marker in text, (
            f"{CONTRACT.name} front-matter is missing `{marker}`. Without the "
            "versions it was captured from, there is no way to tell whether it "
            "still describes the pinned toolchain."
        )
