"""SPEC section 6 (Tool Contracts) parser shared by threadlight-deploy and the recommend-only check.

``generate_only`` builds its MCP tool plan from :func:`tools`; the recommend-only
consistency check (``action_mode.py``) reads the same plan and classification,
so a tool the deploy would serve is never invisible to the check.
Read/write classification: SPEC section 11a ``consequence`` first, then the
section 5b Read/Write column, then declared section 6 side effects, then the
section 6 idempotency column, then a read verb as the first or second name
token (``get_case``, ``returns_get_case``).
"""
from __future__ import annotations

import re
from typing import Any

READ_PREFIXES = ("get_", "list_", "query_", "search_", "find_", "lookup_", "fetch_", "check_", "read_")
MANY_PREFIXES = ("list_", "query_", "search_", "find_")
KNOWLEDGE_BACKING = re.compile(r"foundry[\s-]*iq|knowledge[\s-]*base|ai[\s-]*search|search[\s-]*index", re.I)


class SpecToolsError(ValueError):
    """SPEC section 6 declares no parseable tool."""


def _section(spec: str, number: str) -> str:
    """Body of ``## <number>.`` up to the next level-2 heading."""
    m = re.search(rf"^##\s+{re.escape(number)}\b[^\n]*\n(.*?)(?=^##\s|\Z)", spec, re.M | re.S)
    return m.group(1) if m else ""


def _table_rows(block: str) -> list[list[str]]:
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def _tables(block: str) -> list[list[list[str]]]:
    tables, current = [], []
    for line in block.splitlines() + [""]:
        if line.strip().startswith("|"):
            current.append(line)
        elif current:
            tables.append(_table_rows("\n".join(current)))
            current = []
    return [t for t in tables if t]


def _strip_ticks(text: str) -> str:
    return text.replace("`", "").strip()


def _identifier(name: str) -> str:
    ident = re.sub(r"[^0-9a-zA-Z_]", "_", name).strip("_")
    if not ident or ident[0].isdigit():
        ident = f"t_{ident}"
    return ident


VALUE_WORDS = {
    "amount", "value", "price", "total", "reason", "text", "sku", "score", "method", "status",
    "history", "context", "notes", "note", "query", "decision", "rationale", "category",
    "quantity", "date", "type", "message", "description", "photo", "photos", "uri", "uris",
}


PLACEHOLDER_INPUTS = re.compile(
    r"^\s*(?:none|n/?a|nil|-|—|–|tbd|see\s+below|none\s*/\s*see\s+below|see\s+section\s+6\w*)\s*\.?\s*$", re.I)
EXPLICIT_NO_INPUTS = re.compile(r"^\s*(?:none|n/?a|nil|-|—|–)\s*\.?\s*$", re.I)
PLACEHOLDER_WORDS = {"none", "see", "below", "above", "n", "a", "na", "tbd", "nil"}
TYPE_MAP = {
    "string": "str", "str": "str", "text": "str", "datetime": "str", "date": "str", "time": "str",
    "uuid": "str", "id": "str", "enum": "str", "boolean": "bool", "bool": "bool",
    "integer": "int", "int": "int", "number": "float", "float": "float", "decimal": "float",
}


def _is_placeholder(text: str) -> bool:
    return not _strip_ticks(text).strip() or bool(PLACEHOLDER_INPUTS.match(_strip_ticks(text)))


def _arg_type(raw: str) -> str:
    raw = raw.strip().strip("`").lower()
    if raw.endswith("[]") or raw.startswith(("array", "list")):
        return "list[str]"
    return TYPE_MAP.get(re.split(r"[\s(|<]", raw)[0] if raw else "", "str")


