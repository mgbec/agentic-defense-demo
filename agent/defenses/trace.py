"""A per-request record of what each defense layer decided. The web page and the red-team
scoreboard use it to show which layer stopped an attack."""

from __future__ import annotations

from typing import Any

MAX_VALUE_CHARS = 300


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + "..."
    if isinstance(value, dict):
        return {k: _short(v) for k, v in value.items()}
    return value


class Trace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.blocked_by: str | None = None

    def add(self, layer: str, decision: str, detail: str = "") -> None:
        """decision is one of: allow, block, mask, withhold, deny, info."""
        self.events.append({"layer": layer, "decision": decision, "detail": detail})

    def block(self, layer: str, detail: str = "") -> None:
        self.add(layer, "block", detail)
        if self.blocked_by is None:
            self.blocked_by = layer

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked_by": self.blocked_by,
            "trace": self.events,
            "tool_calls": [_short(c) for c in self.tool_calls],
        }
