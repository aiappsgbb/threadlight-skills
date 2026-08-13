#!/usr/bin/env python3
"""skill_contract_check.py — contract linter for a generated pilot's skills.

A Threadlight pilot is a **super-agent with skills**, not a multi-agent system:
one agent, one context, several skill contracts that the model routes between.
That makes the *skill contract* the load-bearing artefact — and the place where
silent failures concentrate:

* a description over the loader's 1024-char cap is dropped from the registry
  without warning, so the skill simply never fires;
* two skills whose ``USE FOR`` clauses overlap make routing a coin flip;
* a ``DO NOT USE FOR (other-skill)`` pointer left behind by a rename sends the
  model to a skill that no longer exists;
* a ``Deps`` entry naming a tool absent from AGENTS.md is a fabricated tool
  name — the highest-frequency generation defect;
* a business rule in the SPEC that no skill implements is an unbuilt promise.

None of these are visible by reading one file at a time. They are all cheap to
detect across the set. This script does that, statically, with no model call.

Capability keys map to check IDs:

    skills_present                  SKC-001
    frontmatter_parseable           SKC-002
    name_matches_directory          SKC-003
    description_within_limit        SKC-004
    routing_contract_present        SKC-005
    handoff_targets_resolve         SKC-006
    routing_overlap_clear           SKC-007
    operational_contract_complete   SKC-008
    tool_deps_declared              SKC-009
    br_coverage_complete            SKC-010
    br_references_resolve           SKC-011
    skills_registered               SKC-012

stdlib-only. Gracefully degrading — anything that cannot be checked (no
AGENTS.md, no SPEC.md) is reported ``not-verified`` rather than crashing.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import re
import sys

VERSION = "1.11.0"
MANIFEST_SCHEMA = "threadlight-skill-contract-manifest/v1"

# The Copilot skill loader's hard cap on the parsed `description` scalar.
# Anything larger is silently dropped from the registry.
MAX_DESCRIPTION_CHARS = 1024

# Jaccard similarity between two USE FOR token sets at or above which the pair
# is reported as an ambiguous routing boundary.
OVERLAP_THRESHOLD = 0.5

STATUSES = ("pass", "must-fix", "should-fix", "not-verified", "not-applicable")

CAPABILITY_IDS = {
    "skills_present": "SKC-001",
    "frontmatter_parseable": "SKC-002",
    "name_matches_directory": "SKC-003",
    "description_within_limit": "SKC-004",
    "routing_contract_present": "SKC-005",
    "handoff_targets_resolve": "SKC-006",
    "routing_overlap_clear": "SKC-007",
    "operational_contract_complete": "SKC-008",
    "tool_deps_declared": "SKC-009",
    "br_coverage_complete": "SKC-010",
    "br_references_resolve": "SKC-011",
    "skills_registered": "SKC-012",
}

CAPABILITY_ORDER = list(CAPABILITY_IDS)

# Contract fields every generated SKILL.md must declare under
# `## Operational contract`.
CONTRACT_FIELDS = {
    "inputs": r"\*\*inputs\*\*",
    "outputs": r"\*\*outputs\*\*",
    "deps": r"\*\*deps\*\*",
    "idempotency": r"\*\*idempotenc\w*\*\*",
    "failure behavior": r"\*\*failure\s+behaviou?r\*\*",
}

_STOPWORDS = frozenset("""
about above after against been being between both cannot case cases could does
doing done during each either else even every from further have having here
into itself just more most much must only other others over same some such than
that thats their them then there these they this those through under until upon
very what when where which while will with within without would your
""".split())

_KEBAB = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b")
_SNAKE = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")
_BR_DEF = re.compile(r"^#{2,4}\s+(BR-\d+)\b", re.MULTILINE)
_BR_REF = re.compile(r"\bBR-\d+\b")
_TABLE_CELL1 = re.compile(r"^\|\s*`([^`]+)`\s*\|")


# ---------------------------------------------------------------------------
# frontmatter
# ---------------------------------------------------------------------------

def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> dict:
    """Parse the leading ``---`` fenced block into a flat dict.

    Supports ``key: value``, folded (``>``) and literal (``|``) block scalars,
    and one level of nesting flattened to dotted keys (``metadata.version``).
    Deliberately not a YAML implementation — generated frontmatter is a fixed,
    small shape and this keeps the linter stdlib-only.
    """
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    body: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        body.append(line)
    else:
        return {}

    data: dict = {}
    index = 0
    while index < len(body):
        raw = body[index]
        index += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = re.match(r"^(\s*)([\w.-]+):\s?(.*)$", raw)
        if not match:
            continue
        indent, key, rest = len(match.group(1)), match.group(2), match.group(3)
        rest = rest.rstrip()

        if rest[:1] in (">", "|") and rest.strip(">|+-") == "":
            folded = rest[0] == ">"
            chunk: list[str] = []
            while index < len(body):
                nxt = body[index]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                chunk.append(nxt.strip())
                index += 1
            while chunk and not chunk[-1]:
                chunk.pop()
            data[key] = " ".join(c for c in chunk if c) if folded else "\n".join(chunk)
            continue

        if rest == "":
            # A nested mapping: absorb the more-indented block as dotted keys.
            while index < len(body):
                nxt = body[index]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                child = re.match(r"^\s*([\w.-]+):\s*(.*)$", nxt)
                if child:
                    data[f"{key}.{child.group(1)}"] = _unquote(child.group(2))
                index += 1
            continue

        data[key] = _unquote(rest)
    return data


# ---------------------------------------------------------------------------
# routing clauses
# ---------------------------------------------------------------------------

_DO_NOT = re.compile(r"DO\s+NOT\s+USE\s+FOR\b[:\s]*", re.IGNORECASE)
_USE_FOR = re.compile(r"USE\s+FOR\b[:\s]*", re.IGNORECASE)


def use_for_clause(description: str) -> str:
    """The positive routing clause, with the negative clause removed first.

    ``DO NOT USE FOR`` contains the literal ``USE FOR``; splitting on the
    negative form first is what stops a description that carries only the
    negative clause from reading as if it had both.
    """
    head = _DO_NOT.split(description, maxsplit=1)[0]
    match = _USE_FOR.search(head)
    return head[match.end():].strip() if match else ""


def do_not_use_for_clause(description: str) -> str:
    parts = _DO_NOT.split(description, maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z][a-z0-9]{3,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _paren_groups(text: str) -> list:
    return re.findall(r"\(([^)]*)\)", text)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

class SkillDoc:
    __slots__ = ("directory", "path", "name", "description", "body", "parsed")

    def __init__(self, directory, path, front, body):
        self.directory = directory
        self.path = path
        self.name = front.get("name", "")
        self.description = front.get("description", "")
        self.body = body
        self.parsed = bool(front)


def load_skills(root: str) -> list:
    pattern = os.path.join(root, "src", "agent", "skills", "*", "SKILL.md")
    docs = []
    for path in sorted(glob.glob(pattern)):
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            text = ""
        front = parse_frontmatter(text)
        marker = text.find("\n---", 3)
        body = text[marker + 4:] if front and marker != -1 else text
        docs.append(SkillDoc(os.path.basename(os.path.dirname(path)), path, front, body))
    return docs


def _read(path: str) -> str:
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return ""


def agents_tables(root: str):
    """Return ``(tools, skills, found)`` from the AGENTS.md markdown tables.

    A table row whose first cell is a single backticked token is a declaration:
    ``snake_case`` names a tool, ``kebab-case`` names a skill. Reading the rows
    rather than the headings keeps this robust to section retitling.
    """
    text = _read(os.path.join(root, "AGENTS.md"))
    if not text:
        return set(), set(), False
    tools, skills = set(), set()
    for line in text.splitlines():
        match = _TABLE_CELL1.match(line.strip())
        if not match:
            continue
        cell = match.group(1).strip()
        if re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", cell):
            tools.add(cell)
        elif re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", cell):
            skills.add(cell)
    return tools, skills, True


def spec_business_rules(root: str):
    text = _read(os.path.join(root, "specs", "SPEC.md"))
    if not text:
        return set(), False
    return set(_BR_DEF.findall(text)), True


def declared_tool_deps(body: str) -> set:
    """Tool names on the ``**Deps**`` line, up to the first parenthesis.

    The convention is ``- **Deps**: tools `a`, `b` (qualifier `field`)`` — the
    parenthetical carries field names and idempotency keys, not tools, so it is
    truncated before extraction.
    """
    found = set()
    for line in body.splitlines():
        if not re.search(CONTRACT_FIELDS["deps"], line, re.IGNORECASE):
            continue
        head = line.split("(", 1)[0]
        found.update(_SNAKE.findall(head))
    return found


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def _cap(status, evidence=None, hint=None):
    return {"status": status, "evidence": evidence, "hint": hint}


def evaluate(root: str) -> dict:
    """Read-only assessment. Never writes to ``root``."""
    root = os.path.abspath(root)
    docs = load_skills(root)
    caps: dict = {}

    # SKC-001 -------------------------------------------------------------
    if docs:
        caps["skills_present"] = _cap(
            "pass", f"{len(docs)} skill(s): {', '.join(d.directory for d in docs)}")
    else:
        caps["skills_present"] = _cap(
            "must-fix", None,
            "no src/agent/skills/*/SKILL.md found — a super-agent needs at "
            "least one skill contract")

    # SKC-002 -------------------------------------------------------------
    unparsed = [d.directory for d in docs if not d.parsed or not d.description]
    if not docs:
        caps["frontmatter_parseable"] = _cap("not-verified", None, "no skills to check")
    elif unparsed:
        caps["frontmatter_parseable"] = _cap(
            "must-fix", f"missing name/description frontmatter: {', '.join(unparsed)}",
            "a skill without a parseable description is never routed to")
    else:
        caps["frontmatter_parseable"] = _cap("pass", f"{len(docs)} skill(s) parsed")

    # Downstream per-skill checks only consider well-formed documents.
    good = [d for d in docs if d.parsed and d.description]
    names = {d.directory for d in docs}

    def _degrade(key, hint):
        caps[key] = _cap("not-verified", None, hint)

    if not good:
        for key in ("name_matches_directory", "description_within_limit",
                    "routing_contract_present", "handoff_targets_resolve",
                    "routing_overlap_clear", "operational_contract_complete"):
            _degrade(key, "no parseable skill contract to check")
    else:
        # SKC-003 ---------------------------------------------------------
        mismatched = [f"{d.directory}/ declares name: {d.name or '(none)'}"
                      for d in good if d.name != d.directory]
        caps["name_matches_directory"] = _cap(
            "must-fix", "; ".join(mismatched),
            "the loader keys on the frontmatter name; a mismatch orphans the folder",
        ) if mismatched else _cap("pass", f"{len(good)} skill(s) aligned")

        # SKC-004 ---------------------------------------------------------
        over = [f"{d.directory} {len(d.description)}/{MAX_DESCRIPTION_CHARS}"
                for d in good if len(d.description) > MAX_DESCRIPTION_CHARS]
        longest = max(len(d.description) for d in good)
        caps["description_within_limit"] = _cap(
            "must-fix", "; ".join(over),
            f"over {MAX_DESCRIPTION_CHARS} chars the skill is SILENTLY dropped "
            "from the registry",
        ) if over else _cap("pass", f"longest {longest}/{MAX_DESCRIPTION_CHARS}")

        # SKC-005 ---------------------------------------------------------
        incomplete = []
        for d in good:
            missing = []
            if not use_for_clause(d.description):
                missing.append("USE FOR")
            if not do_not_use_for_clause(d.description):
                missing.append("DO NOT USE FOR")
            if missing:
                incomplete.append(f"{d.directory} missing {' + '.join(missing)}")
        caps["routing_contract_present"] = _cap(
            "must-fix", "; ".join(incomplete),
            "both clauses are what the model routes on; without them the "
            "boundary is guessed",
        ) if incomplete else _cap("pass", f"{len(good)} skill(s) declare both clauses")

        # SKC-006 ---------------------------------------------------------
        candidates = []
        for d in good:
            for group in _paren_groups(do_not_use_for_clause(d.description)):
                for token in _KEBAB.findall(group):
                    candidates.append((d.directory, token))
        resolved = [c for c in candidates if c[1] in names]
        dangling = sorted({f"{src} → {tok}" for src, tok in candidates
                           if tok not in names})
        if not resolved:
            caps["handoff_targets_resolve"] = _cap(
                "not-verified", None,
                "no parenthesised handoff pointer resolves to a sibling skill — "
                "cannot tell whether the convention is in use")
        elif dangling:
            caps["handoff_targets_resolve"] = _cap(
                "must-fix", "; ".join(dangling),
                "a handoff pointer to a skill that does not exist routes the "
                "model nowhere — usually a rename left behind")
        else:
            caps["handoff_targets_resolve"] = _cap(
                "pass", f"{len(resolved)} handoff pointer(s) resolve")

        # SKC-007 ---------------------------------------------------------
        collisions = []
        for i, left in enumerate(good):
            for right in good[i + 1:]:
                lt, rt = _tokens(use_for_clause(left.description)), _tokens(
                    use_for_clause(right.description))
                score = _jaccard(lt, rt)
                if score >= OVERLAP_THRESHOLD:
                    shared = ", ".join(sorted(lt & rt)[:6])
                    collisions.append(
                        f"{left.directory} ↔ {right.directory} ({score:.2f}: {shared})")
        caps["routing_overlap_clear"] = _cap(
            "should-fix", "; ".join(collisions),
            "overlapping USE FOR clauses make the routing decision a coin flip — "
            "narrow one side or merge the skills",
        ) if collisions else _cap(
            "pass", f"{len(good)} skill(s), max pairwise overlap under "
                    f"{OVERLAP_THRESHOLD}")

        # SKC-008 ---------------------------------------------------------
        gaps = []
        for d in good:
            missing = [label for label, pattern in CONTRACT_FIELDS.items()
                       if not re.search(pattern, d.body, re.IGNORECASE)]
            if missing:
                gaps.append(f"{d.directory} missing {', '.join(missing)}")
        caps["operational_contract_complete"] = _cap(
            "should-fix", "; ".join(gaps),
            "an undeclared idempotency or failure path is the contract the "
            "runtime silently invents at 3am",
        ) if gaps else _cap("pass", f"{len(good)} skill(s) declare all "
                                    f"{len(CONTRACT_FIELDS)} contract fields")

    # SKC-009 -------------------------------------------------------------
    tools, registered, agents_found = agents_tables(root)
    if not agents_found:
        _degrade("tool_deps_declared", "no AGENTS.md at the target root")
        _degrade("skills_registered", "no AGENTS.md at the target root")
    else:
        undeclared = sorted({
            f"{d.directory} → {tool}"
            for d in docs for tool in declared_tool_deps(d.body)
            if tool not in tools})
        caps["tool_deps_declared"] = _cap(
            "must-fix", "; ".join(undeclared),
            "a Deps entry absent from the AGENTS.md tool table is a fabricated "
            "tool name — the agent will call something that does not exist",
        ) if undeclared else _cap("pass", f"{len(tools)} tool(s) declared in AGENTS.md")

        # SKC-012 ---------------------------------------------------------
        missing_dirs = sorted(registered - names)
        unregistered = sorted(names - registered)
        if missing_dirs:
            caps["skills_registered"] = _cap(
                "must-fix",
                "listed in AGENTS.md with no directory: " + ", ".join(missing_dirs)
                + ("; not listed in AGENTS.md: " + ", ".join(unregistered)
                   if unregistered else ""),
                "the skills table advertises a capability the agent cannot load")
        elif unregistered:
            caps["skills_registered"] = _cap(
                "should-fix", "not listed in AGENTS.md: " + ", ".join(unregistered),
                "an unlisted skill is invisible to anyone reviewing the agent")
        else:
            caps["skills_registered"] = _cap(
                "pass", f"{len(registered)} skill(s) match the AGENTS.md table")

    # SKC-010 / SKC-011 ---------------------------------------------------
    defined, spec_found = spec_business_rules(root)
    referenced = {br for d in docs for br in _BR_REF.findall(d.body)}
    if not spec_found:
        _degrade("br_coverage_complete", "no specs/SPEC.md at the target root")
        _degrade("br_references_resolve", "no specs/SPEC.md at the target root")
    elif not docs:
        _degrade("br_coverage_complete", "no skills to check")
        _degrade("br_references_resolve", "no skills to check")
    else:
        uncovered = sorted(defined - referenced)
        caps["br_coverage_complete"] = _cap(
            "should-fix", "unimplemented: " + ", ".join(uncovered),
            "a business rule no skill references is a promise in the SPEC that "
            "the agent does not keep",
        ) if uncovered else _cap("pass", f"{len(defined)} business rule(s) covered")

        dangling_br = sorted(referenced - defined)
        caps["br_references_resolve"] = _cap(
            "must-fix", "not defined in SPEC § 3: " + ", ".join(dangling_br),
            "a skill citing a business rule that does not exist cannot be "
            "reviewed by the customer SME",
        ) if dangling_br else _cap("pass", f"{len(referenced)} reference(s) resolve")

    return {key: dict(caps[key], check_id=CAPABILITY_IDS[key]) for key in CAPABILITY_ORDER}


def manifest(root: str, caps: dict) -> dict:
    must = [k for k in CAPABILITY_ORDER if caps[k]["status"] == "must-fix"]
    should = [k for k in CAPABILITY_ORDER if caps[k]["status"] == "should-fix"]
    notv = [k for k in CAPABILITY_ORDER if caps[k]["status"] == "not-verified"]

    if must:
        verdict = "unsound"
    elif should or notv:
        verdict = "partial"
    else:
        verdict = "sound"

    docs = load_skills(os.path.abspath(root))
    return {
        "schema": MANIFEST_SCHEMA,
        "tool_version": VERSION,
        "captured_at": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0).isoformat(),
        "verdict": verdict,
        "must_fix": must,
        "should_fix": should,
        "not_verified": notv,
        "metrics": {
            "skills": len(docs),
            "longest_description": max(
                (len(d.description) for d in docs), default=0),
            "description_limit": MAX_DESCRIPTION_CHARS,
        },
        "capabilities": {key: caps[key] for key in CAPABILITY_ORDER},
    }


def _clean_cell(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render(man: dict) -> str:
    icon = {"pass": "✅", "must-fix": "❌", "should-fix": "🟠",
            "not-verified": "⚪", "not-applicable": "➖"}
    lines = [
        "# Skill contract — manifest report",
        "",
        f"> Verdict: **{man['verdict'].upper()}** · "
        f"{man['metrics']['skills']} skill(s) · captured {man['captured_at']}",
        "",
        "| Capability | Check | Status | Evidence / hint |",
        "|---|---|---|---|",
    ]
    for key, cap in man["capabilities"].items():
        detail = cap.get("evidence") or cap.get("hint") or ""
        lines.append(
            f"| `{key}` | `{cap.get('check_id', '')}` | "
            f"{icon.get(cap['status'], '?')} {cap['status']} | {_clean_cell(detail)} |"
        )
    lines += ["", "Generated by `threadlight-design` Step 8 (auto-review).", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Contract linter for a generated pilot's src/agent/skills/")
    parser.add_argument("--target", default=".", help="pilot repo root (default cwd)")
    parser.add_argument("--emit", action="store_true",
                        help="write specs/skill-contract-manifest.json + "
                             "docs/skill-contract-report.md")
    parser.add_argument("--gate", action="store_true",
                        help="exit 2 when any capability is must-fix")
    parser.add_argument("--json", action="store_true",
                        help="print manifest JSON to stdout")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.target)
    try:
        caps = evaluate(root)
    except Exception as exc:  # graceful top-level degradation
        caps = {key: {"check_id": CAPABILITY_IDS[key], "status": "not-verified",
                      "evidence": None,
                      "hint": f"linter could not complete: {exc}"}
                for key in CAPABILITY_ORDER}
    man = manifest(root, caps)

    if args.emit:
        os.makedirs(os.path.join(root, "specs"), exist_ok=True)
        os.makedirs(os.path.join(root, "docs"), exist_ok=True)
        with open(os.path.join(root, "specs", "skill-contract-manifest.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(man, fh, indent=2)
            fh.write("\n")
        with open(os.path.join(root, "docs", "skill-contract-report.md"),
                  "w", encoding="utf-8") as fh:
            fh.write(render(man))

    if args.json:
        print(json.dumps(man, indent=2))
    else:
        print(render(man))

    if args.gate and man["must_fix"]:
        print(f"\nGATE: {len(man['must_fix'])} must-fix capability(ies): "
              f"{', '.join(man['must_fix'])}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
