"""Gate static strings in production_ready.py against staleness."""
import pathlib
import unittest


SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "production_ready.py"
)


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


if __name__ == "__main__":
    unittest.main()
