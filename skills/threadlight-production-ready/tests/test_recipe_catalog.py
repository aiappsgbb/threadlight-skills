"""Recipe catalog enforcement gate (Phase B Task B2).

Checks:
1. Every non-experimental must-fix finding has a recipe file.
2. Every recipe file has all required markdown sections.
3. load_recipe_catalog accepts every recipe (i.e. valid YAML front-matter
   and a `kind` from APPLY_PLAN_KINDS).
4. Every fenced sibling-skill command in the INT/GRD/LOAD/UPG remediation
   recipes parses against the *current* argparse interface of the sibling it
   targets, and every recipe's production_ready verification command parses
   against production_ready's current CLI. This catches recipes that document
   a nonexistent subcommand (e.g. `connect.py verify`), a wrong flag
   (e.g. `ground.py --project`), or an omitted required argument
   (e.g. `loadtest.py` without `--profile`).

This file is RED on creation and turns green when Phase B completes
(after the last bucket in B15 lands).
"""
import importlib.util
import pathlib
import re
import shlex
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT.parent
SCRIPT = ROOT / "scripts" / "production_ready.py"
RDIR = ROOT / "references" / "remediation-recipes"
CONNECT_SCRIPT = SKILLS_DIR / "threadlight-connect" / "scripts" / "connect.py"


