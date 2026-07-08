#!/usr/bin/env python3
"""Agent-identity binding producer — discover the workload/agent identities a
repo declares, emit an ``agent-identity.json`` (an identity AI-BOM), and grade
four Pillar 03 findings.

Pure standard library. No sibling-module imports (production_ready.py imports
THIS module lazily, never the reverse). Discovery is best-effort and defensive:
one malformed file degrades a single subject, never aborts the scan.

Findings produced (aggregated, one per id) by :func:`check`:
  * IAM-006 — agent runs as a passwordless managed/federated identity (not a
    secret-based app registration).
  * IAM-007 — each agent identity declares a responsible human owner.
  * IAM-008 — agent identity scope is least-privilege (no Owner/Contributor,
    no wildcard Graph app-permission).
  * IAM-009 — agent identity lifecycle (expiry/review) is declared.

Remediation always points at Microsoft platform primitives (Entra Agent ID,
user-assigned / federated managed identity, PIM / access reviews) — this
amplifies the platform, never replaces it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Tracks the threadlight-production-ready skill version (see SKILL.md frontmatter).
IDENTITY_VERSION = "0.7.0"

# Built-in Azure role definition GUIDs that are never least-privilege for a
# workload identity.
OWNER_ROLE = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
CONTRIBUTOR_ROLE = "b24988ac-6180-42a0-ab88-20f7382dd24c"
USER_ACCESS_ADMIN_ROLE = "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9"
PRIVILEGED_ROLES = {
    OWNER_ROLE: "Owner",
    CONTRIBUTOR_ROLE: "Contributor",
    USER_ACCESS_ADMIN_ROLE: "User Access Administrator",
}

# High-privilege Microsoft Graph application permissions.
_WILDCARD_GRAPH_RE = re.compile(
    r"Directory\.ReadWrite\.All|RoleManagement\.ReadWrite\.Directory|"
    r"AppRoleAssignment\.ReadWrite\.All|Application\.ReadWrite\.All", re.I)
_WILDCARD_ROLE_RE = re.compile(r"roles?\s*[:=]\s*\[\s*['\"]\*['\"]", re.I)

# Secret-based app-registration signals (the "bad NHI" IAM-006 must catch).
_SECRET_SIGNAL_RE = re.compile(
    r"ClientSecretCredential|passwordCredentials|az ad app credential|"
    r"--password\b", re.I)

_OWNER_TAG_KEYS = ("owner", "owneremail", "managedby")
_REVIEW_TAG_KEYS = ("reviewby", "expireson", "expiry", "expires")

_IDENTITY_MARKERS = (
    "microsoft.managedidentity",
    "microsoft.authorization/roleassignments",
    "microsoft.graph/applications",
)
_UAMI_TYPE = "microsoft.managedidentity/userassignedidentities"
_ROLE_TYPE = "microsoft.authorization/roleassignments"

GOVERNANCE_FILE = "agent-identity.governance.json"
_SKIP_DIRS = {"node_modules", ".git", ".venv", "dist", "build", "__pycache__"}


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class IdentitySubject:
    id: str
    type: str                       # uami | federated | app-secret | unknown
    passwordless: bool
    owner: str | None = None
    review_declared: bool = False
    review_by: str | None = None
    scopes: list[dict] = field(default_factory=list)
    wildcard_scope: bool = False
    declared_in: str = ""
    parse_error: str | None = None


# --------------------------------------------------------------------------
# File collection
# --------------------------------------------------------------------------

def _iter_files(root: Path, *patterns: str):
    for pat in patterns:
        for p in sorted(root.glob(pat)):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.relative_to(root).parts):
                continue
            yield p


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _identity_texts(root: Path) -> list[tuple[str, str]]:
    """(relpath, text) for bicep files + JSON files that mention an identity
    resource type. Cheap substring gate avoids parsing unrelated JSON."""
    out: list[tuple[str, str]] = []
    for p in _iter_files(root, "**/*.bicep"):
        out.append((p.relative_to(root).as_posix(), _read(p)))
    for p in _iter_files(root, "**/*.json"):
        rel = p.relative_to(root).as_posix()
        if rel.endswith(GOVERNANCE_FILE):
            continue
        text = _read(p)
        low = text.lower()
        if any(mark in low for mark in _IDENTITY_MARKERS):
            out.append((rel, text))
    return out


# --------------------------------------------------------------------------
# Tag helpers
# --------------------------------------------------------------------------

def _tag_lookup(tags: dict, keys) -> str | None:
    if not isinstance(tags, dict):
        return None
    lowered = {str(k).lower(): v for k, v in tags.items()}
    for k in keys:
        if k in lowered and lowered[k] not in (None, "", []):
            return str(lowered[k])
    return None


# --------------------------------------------------------------------------
# Structured ARM discovery
# --------------------------------------------------------------------------

def _walk_arm(resources, parent_name=None):
    if not isinstance(resources, list):
        return
    for r in resources:
        if not isinstance(r, dict):
            continue
        yield r, parent_name
        name = r.get("name") if isinstance(r.get("name"), str) else None
        yield from _walk_arm(r.get("resources"), parent_name=name)


def _arm_subjects(rel: str, doc: dict) -> tuple[list[IdentitySubject], set[str]]:
    """Return (uami subjects, federated-parent-hints) from a parsed ARM doc."""
    subjects: list[IdentitySubject] = []
    fed_hints: set[str] = set()
    for r, parent in _walk_arm(doc.get("resources", [])):
        rtype = str(r.get("type", "")).lower()
        if rtype == _UAMI_TYPE:
            name = r.get("name")
            sid = name if isinstance(name, str) and name else f"uami@{rel}"
            tags = r.get("tags", {})
            owner = _tag_lookup(tags, _OWNER_TAG_KEYS)
            review = _tag_lookup(tags, _REVIEW_TAG_KEYS)
            subjects.append(IdentitySubject(
                id=sid, type="uami", passwordless=True, owner=owner,
                review_declared=review is not None, review_by=review,
                declared_in=rel))
        elif rtype.endswith("federatedidentitycredentials"):
            nm = r.get("name")
            if isinstance(nm, str) and "/" in nm:
                fed_hints.add(nm.split("/", 1)[0])
            if isinstance(parent, str):
                fed_hints.add(parent)
            for m in re.finditer(
                    r"userAssignedIdentities'?\s*,\s*'([^')]+)'",
                    json.dumps(r)):
                fed_hints.add(m.group(1))
    return subjects, fed_hints


# --------------------------------------------------------------------------
# Bicep + source discovery (text-level, best-effort)
# --------------------------------------------------------------------------

def _slice_block(text: str, open_idx: int) -> str:
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx:i + 1]
    return text[open_idx:]


_BICEP_UAMI_RE = re.compile(
    r"resource\s+(\w+)\s+'Microsoft\.ManagedIdentity/userAssignedIdentities@[^']*'\s*=",
    re.I)


def _bicep_subjects(rel: str, text: str) -> list[IdentitySubject]:
    subjects: list[IdentitySubject] = []
    low = text.lower()
    file_federated = "federatedidentitycredentials" in low
    for mm in _BICEP_UAMI_RE.finditer(text):
        brace = text.find("{", mm.end())
        block = _slice_block(text, brace) if brace != -1 else ""
        nm = re.search(r"\bname\s*:\s*'([^']+)'", block)
        sid = nm.group(1) if nm else mm.group(1)
        ow = re.search(r"\b(?:owner|ownerEmail|ManagedBy)\s*:\s*'([^']+)'",
                       block, re.I)
        owner = ow.group(1) if ow else (
            "declared" if re.search(r"\b(?:owner|ownerEmail|ManagedBy)\s*:",
                                    block, re.I) else None)
        rv = re.search(r"\b(?:reviewBy|expiresOn)\s*:", block, re.I)
        subjects.append(IdentitySubject(
            id=sid, type="federated" if file_federated else "uami",
            passwordless=True, owner=owner,
            review_declared=rv is not None, declared_in=rel))
    # Graph application with a client secret → secret-based subject.
    if "microsoft.graph/applications" in low and "passwordcredentials" in low:
        dn = re.search(r"\bdisplayName\s*:\s*'([^']+)'", text)
        subjects.append(IdentitySubject(
            id=dn.group(1) if dn else "graph-application",
            type="app-secret", passwordless=False, declared_in=rel))
    return subjects


def _source_secret_subject(root: Path) -> IdentitySubject | None:
    for p in _iter_files(root, "**/*.py", "**/*.ts", "**/*.js", "**/*.tsx",
                         "**/*.jsx", "**/*.cs"):
        text = _read(p)
        if _SECRET_SIGNAL_RE.search(text):
            return IdentitySubject(
                id="app-registration", type="app-secret", passwordless=False,
                declared_in=p.relative_to(root).as_posix())
    return None


# --------------------------------------------------------------------------
# Top-level subject discovery
# --------------------------------------------------------------------------

def discover(root) -> list[IdentitySubject]:
    root = Path(root)
    subjects: list[IdentitySubject] = []
    fed_hints: set[str] = set()
    for rel, text in _identity_texts(root):
        if rel.endswith(".json"):
            try:
                doc = json.loads(text)
            except (ValueError, TypeError):
                if _UAMI_TYPE in text.lower():
                    subjects.append(IdentitySubject(
                        id=f"unparsed@{rel}", type="unknown", passwordless=False,
                        declared_in=rel, parse_error="unparseable ARM/JSON"))
                continue
            subs, hints = _arm_subjects(rel, doc)
            subjects.extend(subs)
            fed_hints |= hints
        else:  # bicep
            subjects.extend(_bicep_subjects(rel, text))
    secret = _source_secret_subject(root)
    if secret is not None:
        subjects.append(secret)
    # Apply federated hints from ARM to matching UAMI subjects.
    for s in subjects:
        if s.type == "uami" and s.id in fed_hints:
            s.type = "federated"
    subjects.sort(key=lambda s: (s.declared_in, s.id))
    return subjects


# --------------------------------------------------------------------------
# Scope-signal discovery (repo-level: role assignments + Graph permissions)
# --------------------------------------------------------------------------

def _scope_signals(root: Path, has_uami: bool) -> dict:
    offenders: set[str] = set()
    workload_roles = 0
    any_role = False
    texts = _identity_texts(root)
    for rel, text in texts:
        if rel.endswith(".json"):
            try:
                doc = json.loads(text)
            except (ValueError, TypeError):
                continue
            for r, _p in _walk_arm(doc.get("resources", [])):
                if str(r.get("type", "")).lower() != _ROLE_TYPE:
                    continue
                any_role = True
                props = r.get("properties", {}) if isinstance(r.get("properties"), dict) else {}
                rdid = str(props.get("roleDefinitionId", ""))
                pt = str(props.get("principalType", "")).lower()
                blob = json.dumps(props).lower()
                if pt in ("user", "group"):
                    workload = False
                elif pt == "serviceprincipal" or "managedidentity" in blob:
                    workload = True
                else:
                    workload = has_uami
                if not workload:
                    continue
                workload_roles += 1
                for guid, label in PRIVILEGED_ROLES.items():
                    if guid in rdid:
                        offenders.add(f"{label} role assignment")
    # Text-level scan across bicep + ARM for Graph wildcard permissions and
    # privileged role GUIDs declared in Bicep.
    joined = "\n".join(t for _r, t in texts)
    if _WILDCARD_GRAPH_RE.search(joined) or _WILDCARD_ROLE_RE.search(joined):
        offenders.add("wildcard Graph app-permission")
    if has_uami or "serviceprincipal" in joined.lower():
        for _rel, text in texts:
            if text.endswith(".json"):
                continue  # ARM handled structurally above
            low = text.lower()
            for guid, label in PRIVILEGED_ROLES.items():
                if guid in low:
                    offenders.add(f"{label} role assignment")
                    any_role = True
                    workload_roles += 1
    return {"offenders": sorted(offenders), "workload_roles": workload_roles,
            "any_role": any_role}


# --------------------------------------------------------------------------
# Governance manifest
# --------------------------------------------------------------------------

def _load_governance(root: Path, path=None) -> dict:
    lp = Path(path) if path else (root / GOVERNANCE_FILE)
    try:
        data = json.loads(lp.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    subs = data.get("subjects") if isinstance(data, dict) else None
    return subs if isinstance(subs, dict) else {}


def _apply_governance(subjects: list[IdentitySubject], gov: dict) -> None:
    for s in subjects:
        entry = gov.get(s.id)
        if not isinstance(entry, dict):
            continue
        if not s.owner and entry.get("owner"):
            s.owner = str(entry["owner"])
        if not s.review_declared and (entry.get("review_by") or entry.get("expires_on")):
            s.review_declared = True
            s.review_by = str(entry.get("review_by") or entry.get("expires_on"))


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------

_STATUS_RANK = {"not-applicable": 0, "pass": 1, "should-fix": 2, "must-fix": 3}
IAM_IDS = ("IAM-006", "IAM-007", "IAM-008", "IAM-009")
_IAM_TITLES = {
    "IAM-006": "Agent identity is passwordless (managed/federated)",
    "IAM-007": "Agent identity declares a responsible owner",
    "IAM-008": "Agent identity scope is least-privilege",
    "IAM-009": "Agent identity lifecycle (expiry/review) is declared",
}


def _worst(statuses: list[str]) -> str:
    if not statuses:
        return "not-applicable"
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 0))


def _subject_status(s: IdentitySubject) -> dict:
    """IAM-006/007/009 for one subject (IAM-008 is repo-level, added later)."""
    if s.parse_error:
        return {"IAM-006": "should-fix", "IAM-007": "should-fix",
                "IAM-009": "should-fix"}
    st = {}
    st["IAM-006"] = "pass" if s.passwordless else "must-fix"
    st["IAM-007"] = "pass" if s.owner else "should-fix"
    if s.type == "federated":
        st["IAM-009"] = "pass"
    else:
        st["IAM-009"] = "pass" if s.review_declared else "should-fix"
    return st


def _scope_status(subjects: list, scope: dict) -> str:
    if scope["offenders"]:
        return "must-fix"
    if scope["workload_roles"] > 0 or scope["any_role"]:
        return "pass"
    return "should-fix"


def _detail_for(fid: str, offenders: list[str]) -> str:
    if fid == "IAM-006":
        return ("Bind these agents to a passwordless managed/federated identity "
                "(Entra Agent ID / UAMI) instead of a client secret: "
                + ", ".join(offenders)) if offenders else \
            "All agent identities are passwordless (managed/federated)."
    if fid == "IAM-007":
        return ("Declare a responsible owner (tag owner/ownerEmail/ManagedBy or "
                "agent-identity.governance.json) for: " + ", ".join(offenders)) \
            if offenders else "Every agent identity declares a responsible owner."
    if fid == "IAM-008":
        return ("Reduce to least privilege (drop Owner/Contributor and wildcard "
                "Graph permissions; use scoped built-in roles / PIM): "
                + ", ".join(offenders)) if offenders else \
            "Agent identity scope is least-privilege (no Owner/Contributor/wildcard)."
    if fid == "IAM-009":
        return ("Declare a lifecycle (reviewBy/expiresOn tag or a federated "
                "credential with no standing secret) for: " + ", ".join(offenders)) \
            if offenders else "Agent identity lifecycle (expiry/review) is declared."
    return ""


def check(subjects: list, scope: dict) -> list[dict]:
    if not subjects:
        return [{"id": fid, "title": _IAM_TITLES[fid], "status": "not-applicable",
                 "detail": "No agent/workload identity declared in this repo.",
                 "offenders": []} for fid in IAM_IDS]
    per = [(s, _subject_status(s)) for s in subjects]
    scope_st = _scope_status(subjects, scope)
    findings = []
    for fid in IAM_IDS:
        if fid == "IAM-008":
            findings.append({
                "id": fid, "title": _IAM_TITLES[fid], "status": scope_st,
                "detail": _detail_for(fid, scope["offenders"]),
                "offenders": list(scope["offenders"])})
            continue
        statuses = [st[fid] for _s, st in per]
        offenders = sorted({s.id for s, st in per
                            if st[fid] in ("must-fix", "should-fix")})
        findings.append({
            "id": fid, "title": _IAM_TITLES[fid], "status": _worst(statuses),
            "detail": _detail_for(fid, offenders), "offenders": offenders})
    return findings


# --------------------------------------------------------------------------
# BOM serialization + top-level assess
# --------------------------------------------------------------------------

def _subject_to_dict(s: IdentitySubject) -> dict:
    return {
        "id": s.id, "type": s.type, "passwordless": s.passwordless,
        "owner": s.owner, "review_declared": s.review_declared,
        "review_by": s.review_by, "scopes": s.scopes,
        "wildcard_scope": s.wildcard_scope, "declared_in": s.declared_in,
        "parse_error": s.parse_error,
    }


def build_identity_bom(subjects: list, scope: dict) -> dict:
    passwordless = sum(1 for s in subjects if s.passwordless)
    return {
        "schema": "threadlight.agent-identity/v1",
        "generator": "threadlight-production-ready/agent_identity",
        "generator_version": IDENTITY_VERSION,
        "subjects": [_subject_to_dict(s) for s in subjects],
        "summary": {
            "subject_count": len(subjects),
            "passwordless": passwordless,
            "secret_based": len(subjects) - passwordless,
            "owned": sum(1 for s in subjects if s.owner),
            "over_privileged": len(scope["offenders"]),
            "must_fix": 0,
            "should_fix": 0,
        },
    }


def assess(root, governance_path=None) -> tuple[dict, list[dict]]:
    root = Path(root)
    subjects = discover(root)
    gov = _load_governance(root, governance_path)
    _apply_governance(subjects, gov)
    has_uami = any(s.type in ("uami", "federated") for s in subjects)
    scope = _scope_signals(root, has_uami)
    findings = check(subjects, scope)
    bom = build_identity_bom(subjects, scope)
    bom["summary"]["must_fix"] = sum(1 for f in findings if f["status"] == "must-fix")
    bom["summary"]["should_fix"] = sum(1 for f in findings if f["status"] == "should-fix")
    scope_st = _scope_status(subjects, scope)
    for sd, s in zip(bom["subjects"], subjects):
        st = _subject_status(s)
        st["IAM-008"] = scope_st
        sd["findings"] = {fid: st.get(fid, "not-applicable") for fid in IAM_IDS}
        if scope["offenders"]:
            sd["wildcard_scope"] = any("Graph" in o for o in scope["offenders"])
    return bom, findings


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Inventory agent/workload identities, emit an identity BOM, "
                    "and gate on must-fix binding findings.")
    ap.add_argument("--root", default=".", help="repo root to scan")
    ap.add_argument("--out", default="agent-identity.json", help="BOM output path")
    ap.add_argument("--governance", default=None,
                    help="path to agent-identity.governance.json")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any finding is must-fix")
    args = ap.parse_args(argv)

    root = Path(args.root)
    bom, findings = assess(root, governance_path=args.governance)

    Path(args.out).write_text(json.dumps(bom, indent=2) + "\n", encoding="utf-8")

    for f in findings:
        print(f"{f['id']}: {f['status']} — {f['detail']}")

    if args.check and any(f["status"] == "must-fix" for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
