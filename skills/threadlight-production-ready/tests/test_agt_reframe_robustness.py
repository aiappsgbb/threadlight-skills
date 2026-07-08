#!/usr/bin/env python3
"""Robustness pins for the AGT (agent-governance) reframe legacy heuristic path.

These lock down correctness edges an adverse review surfaced in the real-toolkit
reframe:

* AGT-001 (schema-valid) and AGT-004 (pinned ruleset version) must evaluate a
  *single canonical* policy file — never a cross-file merge, which can let a
  sibling `policy.prod.yaml` supply an anchor the real `policy.yaml` is missing
  and false-pass a must-fix hard gate.
* AGT-005 (CI gate) must require an actual toolkit *invocation* (a `agt` verb or
  the composite `agent-governance-toolkit/action`), not a bare `pip install`
  mention, and must ignore commented-out lines — matching `threadlight-govern`.
* RAI-002 (sensitive-action rules) on the legacy path must agree with the govern
  leg's severity: no policy at all is must-fix, but a policy that is present yet
  lacks deny/block/escalate rules is should-fix, not must-fix.
* A pilot that adopts a `threadlight-govern` baseline policy template must stay
  `v3_7` under auto-detection (the templates must not carry a v4-only key).

pytest-style bare ``test_`` functions + ``assert``; stdlib only.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"
GOVERN_DIR = SKILL_DIR.parent / "threadlight-govern"

sys.path.insert(0, str(SCRIPT.parent))
import production_ready as pr  # noqa: E402


def _ctx_with_files(
    policies: dict[str, str] | None = None,
    *,
    workflows: dict[str, str] | None = None,
    src: dict[str, str] | None = None,
) -> "pr.RepoContext":
    """Minimal on-disk RepoContext: policy files at root, workflows under
    .github/workflows/, src under src/. ``src_text`` mirrors the src files so
    import-based auto-detection sees them."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "specs").mkdir(parents=True, exist_ok=True)
    (tmp / "specs" / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    for name, text in (policies or {}).items():
        (tmp / name).write_text(text, encoding="utf-8")
    if workflows:
        wf = tmp / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        for name, text in workflows.items():
            (wf / name).write_text(text, encoding="utf-8")
    src_text = ""
    src_files: list[Path] = []
    if src:
        sd = tmp / "src"
        sd.mkdir(parents=True, exist_ok=True)
        for name, text in src.items():
            p = sd / name
            p.write_text(text, encoding="utf-8")
            src_files.append(p)
            src_text += "\n" + text
    bg = pr.BicepGraph(resources=[], source_files=[])
    return pr.RepoContext(
        root=tmp,
        bicep_files=[],
        src_files=src_files,
        test_files=[],
        spec_text="",
        spec_12={},
        spec_11b={},
        azure_yaml_text="",
        docs_text="",
        azd_env={},
        manifest={},
        bicep_text="",
        src_text=src_text,
        bicep_graph=bg,
    )


def _by_id(findings) -> dict[str, "pr.Finding"]:
    return {f.id: f for f in findings}


_VALID_POLICY = 'version: "1.0.0"\nname: pilot\nrules:\n  - name: block-x\n    action: deny\n'


# --- S1: AGT-001 / AGT-004 must be scoped to one canonical policy file --------

def test_agt001_rejects_cross_file_schema_anchors() -> None:
    """policy.yaml missing `rules:` must NOT be rescued by a sibling that has it."""
    ctx = _ctx_with_files({
        "policy.yaml": 'version: "1.0.0"\nname: pilot\n',            # no rules:
        "policy.prod.yaml": 'rules:\n  - name: x\n    action: deny\n',  # rules only
    })
    f = _by_id(pr._check_agt_static(ctx, "auto"))
    assert f["AGT-001"].status == "must-fix", f["AGT-001"].detail


def test_agt001_single_canonical_valid_policy_passes() -> None:
    ctx = _ctx_with_files({"policy.yaml": _VALID_POLICY})
    f = _by_id(pr._check_agt_static(ctx, "auto"))
    assert f["AGT-001"].status == "pass", f["AGT-001"].detail


def test_agt004_pin_scoped_to_canonical_policy() -> None:
    """A non-semver version in the canonical file is not rescued by a sibling's semver."""
    ctx = _ctx_with_files({
        "policy.yaml": 'version: latest\nname: pilot\nrules:\n  - name: x\n    action: deny\n',
        "policy.prod.yaml": 'version: "1.2.3"\n',
    })
    f = _by_id(pr._check_agt_static(ctx, "auto"))
    assert f["AGT-004"].status == "should-fix", f["AGT-004"].detail


def test_agt004_canonical_semver_passes() -> None:
    ctx = _ctx_with_files({"policy.yaml": _VALID_POLICY})
    f = _by_id(pr._check_agt_static(ctx, "auto"))
    assert f["AGT-004"].status == "pass", f["AGT-004"].detail


# --- S2 / N1: AGT-005 requires a real toolkit invocation, ignores comments ----

def test_agt005_install_only_workflow_is_not_a_gate() -> None:
    ctx = _ctx_with_files(
        {"policy.yaml": _VALID_POLICY},
        workflows={"ci.yml": "steps:\n  - run: pip install agent-governance-toolkit\n"},
    )
    f = _by_id(pr._check_agt_static(ctx, "auto"))
    assert f["AGT-005"].status == "should-fix", f["AGT-005"].detail


def test_agt005_commented_gate_is_not_a_gate() -> None:
    ctx = _ctx_with_files(
        {"policy.yaml": _VALID_POLICY},
        workflows={"gov.yml": "steps:\n  # - run: agt verify\n"},
    )
    f = _by_id(pr._check_agt_static(ctx, "auto"))
    assert f["AGT-005"].status == "should-fix", f["AGT-005"].detail


def test_agt005_real_agt_verb_is_a_gate() -> None:
    ctx = _ctx_with_files(
        {"policy.yaml": _VALID_POLICY},
        workflows={"gov.yml": "steps:\n  - run: agt verify --badge\n"},
    )
    f = _by_id(pr._check_agt_static(ctx, "auto"))
    assert f["AGT-005"].status == "pass", f["AGT-005"].detail


def test_agt005_composite_action_is_a_gate() -> None:
    ctx = _ctx_with_files(
        {"policy.yaml": _VALID_POLICY},
        workflows={"gov.yml": "steps:\n  - uses: myorg/agent-governance-toolkit/action@v1\n"},
    )
    f = _by_id(pr._check_agt_static(ctx, "auto"))
    assert f["AGT-005"].status == "pass", f["AGT-005"].detail


# --- N3: legacy RAI-002 severity agrees with the govern leg -------------------

def test_rai002_policy_present_but_no_sensitive_rules_is_should_fix() -> None:
    ctx = _ctx_with_files({
        "policy.yaml": 'version: "1.0.0"\nname: p\nrules:\n  - name: allow-read\n    action: allow\n',
    })
    f = _by_id(pr._check_rai_static(ctx))
    assert f["RAI-002"].status == "should-fix", f["RAI-002"].detail


def test_rai002_no_policy_is_must_fix() -> None:
    ctx = _ctx_with_files({})  # no policy artefact at all
    f = _by_id(pr._check_rai_static(ctx))
    assert f["RAI-002"].status == "must-fix", f["RAI-002"].detail


def test_rai002_policy_with_deny_rule_passes() -> None:
    ctx = _ctx_with_files({"policy.yaml": _VALID_POLICY})
    f = _by_id(pr._check_rai_static(ctx))
    assert f["RAI-002"].status == "pass", f["RAI-002"].detail


# --- N2: govern baseline templates keep an adopting pilot on v3_7 -------------

def test_govern_default_template_keeps_pilot_v3_7() -> None:
    tmpl = (GOVERN_DIR / "references" / "policy-templates" / "default.policy.yaml").read_text(encoding="utf-8")
    ctx = _ctx_with_files({"policy.yaml": tmpl}, src={"app.py": "import agent_compliance\n"})
    assert pr._detect_agt_profile(ctx, "auto") == "v3_7"


def test_govern_policy_templates_have_no_v4_only_key() -> None:
    import re
    v4key = re.compile(r"agent_control_specification_version")
    tmpl_dir = GOVERN_DIR / "references" / "policy-templates"
    offenders = [p.name for p in tmpl_dir.glob("*.policy.yaml")
                 if v4key.search(p.read_text(encoding="utf-8"))]
    sample = GOVERN_DIR / "references" / "fixtures" / "sample-wired" / "policy.yaml"
    if v4key.search(sample.read_text(encoding="utf-8")):
        offenders.append("sample-wired/policy.yaml")
    assert offenders == [], f"v4-only key leaked into v3_7 baseline policies: {offenders}"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR {fn.__name__}:\n{traceback.format_exc()}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