def _prose_args(inputs: str) -> list[dict[str, Any]]:
    """Free-text SPEC inputs ('case + order + amount') -> optional str args."""
    args: list[dict[str, Any]] = []
    if _is_placeholder(inputs):
        return args
    text = re.sub(r"\([^)]*\)", "", inputs.lower())
    for token in re.split(r"\s*(?:\+|,|;|/|\band\b)\s*", text):
        words = re.findall(r"[a-z][a-z0-9]*", token)
        if not words or len(words) > 3 or set(words) <= PLACEHOLDER_WORDS:
            continue
        if len(words) == 1 and words[0] not in VALUE_WORDS and not words[0].endswith("id"):
            name = f"{words[0]}_id"
        else:
            name = "_".join(words)
        name = _identifier(name)
        if name not in {a["name"] for a in args}:
            args.append({"name": name, "type": "str", "optional": True})
    return args


def _ticked_args(inputs: str) -> list[dict[str, Any]]:
    """Table-cell inputs: `a`, optional `b`, `ids[]`."""
    required, optional = [], []
    for m in re.finditer(r"(optional\s+)?`([A-Za-z_][A-Za-z0-9_]*)(\[\])?`", inputs, re.I):
        arg = {"name": _identifier(m.group(2)), "type": "list[str]" if m.group(3) else "str"}
        if any(a["name"] == arg["name"] for a in required + optional):
            continue
        (optional if m.group(1) else required).append(arg)
    for arg in optional:
        arg["optional"] = True
    return required + optional


def _subsection_args(body: str) -> list[dict[str, Any]] | None:
    """``- **Inputs**:`` as prose (`name: type` required) or a Parameter table. None when absent."""
    m = re.search(r"^\s*[-*]\s*\*\*(?:Inputs?|Parameters?|Arguments?)\*\*\s*:?(.*?)(?=^\s*[-*]\s*\*\*|^#{2,4}\s|\Z)",
                  body, re.M | re.S | re.I)
    if not m:
        return None
    text = m.group(1)
    args: list[dict[str, Any]] = []
    for table in _tables(text):
        header = [h.lower() for h in table[0]]
        i_name = next((i for i, h in enumerate(header) if h in {"parameter", "name", "input", "argument", "field"}), 0)
        i_type = next((i for i, h in enumerate(header) if "type" in h), None)
        i_req = next((i for i, h in enumerate(header) if "required" in h), None)
        for row in table[1:]:
            name = _identifier(_strip_ticks(row[i_name]).split()[0]) if i_name < len(row) and row[i_name].strip() else ""
            if not name or name in {a["name"] for a in args}:
                continue
            arg = {"name": name, "type": _arg_type(row[i_type]) if i_type is not None and i_type < len(row) else "str"}
            req = row[i_req].strip().lower() if i_req is not None and i_req < len(row) else "yes"
            if req in {"no", "n", "false", "optional"}:
                arg["optional"] = True
            args.append(arg)
    if args:
        return [a for a in args if not a.get("optional")] + [a for a in args if a.get("optional")]
    for line in [ln for ln in text.splitlines() if not ln.strip().startswith("|")]:
        for pm in re.finditer(r"`([A-Za-z_][A-Za-z0-9_]*)(\[\])?\s*(?::\s*([A-Za-z_][A-Za-z0-9_\[\]]*))?`([^`,;]*)", line):
            name = _identifier(pm.group(1))
            if name in {a["name"] for a in args}:
                continue
            arg = {"name": name, "type": "list[str]" if pm.group(2) else _arg_type(pm.group(3) or "str")}
            if not pm.group(3) and not re.search(r"\b(required|optional)\b", pm.group(4), re.I):
                continue  # a ticked example value such as `MEM-###`, not a parameter
            if re.search(r"\boptional\b", pm.group(4), re.I):
                arg["optional"] = True
            args.append(arg)
    if not args:
        return [] if _is_placeholder(text) else None
    return [a for a in args if not a.get("optional")] + [a for a in args if a.get("optional")]