def _load_module(mod_name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module  # Python 3.14 dataclass+importlib workaround
    spec.loader.exec_module(module)
    return module


mod = _load_module("production_ready", SCRIPT)
connect = _load_module("threadlight_connect", CONNECT_SCRIPT)
ground = _load_module(
    "threadlight_ground", SKILLS_DIR / "threadlight-ground" / "scripts" / "ground.py"
)
loadtest = _load_module(
    "threadlight_loadtest", SKILLS_DIR / "threadlight-loadtest" / "scripts" / "loadtest.py"
)
upgrade = _load_module(
    "threadlight_upgrade", SKILLS_DIR / "threadlight-upgrade" / "scripts" / "upgrade.py"
)

# The four sibling legs whose remediation recipes ship runnable CLI commands.
# Maps sibling_skill -> (build_arg_parser, expected script path fragment).
SIBLING_PARSERS = {
    "threadlight-connect": (connect.build_arg_parser, "skills/threadlight-connect/scripts/connect.py"),
    "threadlight-ground": (ground.build_arg_parser, "skills/threadlight-ground/scripts/ground.py"),
    "threadlight-loadtest": (loadtest.build_arg_parser, "skills/threadlight-loadtest/scripts/loadtest.py"),
    "threadlight-upgrade": (upgrade.build_arg_parser, "skills/threadlight-upgrade/scripts/upgrade.py"),
}

# The 14 remediation recipes that invoke a sibling leg's argparse CLI. Every one
# must document commands that the current parser accepts.
SIBLING_RECIPE_IDS = [
    "INT-001", "INT-002", "INT-003", "INT-004",
    "GRD-001", "GRD-002", "GRD-003", "GRD-004",
    "LOAD-001", "LOAD-002", "LOAD-003",
    "UPG-001", "UPG-002", "UPG-003",
]

# ${UPPER_SNAKE} or <angle-placeholder> tokens sellers substitute at runtime.
_PLACEHOLDER = re.compile(r"\$\{[A-Za-z0-9_]+\}|<[^>]+>")
_BASH_BLOCK = re.compile(r"```bash\n(.*?)```", re.DOTALL)
_VERIFY_CMD = re.compile(r"`(python3 scripts/production_ready\.py[^`]*)`")

REQUIRED_SECTIONS = (
    "## Target file",
    "## Edit type",
    "## Edit recipe",
    "## Verification",
)


def _sibling_skill(text: str) -> str:
    m = re.search(r"^sibling_skill:\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else ""


def _bash_argv_blocks(text: str) -> list[list[str]]:
    """Every fenced ```bash block, line-continuations joined, shlex-split."""
    blocks = []
    for raw in _BASH_BLOCK.findall(text):
        cmd = raw.replace("\\\n", " ").strip()
        if not cmd:
            continue
        blocks.append(shlex.split(cmd))
    return blocks


def _fill(argv: list[str], fixture: str) -> list[str]:
    """Substitute every ${...}/<...> placeholder with a real fixture path/value."""
    return [_PLACEHOLDER.sub(fixture, tok) for tok in argv]


def _must_fix_ids():
    out = []
    for fid, meta in mod.FINDING_CATALOG.items():
        if meta.get("severity") == "must-fix" and not meta.get("experimental"):
            out.append(fid)
    return sorted(out)


def test_every_must_fix_has_recipe():
    missing = [fid for fid in _must_fix_ids() if not (RDIR / f"{fid}.md").exists()]
    assert not missing, (
        f"{len(missing)} must-fix finding(s) without recipe: {missing}\n"
        "→ author the recipe under references/remediation-recipes/{ID}.md"
    )


def test_every_recipe_has_required_sections():
    bad = []
    for path in sorted(RDIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        for sec in REQUIRED_SECTIONS:
            if sec not in text:
                bad.append(f"{path.name}: missing {sec!r}")
    assert not bad, "\n".join(bad)


def test_loader_accepts_every_recipe():
    mod.load_recipe_catalog(RDIR)


def test_int_002_command_matches_connect_parser_contract():
    text = (RDIR / "INT-002.md").read_text(encoding="utf-8")
    command = text.split("```bash", 1)[1].split("```", 1)[0].replace("\\\n", " ")
    argv = shlex.split(command)

    assert argv[:2] == ["python3", "skills/threadlight-connect/scripts/connect.py"]
    args = connect.build_arg_parser().parse_args(argv[2:])
    assert args.project_root == "${PROJECT_ROOT}"
    assert args.tool_name == "${TOOL_NAME}"
    assert args.tool_source_file == "${TOOL_SOURCE_FILE}"
    assert args.sample_file == "${MOCK_SAMPLE_FILE}"
    assert args.real_response_file == "${REAL_RESPONSE_FILE}"
    assert args.obo_evidence_file == "${OBO_EVIDENCE_FILE}"
    assert args.role_evidence_file == "${ROLE_EVIDENCE_FILE}"
    assert args.current_agent_identity == "${CURRENT_AGENT_IDENTITY}"
    assert args.real_endpoint == "https://api.example.com/mcp"
    assert args.apply is True


@pytest.mark.parametrize("recipe_id", SIBLING_RECIPE_IDS)
def test_sibling_recipe_commands_parse_against_current_cli(recipe_id, tmp_path):
    """Every fenced sibling command in the 14 INT/GRD/LOAD/UPG recipes must be
    accepted by the *current* argparse interface of the sibling it targets.

    Placeholders are replaced with a real temp path so argparse sees concrete
    values; the parser is only run through argument parsing (no live operation),
    so a well-formed command parses even though the fixture is otherwise empty.
    A wrong flag, a bogus positional subcommand, or an omitted required argument
    makes argparse raise SystemExit — which fails this test.
    """
    fixture = str(tmp_path / "fixture.json")
    pathlib.Path(fixture).write_text("{}", encoding="utf-8")

    text = (RDIR / f"{recipe_id}.md").read_text(encoding="utf-8")
    sibling = _sibling_skill(text)
    assert sibling in SIBLING_PARSERS, f"{recipe_id}: unexpected sibling_skill {sibling!r}"
    build_parser, script_frag = SIBLING_PARSERS[sibling]

    blocks = _bash_argv_blocks(text)
    assert blocks, f"{recipe_id}: no fenced ```bash command block found"

    for argv in blocks:
        assert argv[0] in ("python3", "python"), f"{recipe_id}: {argv!r}"
        assert argv[1] == script_frag, (
            f"{recipe_id}: command targets {argv[1]!r} but sibling_skill is {sibling!r}"
        )
        filled = _fill(argv[2:], fixture)
        parser = build_parser()
        try:
            parser.parse_args(filled)
        except SystemExit as exc:  # argparse rejected the documented command
            pytest.fail(
                f"{recipe_id}: sibling command rejected by current {sibling} CLI "
                f"(exit={exc.code}): {argv!r}"
            )


def test_connect_and_upgrade_recipes_have_no_forbidden_subcommands():
    """connect has no `verify`/`apply` positional subcommand and upgrade is
    plan-only (no `--apply`). Assert no recipe documents them — a belt-and-
    suspenders guard independent of argparse's own rejection.
    """
    problems = []
    for recipe_id in SIBLING_RECIPE_IDS:
        text = (RDIR / f"{recipe_id}.md").read_text(encoding="utf-8")
        sibling = _sibling_skill(text)
        for argv in _bash_argv_blocks(text):
            rest = argv[2:]
            if sibling == "threadlight-connect":
                if "verify" in rest or "apply" in rest:
                    problems.append(f"{recipe_id}: connect positional subcommand in {argv!r}")
            if sibling == "threadlight-upgrade":
                if "--apply" in rest:
                    problems.append(f"{recipe_id}: upgrade is plan-only, no --apply ({argv!r})")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("recipe_id", SIBLING_RECIPE_IDS)
def test_recipe_verification_command_matches_production_ready_cli(recipe_id, tmp_path):
    """Each recipe's `python3 scripts/production_ready.py ...` verification
    command must parse against production_ready's current CLI."""
    fixture = str(tmp_path / "fixture")
    text = (RDIR / f"{recipe_id}.md").read_text(encoding="utf-8")
    match = _VERIFY_CMD.search(text)
    assert match, f"{recipe_id}: no production_ready verification command found"
    argv = shlex.split(match.group(1).replace("\\\n", " "))
    assert argv[:2] == ["python3", "scripts/production_ready.py"], f"{recipe_id}: {argv!r}"
    filled = _fill(argv[2:], fixture)
    try:
        mod._parse_args(filled)
    except SystemExit as exc:
        pytest.fail(
            f"{recipe_id}: verification command rejected by production_ready CLI "
            f"(exit={exc.code}): {argv!r}"
        )


if __name__ == "__main__":
    test_every_must_fix_has_recipe()
    test_every_recipe_has_required_sections()
    test_loader_accepts_every_recipe()
    print("OK")
