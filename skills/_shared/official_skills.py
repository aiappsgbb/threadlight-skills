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
    python3 skills/_shared/official_skills.py --plugin-dir DIR ...  # session started with --plugin-dir

Official plugins loaded with ``copilot --plugin-dir DIR`` are visible to
``copilot skill list`` only when the same flags precede the subcommand. Pass
them with repeatable ``--plugin-dir`` or ``THREADLIGHT_PLUGIN_DIRS``
(os.pathsep-separated); otherwise the check reports false MISSING/DRIFT.

Install order matters: Copilot CLI (observed on 1.0.91) describes plugin skills
in plugin install order up to a prompt budget and lists the rest by name only.
When the official plugins are installed before threadlight-skills, no
threadlight-* skill is described and Threadlight prompts route to official
skills (for example "deploy the pilot" to azure-deploy). The listing follows
the same order, so the check reports ORDER with the reinstall fix. The budget is
shared with personal skills and every other plugin listed before
threadlight-skills, so the check also simulates it (about 15,000 characters of
name, description and markup, calibrated on CLI 1.0.91 listings) and reports
ORDER when threadlight-auto, -design or -deploy would be listed by name only.

Exit 0 when every locked skill resolves to the pinned content, 1 otherwise.
Offline: it reads local files only and never contacts GitHub or Azure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

SHARED = Path(__file__).resolve().parent
LOCK_PATH = SHARED / "official-skills-lock.json"
MANIFEST_PATH = SHARED / "skill-dependencies.json"
OFFICIAL_PLUGINS = ("azure@azure-skills", "azure-cost@azure-skills", "foundry-iq-skills@azure-skills")
DESCRIPTION_BUDGET_CHARS = 15000
ENTRY_OVERHEAD_CHARS = 80
ROUTING_SKILLS = ("threadlight-auto", "threadlight-design", "threadlight-deploy")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _routing_order(listing: list[dict], official: set[str]) -> str:
    plugin_rows = [r for r in listing if r.get("enabled", True) and r.get("source") == "plugin"]
    tl = next((i for i, r in enumerate(plugin_rows) if r["name"].startswith("threadlight-")), None)
    off = next((i for i, r in enumerate(plugin_rows)
                if r["name"].split(":")[-1] in official and not _other_marketplace(r)), None)
    if tl is None or off is None:
        return "unknown"
    return "threadlight-first" if tl < off else "official-first"


def _other_marketplace(row: dict) -> bool:
    parts = Path(row.get("path") or "").parts
    if "installed-plugins" not in parts:
        return False
    i = parts.index("installed-plugins")
    return len(parts) > i + 1 and parts[i + 1] != "azure-skills"


def _plugin_label(row: dict) -> str:
    parts = Path(row.get("path") or "").parts
    if row.get("source") == "plugin" and "installed-plugins" in parts:
        i = parts.index("installed-plugins")
        if len(parts) > i + 2 and parts[i + 1] == "_direct":
            root = Path(*parts[: i + 3])
            try:
                name = _load(root / "plugin.json").get("name")
            except (OSError, ValueError, AttributeError):
                name = None
            segments = parts[i + 2].split("--")
            source = "/".join(segments) if len(segments) == 2 else "its original source"
            return f"{name or parts[i + 2]} ({source})"
        if len(parts) > i + 2:
            return f"{parts[i + 2]}@{parts[i + 1]}"
    return "personal skills" if row.get("source") != "plugin" else "plugin skills"


def _routing_budget(listing: list[dict]) -> tuple[list[str], list[str]]:
    """Return (threadlight skills listed by name only, sources described before them)."""
    rows = [r for r in listing if r.get("enabled", True) and r.get("source") != "builtin"]
    used, overflow, seen_threadlight, ahead = 0, False, False, []
    name_only = []
    for row in rows:
        name = row["name"].split(":")[-1]
        cost = len(name) + len(row.get("description") or "") + ENTRY_OVERHEAD_CHARS
        overflow = overflow or used + cost > DESCRIPTION_BUDGET_CHARS
        if not overflow:
            used += cost
        if name.startswith("threadlight-"):
            if overflow:
                name_only.append(name)
        elif not seen_threadlight:
            label = _plugin_label(row)
            if label not in ahead:
                ahead.append(label)
        seen_threadlight = seen_threadlight or name.startswith("threadlight-")
    return name_only, ahead