_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_NAME_LIST = re.compile(rf"(`?){_IDENT}\1(?:\s*(?:/|,|&|\band\b)\s*(`?){_IDENT}\2)*")
_TOOL_BULLETS = re.compile(r"^\s*[-*]\s*\*\*(?:Inputs?|Parameters?|Arguments?|Description)\*\*", re.M | re.I)
# "No effect" phrasings at the start of a Side Effects bullet; a mutation verb in
# the rest ("None; marks the case reserved") still declares an effect, and any
# other text ("Calls the OMS API (read)") is treated as a declared effect.
_NO_SIDE_EFFECTS = re.compile(
    r"^\s*(?:none|n/?a|nil|no\s+side[\s-]*effects?|no\s+state[\s-]*changes?|no\s+writes?|no\s+mutations?"
    r"|no\s+changes?|read[\s-]*only|-|—|–)(?=\W|$)", re.I)
_MUTATION = re.compile(
    r"\b(?:writ\w*|updat\w*|creat\w*|delet\w*|remov\w*|insert\w*|sets?|stor\w*|persist\w*|mark\w*|send\w*|sent"
    r"|reserv\w*|lock\w*|modif\w*|chang\w*|mutat\w*|record\w*|publish\w*|append\w*|replac\w*|cancel\w*)\b", re.I)


def _heading_names(heading: str) -> tuple[list[str], bool]:
    """Tool names a section 6 heading declares: ``### `a` ``, ``### `a` / `b` — BR-1``, ``### a / b``.

    Ticked names may be followed by any text; unticked names must be the whole
    heading apart from a trailing ``— note``, ``: note`` or ``(note)``.
    """
    m = _NAME_LIST.match(heading)
    if not m:
        return [], False
    names = re.findall(_IDENT, m.group(0).replace(" and ", " / "))
    ticked = "`" in m.group(0)
    rest = heading[m.end():]
    if ticked:
        ok = not rest or rest[0] in " \t:—–-("
    else:
        ok = not rest.strip() or bool(re.match(r"\s*(?:[—–:(]|-\s)", rest))
    return (names, ticked) if ok else ([], False)


def _section6_headings(block: str):
    """``(heading text, names, ticked, body)`` for every level-3/4 heading of section 6."""
    for m in re.finditer(r"^#{3,4}\s+([^\n]*)\n(.*?)(?=^#{2,4}\s|\Z)", block, re.M | re.S):
        names, ticked = _heading_names(m.group(1).strip())
        yield m.group(1).strip(), names, ticked, m.group(2)


def _bullet(body: str, label: str) -> str:
    m = re.search(rf"^\s*[-*]\s*\*\*{label}\*\*\s*:?(.*?)(?=^\s*[-*]\s*\*\*|^#{{2,4}}\s|\Z)", body, re.M | re.S | re.I)
    return " ".join(m.group(1).split()) if m else ""


def declared_side_effects(text: str) -> str:
    """The section 6 ``**Side Effects**`` text when it declares an effect, else ``""``."""
    text = _strip_ticks(text)
    if not text:
        return ""
    m = _NO_SIDE_EFFECTS.match(text)
    return "" if m and not _MUTATION.search(text[m.end():]) else text


def _tool_subsections(block: str) -> dict[str, dict[str, Any]]:
    """``### `tool_name` — BR-...`` subsections of section 6, in order (every name of a multi-name heading)."""
    found: dict[str, dict[str, Any]] = {}
    for _heading, names, ticked, body in _section6_headings(block):
        # Unticked template headings (``### get_member``) count only with tool bullets.
        if not names or (not ticked and not _TOOL_BULLETS.search(body)):
            continue
        desc = re.search(r"^\s*[-*]\s*\*\*Description\*\*\s*:?\s*(.+)$", body, re.M | re.I)
        inputs = re.search(r"^\s*[-*]\s*\*\*(?:Inputs?|Parameters?|Arguments?)\*\*\s*:?(.*?)(?=^\s*[-*]\s*\*\*|^#{2,4}\s|\Z)",
                           body, re.M | re.S | re.I)
        backed = re.search(r"^\s*[-*]\s*\*\*Backed\s+by\*\*\s*:?\s*(.+)$", body, re.M | re.I)
        info = {
            "args": _subsection_args(body),
            "no_inputs": bool(inputs and EXPLICIT_NO_INPUTS.match(_strip_ticks(inputs.group(1)))),
            "description": _strip_ticks(desc.group(1)) if desc else "",
            "knowledge": bool(backed and KNOWLEDGE_BACKING.search(backed.group(1))),
            "side_effects": declared_side_effects(_bullet(body, r"Side\s+Effects?")),
        }
        for name in names:
            found.setdefault(_identifier(name), dict(info))
    return found


