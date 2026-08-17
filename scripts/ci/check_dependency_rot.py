#!/usr/bin/env python3
"""Detect dependency rot in the Pattern 0 quickstart before it breaks a run.

## Why

Twice now, the design->deploy path broke because a pinned dependency stopped
resolving upstream while every test in the repo stayed green. Nothing in the
repo installs those packages, so the only sensor was a ~$1 manually-dispatched
E2E workflow. Six weeks passed before anyone noticed.

Rot comes in two shapes, and they need different responses:

**Hard rot** — the specifier no longer matches any released version. The build
is already broken for everyone; we just haven't run it yet. This fails the
check.

**Soft rot** — the pin still resolves, but upstream has moved far ahead, so we
are testing against a version nobody uses any more and quietly accumulating an
upgrade cliff. This reports, and only fails when asked to.

A third signal matters as much as either: **headroom**. A specifier matching
exactly one published version is one yank away from hard rot, even though it
resolves fine today.

## What this does not do

It checks *specifiers against published versions*. It cannot see a broken
extra or a conflicting transitive dependency — which is exactly what broke the
quickstart the first time. Only a real resolver sees that, so the accompanying
workflow also runs `pip install --dry-run`. The two are complementary; neither
replaces the other.

Exit 0 when no hard rot, 1 otherwise (or on soft rot with --fail-on-drift).
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PYPROJECT = (
    REPO_ROOT / "skills" / "threadlight-local-test" / "references" / "quickstart" / "pyproject.toml"
)

VersionLookup = Callable[[str], list[str]]


@dataclass
class Finding:
    name: str
    specifier: str
    matching: list[str]
    latest: str | None
    severity: str  # "hard" | "headroom" | "drift" | "ok"
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def hard(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "hard"]

    @property
    def headroom(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "headroom"]

    @property
    def drift(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "drift"]


def parse_requirements(pyproject: Path) -> list[Requirement]:
    """Every requirement the quickstart declares, base plus every extra.

    Extras are included because the E2E workflow and the workshop both install
    `quickstart[aoai]`, so an extra that stops resolving breaks them even
    though the base install is fine.
    """
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    raw: list[str] = list(project.get("dependencies", []))
    for extra_deps in (project.get("optional-dependencies") or {}).values():
        raw.extend(extra_deps)

    seen: dict[str, Requirement] = {}
    for line in raw:
        req = Requirement(line)
        # Same package can appear in several extras with the same specifier;
        # keep one entry per (name, specifier) pair.
        seen[f"{req.name}{req.specifier}"] = req
    return [seen[k] for k in sorted(seen)]


def _usable_versions(raw: Iterable[str]) -> list[Version]:
    out: list[Version] = []
    for candidate in raw:
        try:
            version = Version(candidate)
        except InvalidVersion:
            continue
        if version.is_prerelease or version.is_devrelease:
            continue
        out.append(version)
    return sorted(out)


def evaluate(
    requirements: list[Requirement],
    lookup: VersionLookup,
    *,
    max_minor_drift: int = 6,
) -> Report:
    """Pure evaluation, so the logic is testable without touching the network."""
    report = Report()

    for req in requirements:
        try:
            published = _usable_versions(lookup(req.name))
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            report.errors.append(f"{req.name}: could not list versions ({exc})")
            continue

        if not published:
            report.findings.append(
                Finding(
                    req.name,
                    str(req.specifier),
                    [],
                    None,
                    "hard",
                    "no stable versions published at all",
                )
            )
            continue

        latest = published[-1]
        matching = [v for v in published if req.specifier.contains(v)]

        if not matching:
            report.findings.append(
                Finding(
                    req.name,
                    str(req.specifier),
                    [],
                    str(latest),
                    "hard",
                    f"specifier matches no published version; latest is {latest}",
                )
            )
            continue

        newest_match = matching[-1]

        if len(matching) == 1:
            report.findings.append(
                Finding(
                    req.name,
                    str(req.specifier),
                    [str(v) for v in matching],
                    str(latest),
                    "headroom",
                    f"only {newest_match} satisfies this pin — a single yank away "
                    f"from a broken build (latest published: {latest})",
                )
            )
            continue

        drift = _minor_distance(newest_match, latest)
        if drift > max_minor_drift:
            report.findings.append(
                Finding(
                    req.name,
                    str(req.specifier),
                    [str(v) for v in matching],
                    str(latest),
                    "drift",
                    f"pinned at {newest_match} but {latest} is published "
                    f"({_describe_distance(newest_match, latest)})",
                )
            )
            continue

        report.findings.append(
            Finding(
                req.name,
                str(req.specifier),
                [str(v) for v in matching],
                str(latest),
                "ok",
                f"resolves to {newest_match}",
            )
        )

    return report


def _minor_distance(pinned: Version, latest: Version) -> int:
    """Rough distance in minor releases; a major bump counts as a big jump.

    Deliberately approximate. This drives a warning threshold, not a gate, so
    'clearly far behind' is all it needs to express. `_describe_distance`
    renders the human-facing wording, because "101 minor releases ahead" is a
    true statement about this number and a useless thing to read.
    """
    if latest.major > pinned.major:
        return 100 * (latest.major - pinned.major) + latest.minor
    return max(0, latest.minor - pinned.minor)


def _describe_distance(pinned: Version, latest: Version) -> str:
    major_gap = latest.major - pinned.major
    if major_gap == 1:
        return "a major release behind"
    if major_gap > 1:
        return f"{major_gap} major releases behind"
    return f"{max(0, latest.minor - pinned.minor)} minor releases behind"


def fetch_pypi_versions(name: str) -> list[str]:
    url = f"https://pypi.org/pypi/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    return list(payload.get("releases", {}))


def render(report: Report, pyproject: Path) -> str:
    lines = [f"Dependency rot report for {pyproject}", ""]
    order = {"hard": 0, "headroom": 1, "drift": 2, "ok": 3}
    label = {"hard": "BROKEN ", "headroom": "FRAGILE", "drift": "BEHIND ", "ok": "ok     "}
    for finding in sorted(report.findings, key=lambda f: (order[f.severity], f.name)):
        lines.append(f"  [{label[finding.severity]}] {finding.name}{finding.specifier} — {finding.detail}")
    if report.errors:
        lines.append("")
        lines.append("  Lookup errors (not treated as rot):")
        lines.extend(f"    - {err}" for err in report.errors)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT)
    parser.add_argument(
        "--max-minor-drift",
        type=int,
        default=6,
        help="how many minor releases behind latest before reporting drift",
    )
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="also exit non-zero for soft rot (drift / low headroom)",
    )
    args = parser.parse_args(argv)

    if not args.pyproject.is_file():
        print(f"ERROR: {args.pyproject} not found", file=sys.stderr)
        return 1

    requirements = parse_requirements(args.pyproject)
    report = evaluate(requirements, fetch_pypi_versions, max_minor_drift=args.max_minor_drift)

    print(render(report, args.pyproject))

    if report.hard:
        print(
            f"\nFAIL: {len(report.hard)} dependency specifier(s) match nothing on PyPI. "
            "The quickstart is already unbuildable.",
            file=sys.stderr,
        )
        return 1

    soft = report.headroom + report.drift
    if soft:
        print(f"\n{len(soft)} soft-rot warning(s). Nothing is broken yet.")
        if args.fail_on_drift:
            return 1

    if not soft:
        print("\nAll pins resolve with headroom and are reasonably current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
