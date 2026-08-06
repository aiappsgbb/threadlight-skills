#!/usr/bin/env python3
"""Sanitise an internal process_library.json into the committed static asset.

The raw source is NOT committed. This producer keeps only a whitelist of
presentation fields and asserts the output carries no supply-chain leak
markers. Generic business vocabulary in the source (e.g. audit, regulatory,
risk) is legitimate third-party content and is deliberately NOT scrubbed.

Usage:
    python3 scripts/build_process_library.py --source path/to/process_library.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KEEP = [
    "id", "name", "industry", "complexity", "summary", "use_when", "description",
    "tags", "business_constraints", "external_integrations",
    "human_approvals", "knowledge_sources",
]
LEAK = re.compile(r"agentic[- ]?loop|threadlight-vnext|northcentralus|remote-gw|gpt-5\.1", re.I)
EVENT_TAG = re.compile(r"event|trigger|schedul|webhook|cron|real[- ]?time|stream")
REGULATED_TAG = re.compile(r"regulat|complian|hipaa|gdpr|sox|pci|audit")
COMPLEXITY_LEVELS = {
    "low": "Starter",
    "medium": "Intermediate",
    "high": "Advanced",
}
CANON = [
    "threadlight-design",
    "threadlight-demo-data-factory",
    "threadlight-local-test",
    "threadlight-hitl-patterns",
    "threadlight-event-triggers",
    "threadlight-safe-check",
    "threadlight-redteam",
    "threadlight-govern",
    "threadlight-deploy",
    "threadlight-cicd",
    "threadlight-production-ready",
    "threadlight-evals",
    "threadlight-consumption-iq",
]
REGULATED = {
    "financial_services",
    "healthcare",
    "pharmaceutical",
    "insurance",
    "government",
}
PREREQUISITES = [
    "github-copilot",
    "threadlight-skills",
    "azure-subscription",
]
SKILL_ARTIFACTS = {
    "threadlight-design": [
        "specs/foundation.md",
        "specs/SPEC.md",
        "specs/manifest.json",
        "AGENTS.md",
        "src/agent/skills/*/SKILL.md",
    ],
    "threadlight-demo-data-factory": [
        "specs/sample-data/*.json",
    ],
    "threadlight-local-test": [],
    "threadlight-hitl-patterns": [
        "src/agent/skills/*/cards/*.json",
    ],
    "threadlight-event-triggers": [],
    "threadlight-safe-check": [
        "docs/safe-check-post.md",
    ],
    "threadlight-redteam": [
        "docs/redteam-report.md",
        "specs/redteam-manifest.json",
    ],
    "threadlight-govern": [
        "specs/govern-manifest.json",
    ],
    "threadlight-deploy": [
        "azure.yaml",
        "infra/main.bicep",
    ],
    "threadlight-cicd": [
        ".github/workflows/azd-deploy-prod.yml",
    ],
    "threadlight-production-ready": [
        "docs/production-readiness-report.md",
        "tests/production-readiness-manifest.json",
    ],
    "threadlight-evals": [
        "specs/evals-manifest.json",
    ],
    "threadlight-consumption-iq": [
        "docs/cost-projection.md",
        "specs/cost-manifest.json",
    ],
}


def sanitise(entry: dict) -> dict:
    if not isinstance(entry, dict):
        raise TypeError("invalid process-library entry: entry must be an object")
    return {k: entry.get(k) for k in KEEP if k in entry}


def arr(value: object) -> list:
    return value if isinstance(value, list) else []


def level_for_complexity(entry: dict) -> str:
    complexity = entry.get("complexity")
    try:
        return COMPLEXITY_LEVELS[complexity]
    except KeyError as exc:
        raise ValueError(
            f"invalid process-library entry: unsupported complexity {complexity}"
        ) from exc


def derive_use_when(entry: dict) -> str:
    if "use_when" in entry:
        use_when = entry["use_when"]
        if not isinstance(use_when, str) or not use_when.strip():
            raise ValueError("invalid process-library entry: malformed use_when")
        return use_when.strip()

    summary = entry.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("invalid process-library entry: malformed summary")
    return summary.strip()


def derive_build_skills(entry: dict) -> list[str]:
    need = {
        "threadlight-design": True,
        "threadlight-local-test": True,
        "threadlight-safe-check": True,
        "threadlight-deploy": True,
        "threadlight-cicd": True,
        "threadlight-evals": True,
    }

    if arr(entry.get("external_integrations")):
        need["threadlight-demo-data-factory"] = True
    if arr(entry.get("human_approvals")):
        need["threadlight-hitl-patterns"] = True

    tags = [str(tag).lower() for tag in arr(entry.get("tags"))]
    if any(EVENT_TAG.search(tag) for tag in tags):
        need["threadlight-event-triggers"] = True

    if entry.get("complexity") == "high":
        need["threadlight-production-ready"] = True
        need["threadlight-govern"] = True
        need["threadlight-redteam"] = True

    regulated_tag = any(REGULATED_TAG.search(tag) for tag in tags)
    if entry.get("industry") in REGULATED or regulated_tag:
        need["threadlight-consumption-iq"] = True

    return [skill for skill in CANON if need.get(skill)]


def derive_artifacts(build_skills: list[str]) -> list[str]:
    artifacts: list[str] = []
    seen: set[str] = set()

    for skill in build_skills:
        if skill not in SKILL_ARTIFACTS:
            raise ValueError(f"invalid process-library entry: no artifact mapping for {skill}")
        for artifact in SKILL_ARTIFACTS[skill]:
            if artifact not in seen:
                seen.add(artifact)
                artifacts.append(artifact)

    return artifacts


def decorate(entry: dict) -> dict:
    clean = sanitise(entry)
    build_skills = derive_build_skills(clean)
    clean["playbook"] = {
        "schema": "threadlight.playbook/v1",
        "level": level_for_complexity(clean),
        "use_when": derive_use_when(clean),
        "build_skills": build_skills,
        "run_skills": [],
        "run_skills_source": "generated-by-threadlight-design",
        "prerequisites": PREREQUISITES,
        "artifacts": derive_artifacts(build_skills),
    }
    return clean


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="path to the raw process_library.json")
    ap.add_argument("--out", default="docs/assets/process-library.json")
    a = ap.parse_args()

    try:
        raw = json.loads(Path(a.source).read_text(encoding="utf-8"))
        out = [decorate(e) for e in raw]
        blob = json.dumps(out, indent=2, ensure_ascii=False, sort_keys=False)
    except (KeyError, TypeError, ValueError) as err:
        print(err, file=sys.stderr)
        return 2

    hits = LEAK.findall(blob)
    if hits:
        print(f"LEAK markers in output: {sorted(set(h.lower() for h in hits))}", file=sys.stderr)
        return 1

    Path(a.out).write_text(blob + "\n", encoding="utf-8")
    print(f"wrote {len(out)} entries -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