def check(listing: list[dict], lock: dict | None = None, manifest: dict | None = None) -> dict:
    lock = lock if lock is not None else _load(LOCK_PATH)
    manifest = manifest if manifest is not None else _load(MANIFEST_PATH)
    fallbacks = manifest["official"]["fallbacks"]
    resolved = {row["name"]: row for row in listing if row.get("enabled", True)}
    rows = []
    for plugin, entry in sorted(lock["plugins"].items()):
        for skill, spec in sorted(entry["skills"].items()):
            row = {"skill": skill, "plugin": plugin, "fallback": fallbacks.get(skill)}
            # Colliding names are listed as `<plugin>:<skill>`; prefer the pinned plugin's copy.
            found = resolved.get(f"{plugin.split('@')[0]}:{skill}") or resolved.get(skill)
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
    official = {skill for entry in lock["plugins"].values() for skill in entry["skills"]}
    order = _routing_order(listing, official)
    name_only, ahead = _routing_budget(listing)
    crowded = any(s in name_only for s in ROUTING_SKILLS)
    ok = all(r["status"].startswith("ok") for r in rows) and order != "official-first" and not crowded
    return {"status": "ok" if ok else "degraded", "tag": lock["tag"], "commit": lock["commit"],
            "routing_order": order, "routing_name_only": name_only, "routing_ahead": ahead if crowded else [],
            "skills": rows}


def render(report: dict) -> str:
    tag, commit = report["tag"], report["commit"]
    lines = [f"Official skills pinned at microsoft/azure-skills {tag} ({commit[:8]}): {report['status'].upper()}"]
    if report.get("routing_order") == "official-first":
        lines.append("ORDER official plugins are installed before threadlight-skills: Copilot describes skills in "
                     "install order within a prompt budget, so threadlight-* skills are listed by name only and "
                     "Threadlight prompts can route to official skills.")
        lines.append("  Fix: reinstall the official plugins after threadlight-skills:")
        lines.append("    " + " && ".join(f"copilot plugin uninstall {p}" for p in OFFICIAL_PLUGINS))
        lines.append("    copilot plugin install threadlight-skills@threadlight-skills  # if not installed")
        lines.append("    " + " && ".join(f"copilot plugin install {p}" for p in OFFICIAL_PLUGINS))
    elif report.get("routing_ahead"):
        lines.append("ORDER " + ", ".join(s for s in ROUTING_SKILLS if s in report["routing_name_only"])
                     + " would be listed by name only: skills listed before threadlight-skills ("
                     + ", ".join(report["routing_ahead"]) + ") fill Copilot's skill-description budget, so "
                     "Threadlight prompts can route to other skills.")
        lines.append("  Fix: reinstall the plugins listed before threadlight-skills after it, "
                     "or disable the ones you do not use in workshops:")
        for label in report["routing_ahead"]:
            if label.endswith("(its original source)"):
                lines.append(f"    copilot plugin uninstall {label.split(' (')[0]}, then reinstall it from its original source")
            elif " (" in label:
                name, source = label[:-1].split(" (", 1)
                lines.append(f"    copilot plugin uninstall {name} && copilot plugin install {source}")
            elif "@" in label:
                lines.append(f"    copilot plugin uninstall {label} && copilot plugin install {label}")
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


def _plugin_dirs(explicit: list[str] | None) -> list[str]:
    dirs = list(explicit or [])
    env = os.environ.get("THREADLIGHT_PLUGIN_DIRS", "")
    dirs += [d for d in env.split(os.pathsep) if d.strip() and d not in dirs]
    return dirs


def _copilot_listing(plugin_dirs: list[str] | None = None) -> list[dict]:
    flags = [arg for d in plugin_dirs or [] for arg in ("--plugin-dir", d)]
    proc = subprocess.run(["copilot", *flags, "skill", "list", "--json"], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def _unchecked(reason: str, as_json: bool) -> int:
    msg = f"copilot skill list --json unavailable ({reason}); official skills not checked."
    print(json.dumps({"status": "unchecked", "reason": msg}, indent=2) if as_json else f"UNCHECKED: {msg}")
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skill-list", type=Path, help="saved output of `copilot skill list --json`")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--plugin-dir", action="append", default=[], metavar="DIR",
                    help="plugin directory the session loads with `copilot --plugin-dir` (repeatable; "
                         "also THREADLIGHT_PLUGIN_DIRS, os.pathsep-separated)")
    args = ap.parse_args(argv)
    if args.skill_list:
        listing = _load(args.skill_list)
    else:
        try:
            listing = _copilot_listing(_plugin_dirs(args.plugin_dir))
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
