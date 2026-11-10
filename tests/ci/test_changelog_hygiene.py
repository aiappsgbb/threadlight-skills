from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"


def test_changelog_has_no_leftover_merge_markers():
    bad = [
        f"{n}: {line[:60]}"
        for n, line in enumerate(CHANGELOG.read_text(encoding="utf-8").splitlines(), 1)
        if line.startswith(("<<<<<<< ", "||||||| ", ">>>>>>> ")) or line == "======="
    ]
    assert not bad, bad
