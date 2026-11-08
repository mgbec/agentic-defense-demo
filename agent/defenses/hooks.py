"""Strands hooks that apply the tool-level defenses and record what happened.

Strands lets a hook cancel a tool call before it runs (BeforeToolCallEvent.cancel_tool) and
replace a tool's result before the model sees it (AfterToolCallEvent.result). Those two
capabilities are all we use.
"""

from __future__ import annotations

import json
from typing import Any

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from defenses import isolation
from defenses.guardrail import Guardrail
from defenses.trace import Trace

# A runaway agent loop is a risk in its own right (cost, repeated side effects).
MAX_TOOL_CALLS = 6

# Words that suggest the gateway refused a call, as opposed to the tool itself failing.
_DENIAL_MARKERS = ("denied", "not authorized", "unauthorized", "forbidden", "not permitted")


def short_name(tool_name: str) -> str:
    """Gateway tools are named '<target>___<tool>'; return just '<tool>'."""
    return tool_name.split("___")[-1]


def result_text(result: dict[str, Any]) -> str:
    parts = []
    for block in result.get("content", []):
        if "text" in block:
            parts.append(block["text"])
        elif "json" in block:
            parts.append(json.dumps(block["json"]))
    return "\n".join(parts)


class ToolTraceHook(HookProvider):
    """Records every tool call, enforces a call budget, and spots gateway denials."""

    def __init__(self, trace: Trace) -> None:
        self.trace = trace
        self._calls = 0
        self._by_id: dict[str, dict[str, Any]] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)
        registry.add_callback(AfterToolCallEvent, self.after)

    def before(self, event: BeforeToolCallEvent) -> None:
        self._calls += 1
        use = event.tool_use
        entry = {"name": short_name(use.get("name", "")), "args": use.get("input", {}),
                 "status": "pending", "summary": ""}
        self.trace.tool_calls.append(entry)
        self._by_id[use.get("toolUseId", str(self._calls))] = entry

        if self._calls > MAX_TOOL_CALLS:
            event.cancel_tool = "Tool call budget exceeded for this request."
            self.trace.add("tool_budget", "deny", f"more than {MAX_TOOL_CALLS} tool calls")

    def after(self, event: AfterToolCallEvent) -> None:
        entry = self._by_id.get(event.tool_use.get("toolUseId", ""))
        text = result_text(event.result)
        status = event.result.get("status", "error")
        if entry is not None:
            entry["status"] = "cancelled" if event.cancel_message else status
            entry["summary"] = text[:200]

        if status == "error" and any(m in text.lower() for m in _DENIAL_MARKERS):
            name = entry["name"] if entry else event.tool_use.get("name", "")
            self.trace.add("gateway_policy", "deny", f"{name}: {text[:160]}")
            if entry is not None:
                entry["status"] = "denied"


class IsolationHook(HookProvider):
    """Screens retrieved documents and fences them off as untrusted data."""

    def __init__(self, guardrail: Guardrail, trace: Trace) -> None:
        self.guardrail = guardrail
        self.trace = trace

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.after)

    def after(self, event: AfterToolCallEvent) -> None:
        if short_name(event.tool_use.get("name", "")) not in isolation.UNTRUSTED_TOOLS:
            return
        if event.result.get("status") != "success":
            return

        new_text, events = isolation.process(result_text(event.result), self.guardrail)
        for decision, detail in events:
            self.trace.add("tool_result_screen", decision, detail)
        event.result = {**event.result, "content": [{"text": new_text}]}
