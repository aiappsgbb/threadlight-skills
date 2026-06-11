"""Gate v0.4.0 recipes against smuggled-in ADO/GitLab guidance (issue #32, locked invariant 5)."""
import pathlib
import re
import unittest


RECIPES_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "references"
    / "remediation-recipes"
)

# v0.4.0 + v0.5.0 ship GitHub Actions only. ADO + GitLab are deferred to v0.6.0+.
# Recipes referencing them in body text mislead the apply-plan dispatcher.
FORBIDDEN_TOKENS = (
    "azure-pipelines.yml",
    "azure-devops",
    "Azure DevOps",
    ".gitlab-ci.yml",
    "GitLab CI",
)


class NoAdoOrGitlabInRecipes(unittest.TestCase):
    def test_no_recipe_mentions_ado_or_gitlab_yaml_filenames(self):
        offenders = []
        for md in RECIPES_DIR.glob("*.md"):
            if md.name == "_template.md":
                continue
            text = md.read_text(encoding="utf-8")
            for token in FORBIDDEN_TOKENS:
                if token in text:
                    offenders.append(f"{md.name}: contains forbidden token '{token}'")
        self.assertEqual(
            offenders,
            [],
            "Recipes ship GitHub Actions only in v0.5.0. See locked invariant #5.\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
