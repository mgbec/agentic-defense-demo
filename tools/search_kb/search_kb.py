"""Tool: search_kb. Read-only keyword search over a small knowledge base.

One of the documents is poisoned (see common/data.py). The tool returns it like any other
document, because from the tool's point of view it is just data. That is the scenario: content
the agent retrieves is not content the agent should obey. This function needs no database
access, and its IAM role has none.
"""

from __future__ import annotations

import re
from typing import Any

from common.data import KB_DOCS

MAX_RESULTS = 3


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2]


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    query = str(event.get("query", "")).strip()
    wanted = set(_tokens(query))
    if not wanted:
        return {"results": [], "error": "Empty query"}

    scored = []
    for doc in KB_DOCS:
        words = _tokens(f"{doc['title']} {doc['text']}")
        score = sum(words.count(w) for w in wanted)
        # Title matches count extra so a direct question finds the right article first.
        score += 3 * sum(1 for w in _tokens(doc["title"]) if w in wanted)
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    results = [
        {"id": d["id"], "title": d["title"], "text": d["text"]} for _, d in scored[:MAX_RESULTS]
    ]
    return {"results": results}
