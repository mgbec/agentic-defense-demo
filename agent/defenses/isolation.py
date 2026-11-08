"""Layer 2: treat retrieved content as untrusted data.

Documents the agent retrieves can carry instructions aimed at the model ("indirect prompt
injection"). Two measures:

1. Screen each document with the guardrail's prompt-attack detection, and withhold any document
   that is flagged.
2. Fence what remains in <untrusted_document> tags, which the hardened system prompt tells the
   model never to obey. This part depends on the model cooperating, so it is a weaker layer
   than the screen and nowhere near a guarantee.
"""

from __future__ import annotations

import json
from typing import Any

from defenses.guardrail import Guardrail

WITHHELD = "[Document withheld: it contained instructions aimed at the assistant.]"

# Tools whose output is content from outside the conversation.
UNTRUSTED_TOOLS = frozenset({"search_kb"})


def _fence(doc_id: str, text: str) -> str:
    return f'<untrusted_document id="{doc_id}">\n{text}\n</untrusted_document>'


def process(raw: str, guardrail: Guardrail) -> tuple[str, list[tuple[str, str]]]:
    """Screen and fence a tool result.

    Returns the text to hand to the model and a list of (decision, detail) events for the trace.
    """
    events: list[tuple[str, str]] = []

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        payload = None

    docs = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(docs, list) or not all(isinstance(d, dict) for d in docs):
        # Unknown shape: screen the whole thing as one document.
        verdict = guardrail.check(raw, "INPUT")
        if verdict.blocked:
            events.append(("withhold", f"tool output: {verdict.detail()}"))
            return WITHHELD, events
        events.append(("allow", "tool output screened"))
        return _fence("tool-output", raw), events

    kept: list[dict[str, Any]] = []
    for doc in docs:
        doc_id = str(doc.get("id", "unknown"))
        title = str(doc.get("title", ""))
        verdict = guardrail.check(f"{title}\n{doc.get('text', '')}", "INPUT")
        if verdict.blocked:
            events.append(("withhold", f"{doc_id}: {verdict.detail()}"))
            kept.append({"id": doc_id, "title": title, "text": WITHHELD})
        else:
            events.append(("allow", f"{doc_id} screened"))
            kept.append(
                {"id": doc_id, "title": title, "text": _fence(doc_id, str(doc.get("text", "")))}
            )

    return json.dumps({"results": kept}), events
