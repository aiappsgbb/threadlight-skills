#!/usr/bin/env python3
"""Check that the installed official skills are the pinned release.

Compares the skills the Copilot runtime resolves (``copilot skill list --json``)
with skills/_shared/official-skills-lock.json. Every missing, disabled or
drifted official skill gets a clear message naming the fix and the
Threadlight-owned fallback recorded in skill-dependencies.json, so a pilot never
silently runs against an unpinned or absent companion.

    python3 skills/_shared/official_skills.py                       # runs `copilot skill list --json`
    python3 skills/_shared/official_skills.py --skill-list FILE     # saved listing
    python3 skills/_shared/official_skills.py --json                # machine-readable report

Exit 0 when every locked skill resolves to the pinned content, 1 otherwise.
Offline: it reads local files only and never contacts GitHub or Azure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

SHARED = Path(__file__).resolve().parent
LOCK_PATH = SHARED / "official-skills-lock.json"
MANIFEST_PATH = SHARED / "skill-dependencies.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def check(listing: list[dict], lock: dict | None = None, manifest: dict | None = None) -> dict:
    lock = lock if lock is not None else _load(LOCK_PATH)
    manifest = manifest if manifest is not None else _load(MANIFEST_PATH)
    fallbacks = manifest["official"]["fallbacks"]
    resolved = {row["name"]: row for row in listing if row.get("enabled", True)}
    rows = []
    for plugin, entry in sorted(lock["plugins"].items()):
        for skill, spec in sorted(entry["skills"].items()):
            row = {"skill": skill, "plugin": plugin, "fallback": fallbacks.get(skill)}
            found = resolved.get(skill)
            if not found:
                row["status"] = "missing"
                rows.append(row)
                continue
            base = Path(found["path"])
            mismatched = sorted(rel for rel, digest in spec["files"].items() if _sha256(base / rel) != digest)
            row.update(path=str(base), source=found.get("source"), mismatched=mismatched)
            if mismatched:
                row["status"] = "drift"
            elif found.get("source") != "plugin":
                row["status"] = "ok-not-plugin"
            else:
                row["status"] = "ok"
            rows.append(row)
    status = "ok" if all(r["status"].startswith("ok") for r in rows) else "degraded"
    return {"status": status, "tag": lock["tag"], "commit": lock["commit"], "skills": rows}


def render(report: dict) -> str:
    tag, commit = report["tag"], report["commit"]
    lines = [f"Official skills pinned at microsoft/azure-skills {tag} ({commit[:8]}): {report['status'].upper()}"]
    for row in report["skills"]:
        status, skill, plugin = row["status"], row["skill"], row["plugin"]
        if status == "ok":
            continue
        if status == "ok-not-plugin":
            lines.append(f"NOTE {skill}: pinned content, but resolved from {row['source']} copy {row['path']}, not the {plugin} plugin.")
            continue
        if status == "missing":
            lines.append(f"MISSING {skill} ({plugin}): not installed or disabled.")
            lines.append(f"  Fix: copilot plugin marketplace add microsoft/azure-skills && copilot plugin install {plugin} (release {tag}).")
        else:
            lines.append(f"DRIFT {skill}: resolved {row['source']} copy {row['path']} differs from {tag} in {', '.join(row['mismatched'])}.")
            lines.append(
                f"  Fix: update or remove that copy so the {plugin} plugin at {tag} resolves. "
                "Do not re-pin without a parity rerun (same throwaway pilot, Threadlight and official routes)."
            )
        fb = row.get("fallback") or {}
        homes = ", ".join(fb.get("threadlight") or []) or "none"
        lines.append(f"  Meanwhile: {fb.get('note', 'no fallback recorded')} Threadlight fallback: {homes}.")
    return "\n".join(lines)


def _copilot_listing() -> list[dict]:
    proc = subprocess.run(["copilot", "skill", "list", "--json"], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def _unchecked(reason: str, as_json: bool) -> int:
    msg = f"copilot skill list --json unavailable ({reason}); official skills not checked."
    print(json.dumps({"status": "unchecked", "reason": msg}, indent=2) if as_json else f"UNCHECKED: {msg}")
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skill-list", type=Path, help="saved output of `copilot skill list --json`")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    if args.skill_list:
        listing = _load(args.skill_list)
    else:
        try:
            listing = _copilot_listing()
        except FileNotFoundError:
            return _unchecked("copilot CLI not found on PATH", args.json)
        except subprocess.CalledProcessError as exc:
            return _unchecked(f"exit {exc.returncode}: {(exc.stderr or '').strip()[:200]}", args.json)
        except json.JSONDecodeError:
            return _unchecked("output is not JSON", args.json)
    report = check(listing)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
