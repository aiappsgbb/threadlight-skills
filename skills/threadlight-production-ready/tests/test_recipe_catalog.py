"""Recipe catalog enforcement gate (Phase B Task B2).

Three checks:
1. Every non-experimental must-fix finding has a recipe file.
2. Every recipe file has all required markdown sections.
3. load_recipe_catalog accepts every recipe (i.e. valid YAML front-matter
   and a `kind` from APPLY_PLAN_KINDS).

This file is RED on creation and turns green when Phase B completes
(after the last bucket in B15 lands).
"""
import importlib.util
import pathlib
import shlex
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_ready.py"
RDIR = ROOT / "references" / "remediation-recipes"
CONNECT_SCRIPT = ROOT.parent / "threadlight-connect" / "scripts" / "connect.py"

_spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod  # Python 3.14 dataclass+importlib workaround
_spec.loader.exec_module(mod)

_connect_spec = importlib.util.spec_from_file_location("threadlight_connect", CONNECT_SCRIPT)
connect = importlib.util.module_from_spec(_connect_spec)
sys.modules["threadlight_connect"] = connect
_connect_spec.loader.exec_module(connect)

REQUIRED_SECTIONS = (
    "## Target file",
    "## Edit type",
    "## Edit recipe",
    "## Verification",
)


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
    assert args.apply is True


if __name__ == "__main__":
    test_every_must_fix_has_recipe()
    test_every_recipe_has_required_sections()
    test_loader_accepts_every_recipe()
    print("OK")
