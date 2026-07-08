"""Skill & tool supply-chain checks (SUP-008 / SUP-009).

Skills and tools an agent depends on are part of its supply chain. These
static checks assert two production disciplines:

* SUP-008 — committed automation never force-publishes a skill/tool (which
  deletes prior immutable versions and breaks pinned consumers).
* SUP-009 — when a repo consumes agent skills/tools, it pins them to a version
  for production rather than tracking a floating default.

stdlib only; bare ``test_`` functions + ``assert`` (matches sibling tests).
"""
import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "production_ready", ROOT / "scripts" / "production_ready.py"
)
pr = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = pr
_spec.loader.exec_module(pr)


def _repo(**files: str) -> "pr.RepoContext":
    """Materialize a tiny repo on disk and build a RepoContext from it.

    Keys are repo-relative paths; values are file contents. A minimal
    ``specs/SPEC.md`` is always present unless overridden.
    """
    root = pathlib.Path(tempfile.mkdtemp())
    files.setdefault("specs/SPEC.md", "# SPEC\n")
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return pr.RepoContext.from_repo(root, {})


def _by_id(findings) -> dict:
    return {f.id: f for f in findings}


# ---------------------------------------------------------------------------
# Catalog contract
# ---------------------------------------------------------------------------

def test_finding_ids_in_catalog() -> None:
    for fid in ("SUP-008", "SUP-009"):
        assert fid in pr.FINDING_CATALOG, f"{fid} missing from FINDING_CATALOG"
        meta = pr.FINDING_CATALOG[fid]
        assert meta["pillar"] == "supply-chain", f"{fid} must be under supply-chain"
        assert meta["severity"] == "should-fix", f"{fid} must be should-fix"
        assert meta["tier"] == 0, f"{fid} must be tier 0 (static)"
        assert not meta.get("experimental"), f"{fid} must not be experimental"


# ---------------------------------------------------------------------------
# SUP-008 — force-publish guard
# ---------------------------------------------------------------------------

def test_sup008_pass_on_clean_repo() -> None:
    f = _by_id(pr._check_supply_static(_repo()))
    assert f["SUP-008"].status == "pass", f["SUP-008"].detail


def test_sup008_flags_force_publish_in_azure_yaml() -> None:
    azure_yaml = (
        "hooks:\n"
        "  postprovision:\n"
        "    shell: sh\n"
        "    run: azd ai skill create --name pilot-skill --force\n"
    )
    f = _by_id(pr._check_supply_static(_repo(**{"azure.yaml": azure_yaml})))
    assert f["SUP-008"].status == "should-fix", f["SUP-008"].detail


def test_sup008_flags_force_publish_in_workflow() -> None:
    wf = (
        "jobs:\n  publish:\n    steps:\n"
        "      - run: az ml skill create -f skill.yaml --overwrite\n"
    )
    f = _by_id(pr._check_supply_static(
        _repo(**{".github/workflows/publish.yml": wf})))
    assert f["SUP-008"].status == "should-fix", f["SUP-008"].detail


def test_sup008_no_false_positive_on_file_flag() -> None:
    # `-f` here is the manifest-file flag, NOT a force flag.
    azure_yaml = (
        "hooks:\n  postprovision:\n"
        "    run: azd ai skill create -f ./skill.yaml\n"
    )
    f = _by_id(pr._check_supply_static(_repo(**{"azure.yaml": azure_yaml})))
    assert f["SUP-008"].status == "pass", f["SUP-008"].detail


# ---------------------------------------------------------------------------
# SUP-009 — version pinning
# ---------------------------------------------------------------------------

def test_sup009_not_applicable_when_no_skills() -> None:
    f = _by_id(pr._check_supply_static(_repo()))
    assert f["SUP-009"].status == "not-applicable", f["SUP-009"].detail


def test_sup009_should_fix_when_skills_used_but_unpinned() -> None:
    f = _by_id(pr._check_supply_static(
        _repo(**{"README.md": "We register a foundry toolbox for the agent.\n"})))
    assert f["SUP-009"].status == "should-fix", f["SUP-009"].detail


def test_sup009_pass_when_version_pinned() -> None:
    spec = (
        "# SPEC\n## Skills\n"
        "The agent consumes a foundry toolbox pinned to SkillVersion 3.\n"
        "default_version: 3\n"
    )
    f = _by_id(pr._check_supply_static(_repo(**{"specs/SPEC.md": spec})))
    assert f["SUP-009"].status == "pass", f["SUP-009"].detail


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in fns:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
