"""Guard: the shipped govern CI gate must treat `agt test` as ADVISORY.

Shipping Agent Governance Toolkit 4.1.0 carries divergent policy schemas: the
`agt lint-policy` linter and the `agent_os` runtime both accept `conditions[]`
plus the `escalate` action, but the `agt test` replay path binds to an
`agent_os` PolicyDocument model that requires a singular `condition` and only
allows `allow|deny|audit|block` (no `escalate`). A human-in-the-loop policy —
which needs `escalate` — therefore passes `agt lint-policy`, is valid at
runtime, yet FAILS `agt test`.

So any CI gate that runs `agt test` as a *hard* step goes red on a valid HITL
policy (including this skill's own `sample-wired` exemplar). This guard pins the
fix: wherever a shipped workflow runs `agt test`, that step must be advisory
(`continue-on-error: true`), while `agt lint-policy` and `agt verify` stay
required (hard) gates. No pytest / PyYAML — stdlib unittest only.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
WIRED_WORKFLOW = os.path.join(
    SKILL, "references", "fixtures", "sample-wired",
    ".github", "workflows", "governance.yml",
)

_STEP_START = re.compile(r"^\s+-\s+(name|uses|run|id):")


def _step_blocks(text):
    """Split a workflow's steps into per-step text blocks (stdlib, no YAML dep).

    A step begins at a list item under `steps:` (a line like `- name:`,
    `- run:`, `- uses:`); the block runs until the next such sibling item.
    Comment-only lines are dropped so a comment that merely *mentions* a CLI
    verb is never mistaken for a step that *runs* it.
    """
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    blocks, current = [], None
    for ln in lines:
        if _STEP_START.match(ln):
            if current is not None:
                blocks.append("\n".join(current))
            current = [ln]
        elif current is not None:
            current.append(ln)
    if current is not None:
        blocks.append("\n".join(current))
    return blocks


def _is_advisory(block):
    return re.search(r"^\s*continue-on-error:\s*true\s*$", block, re.MULTILINE) is not None


class AgtTestAdvisoryTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            os.path.isfile(WIRED_WORKFLOW),
            "sample-wired governance.yml must exist",
        )
        with open(WIRED_WORKFLOW, encoding="utf-8") as fh:
            self.text = fh.read()
        self.blocks = _step_blocks(self.text)

    def test_agt_test_step_is_advisory(self):
        test_blocks = [b for b in self.blocks if re.search(r"\bagt test\b", b)]
        self.assertTrue(
            test_blocks,
            "expected a CI step running `agt test` in the wired governance gate",
        )
        for b in test_blocks:
            self.assertTrue(
                _is_advisory(b),
                "the `agt test` step must be advisory (continue-on-error: true) "
                "because agt 4.1.0's replay schema rejects valid HITL "
                "(escalate/conditions[]) policies:\n" + b,
            )

    def test_lint_and_verify_stay_hard_gates(self):
        lint_blocks = [b for b in self.blocks if re.search(r"\bagt lint-policy\b", b)]
        verify_blocks = [b for b in self.blocks if re.search(r"\bagt verify\b", b)]
        self.assertTrue(lint_blocks, "wired gate must run `agt lint-policy`")
        self.assertTrue(verify_blocks, "wired gate must run `agt verify`")
        for b in lint_blocks + verify_blocks:
            self.assertFalse(
                _is_advisory(b),
                "`agt lint-policy` / `agt verify` are the authoritative schema + "
                "attestation gates and must stay hard (no continue-on-error):\n" + b,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
