"""Gate the SACRED ARCHITECTURAL RULE wording for self-consistency (issue #29)."""
import importlib.util
import pathlib
import re
import sys
import unittest


REPO = pathlib.Path(__file__).resolve().parents[3]
SKILL_MD = REPO / "skills" / "threadlight-production-ready" / "SKILL.md"
CHANGELOG = REPO / "CHANGELOG.md"
SCRIPT = REPO / "skills" / "threadlight-production-ready" / "scripts" / "production_ready.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("production_ready_srw", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["production_ready_srw"] = mod
    spec.loader.exec_module(mod)
    return mod


class SacredRuleWording(unittest.TestCase):
    def test_skill_md_acknowledges_cicd_scaffold_exception(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertNotIn(
            "The Python script is still assessor-only. It never mutates your repo",
            text,
            "SKILL.md still claims Python never writes — contradicts --scaffold-cicd. See #29.",
        )
        self.assertIn("--scaffold-cicd", text)
        self.assertIn("exception", text.lower())

    def test_changelog_v040_entry_acknowledges_cicd_scaffold_exception(self):
        text = CHANGELOG.read_text(encoding="utf-8")
        self.assertNotIn(
            "the Python script never mutates the user's repo",
            text,
            "CHANGELOG v0.4.0 entry still claims Python never writes. See #29.",
        )

    def test_skill_md_scaffold_paths_match_actual_writer(self):
        """The two paths cited in the SACRED RULE prose must be the paths
        --scaffold-cicd actually writes. PR #34 review caught a regression
        where the prose cited fictional filenames."""
        import tempfile
        text = SKILL_MD.read_text(encoding="utf-8")
        para_match = re.search(
            r"documented exception is `--scaffold-cicd`.*?does not\s+emit remediation patches",
            text, re.DOTALL,
        )
        self.assertIsNotNone(
            para_match,
            "SACRED RULE exception paragraph not found in SKILL.md — has the prose been removed?",
        )
        para = para_match.group(0)

        # Invoke the actual writer against a tmp dir; learn the real paths.
        mod = _load_script_module()
        framing = {
            "target_subscription_id": "00000000-0000-0000-0000-000000000000",
            "target_resource_group": "rg-x",
            "target_posture": "self-hosted-flat",
            "provisioning_rights": True,
            "central_platform_team": False,
            "restricted_environment": False,
            "cicd_target": "github-actions",
            "azure_tenant_id": "11111111-1111-1111-1111-111111111111",
        }
        with tempfile.TemporaryDirectory() as tmp:
            written = mod._scaffold_cicd(framing, "octocat/demo", tmp)
            actual_paths = sorted(
                str(pathlib.Path(p).relative_to(tmp)).replace("\\", "/")
                for p in written
            )
        self.assertTrue(actual_paths, "could not parse scaffold output paths from script")

        cited_paths = re.findall(r"`([^`]+\.(?:yml|yaml|md|sh))`", para)
        self.assertTrue(
            cited_paths,
            "SACRED RULE paragraph cites no file paths — should name the 2 scaffold outputs",
        )
        for cited in cited_paths:
            self.assertIn(
                cited, actual_paths,
                f"SACRED RULE cites {cited!r} but --scaffold-cicd writes {actual_paths!r}. "
                "Fix the SKILL.md prose or the scaffold pairs.",
            )


if __name__ == "__main__":
    unittest.main()
