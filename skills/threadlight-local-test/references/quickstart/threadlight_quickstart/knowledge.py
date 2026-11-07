"""Read-only local knowledge search over ``specs/knowledge/*.md``.

Pattern 0 stand-in for a SPEC section 6 knowledge tool (Foundry IQ / AI Search
in production). It splits each Markdown file at its headings, ranks sections
by clause-id and word overlap with the query, and returns the top matches with
their clause ids so the agent can cite them. It is not Foundry IQ evidence.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

CLAUSE = re.compile(r"\b([A-Z]{1,6}-\d+(?:\.\d+)*)\b")
BACKING = "local stand-in: specs/knowledge (Pattern 0; Foundry IQ in production)"


def sections(knowledge_dir: Path) -> list[dict[str, str]]:
    """Paragraphs of every ``*.md``; a paragraph opening with a clause id is its own unit."""
    found: list[dict[str, str]] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        title = path.stem
        for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")):
            block = block.strip()
            if not block:
                continue
            if block.startswith("#"):
                heading, _, block = block.partition("\n")
                title = heading.lstrip("#").strip()
                block = block.strip()
                if not block:
                    continue
            own = CLAUSE.match(block.lstrip("*_> "))
            heading_clause = CLAUSE.search(title)
            clause = own.group(1) if own else (heading_clause.group(1) if heading_clause else title)
            found.append({"clause_id": clause, "title": title, "text": block[:1200], "source": path.name})
    return found


def search(knowledge_dir: Path, query: str, top: int = 3) -> dict[str, Any]:
    wanted = set(CLAUSE.findall(query or ""))
    words = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))
    scored = []
    for section in sections(knowledge_dir):
        haystack = f"{section['title']} {section['text']}".lower()
        score = (10 if section["clause_id"] in wanted else 0) + sum(1 for w in words if w in haystack)
        if score:
            scored.append((score, section))
    scored.sort(key=lambda item: -item[0])
    hits = [s for _, s in scored[:top]]
    if not hits:
        return {"answer": "", "citations": [], "backing": BACKING,
                "message": "no matching clause; say so and do not invent one"}
    return {"answer": hits[0]["text"], "backing": BACKING,
            "citations": [{"clause_id": h["clause_id"], "text": h["text"], "source": h["source"]} for h in hits]}
