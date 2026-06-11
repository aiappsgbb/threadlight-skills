"""Gate the SACRED ARCHITECTURAL RULE wording for self-consistency (issue #29)."""
import pathlib
import unittest


REPO = pathlib.Path(__file__).resolve().parents[3]
SKILL_MD = REPO / "skills" / "threadlight-production-ready" / "SKILL.md"
CHANGELOG = REPO / "CHANGELOG.md"


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


if __name__ == "__main__":
    unittest.main()
