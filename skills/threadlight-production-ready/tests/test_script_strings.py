"""Gate static strings in production_ready.py against staleness."""
import importlib.util
import pathlib
import re
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_ready.py"

_spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = _mod
_spec.loader.exec_module(_mod)


class ScriptStrings(unittest.TestCase):
    def test_no_stale_v050_deferred_reference(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn(
            "deferred to v0.5.0",
            text,
            "Stale string at ~L528 — ADO/GitLab are now deferred to v0.6.0+.",
        )

    def test_v060_deferred_reference_present(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("v0.6.0", text)

    def test_skill_md_finding_count_matches_catalog(self):
        """SKILL.md advertises how many findings the assessor scores against.

        It had drifted to 151 against a catalog of 171 before this guard
        existed, so the headline number was understating the tool by twenty
        checks. Every finding added from here on has to update the sentence.
        """
        skill_md = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        m = re.search(r"scores it against (\d+) findings", skill_md)
        self.assertIsNotNone(
            m, "SKILL.md no longer states a finding count — update this guard"
        )
        self.assertEqual(
            int(m.group(1)),
            len(_mod.FINDING_CATALOG),
            "SKILL.md finding count is stale vs FINDING_CATALOG",
        )


if __name__ == "__main__":
    unittest.main()