def _consequences(spec: str) -> dict[str, str]:
    """Section 11a ``tools: - id: x / consequence: y`` and section 5b Read/Write column -> read|write."""
    kinds: dict[str, str] = {}
    sec5b = re.search(r"^#{2,3}\s*5b\b[^\n]*\n(.*?)(?=^##\s+(?!5b)\d|\Z)", spec, re.M | re.S | re.I)
    for table in _tables(sec5b.group(1) if sec5b else ""):
        header = [h.lower().replace(" ", "") for h in table[0]]
        i_rw = next((i for i, h in enumerate(header) if h in {"read/write", "r/w", "access", "mode"}), None)
        if i_rw is None or "tool" not in header[0]:
            continue
        for row in table[1:]:
            names = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", row[0]) or [row[0].strip()]
            mode = row[i_rw].strip().upper().replace(" ", "") if i_rw < len(row) else ""
            if names and mode:
                kinds[_identifier(names[0])] = "read" if mode in {"R", "READ", "READ-ONLY", "RO"} else "write"
    sec11a = re.search(r"^#{2,4}\s*11a\b[^\n]*\n(.*?)(?=^#{2,4}\s*11[b-z]\b|^##\s+\d|\Z)", spec, re.M | re.S | re.I)
    if sec11a:
        for item in re.split(r"^\s*-\s+(?=id:)", sec11a.group(1), flags=re.M)[1:]:
            ident = re.match(r"id:\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)", item)
            cons = re.search(r"^\s*consequence:\s*[\"']?([A-Za-z_-]+)", item.split("\n- ", 1)[0], re.M)
            if ident and cons:
                kinds[_identifier(ident.group(1))] = "read" if cons.group(1).lower() in {"read", "read-only", "read_only"} else "write"
    return kinds


def _verb_in(name: str, prefixes: tuple[str, ...]) -> bool:
    """``name`` starts with a verb of ``prefixes`` or has it as its second token (``returns_get_case``)."""
    tokens = name.split("_")
    return name.startswith(prefixes) or (len(tokens) >= 2 and f"{tokens[1]}_" in prefixes)


def _reads_by_name(name: str) -> bool:
    return _verb_in(name, READ_PREFIXES)


def tools(spec: str) -> list[dict[str, Any]]:
    """SPEC section 6 (Tool Contracts) as a typed tool plan, in SPEC order.

    Section 6 may be a summary table, ``### `tool` `` subsections, or both. Per-tool
    ``**Inputs**`` subsections are canonical; table placeholders such as
    ``none / see below`` never become arguments. Read/write classification uses
    section 11a ``consequence`` first, then the section 5b Read/Write column,
    then the section 6 idempotency column, then the tool-name heuristic.
    """
    block = _section(spec, "6") or _section(spec, "5b")
    subsections = _tool_subsections(_section(spec, "6"))
    consequences = _consequences(spec)
    rows: dict[str, dict[str, Any]] = {}
    for table in _tables(block):
        header = [h.lower() for h in table[0]]
        if not header or "tool" not in header[0]:
            continue
        idx_input = next((i for i, h in enumerate(header) if "input" in h), 1)
        idx_idem = next((i for i, h in enumerate(header) if "idempot" in h or "failure" in h), None)
        idx_desc = next((i for i, h in enumerate(header) if "output" in h or "description" in h), idx_input + 1)
        for row in table[1:]:
            names = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", row[0]) or [row[0].strip()]
            name = _identifier(names[0])
            if not name or name in rows:
                continue
            inputs = row[idx_input] if idx_input < len(row) else ""
            rows[name] = {
                "args": [] if _is_placeholder(inputs) else (_ticked_args(inputs) or _prose_args(inputs)),
                "no_inputs": bool(EXPLICIT_NO_INPUTS.match(_strip_ticks(inputs))),
                "idem": row[idx_idem].lower() if idx_idem is not None and idx_idem < len(row) else "",
                "description": _strip_ticks(row[idx_desc]) if idx_desc < len(row) else "",
            }
    plan: list[dict[str, Any]] = []
    for name in list(rows) + [n for n in subsections if n not in rows]:
        row = rows.get(name, {"args": [], "no_inputs": False, "idem": "", "description": ""})
        sub = subsections.get(name, {})
        source = sub if sub.get("args") is not None else row
        args, no_inputs = source["args"], source.get("no_inputs", False) and not source["args"]
        idem = row["idem"]
        if name in consequences:
            kind = consequences[name]
        elif sub.get("side_effects"):
            kind = "write"  # section 6 declares an effect, whatever the name says
        elif sub.get("knowledge"):
            kind = "read"  # a knowledge-base search is a read unless section 11a says otherwise
        elif "read-only" in idem or "read only" in idem:
            kind = "read"
        else:
            kind = "read" if not idem and _reads_by_name(name) else "write"
        verb, _, noun = name.partition("_")
        nouns = [n for n in (noun or verb).split("_") if n]
        plan.append({
            "name": name,
            "kind": kind,
            "many": kind == "read" and name.startswith(MANY_PREFIXES),
            "nouns": nouns,
            "args": args,
            "description": sub.get("description") or row["description"],
            **({"no_inputs": True} if no_inputs else {}),
            **({"knowledge": True} if sub.get("knowledge") and kind == "read" else {}),
            **({"side_effects": sub["side_effects"]} if sub.get("side_effects") else {}),
        })
    if not plan:
        raise SpecToolsError("SPEC section 6 (Tool Contracts) has no tool table or `### `tool`` subsections")
    return plan


def section6_inventory(spec: str) -> dict[str, list]:
    """Every section 6 entry: the parsed tool plan plus what could not be parsed as a tool.

    ``unparsed`` lists headings that name no tool (or an unticked name without
    tool bullets), tool-table rows whose first cell is not a tool name, and
    tables before the first tool heading that are not tool tables. Consumers
    that must fail closed (recommend-only pilots) treat each as a gap.
    """
    block = _section(spec, "6")
    unparsed: list[str] = []
    for heading, names, ticked, body in _section6_headings(block):
        if not names or (not ticked and not _TOOL_BULLETS.search(body)):
            unparsed.append(f"heading '{heading}'")
    preamble = re.split(r"^#{3,4}\s", block, maxsplit=1, flags=re.M)[0]
    for table in _tables(block):
        header = [h.lower() for h in table[0]]
        if not header or "tool" not in header[0]:
            if "|".join(table[0]) in preamble:
                unparsed.append(f"table '{' | '.join(table[0])}'")
            continue
        for row in table[1:]:
            cell = row[0].strip()
            names = re.findall(rf"`({_IDENT})`", cell) or [p.strip() for p in re.split(r"\s*(?:/|,)\s*", _strip_ticks(cell))]
            if not cell or not all(re.fullmatch(_IDENT, n) for n in names):
                unparsed.append(f"table row '{cell}'")
    try:
        plan = tools(spec)
    except SpecToolsError:
        plan = []
    return {"tools": plan, "unparsed": unparsed}

