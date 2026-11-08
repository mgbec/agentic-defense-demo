"""Layer 1: Amazon Bedrock Guardrails through the ApplyGuardrail API.

ApplyGuardrail reports "intervened" both when it blocks content and when it only masks part of
it (for example redacting a social security number). The two need different handling, so this
module tells them apart by looking for an action of BLOCKED in the assessments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ALLOW = "allow"
BLOCK = "block"
MASK = "mask"


@dataclass
class Verdict:
    action: str  # allow | block | mask
    text: str  # the text to carry forward (masked when action == mask)
    reasons: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.action == BLOCK

    def detail(self) -> str:
        return ", ".join(self.reasons)


def _walk(node: Any, found: list[tuple[str, str]], label_keys: tuple[str, ...]) -> None:
    """Collect (label, action) pairs from the nested assessment structure."""
    if isinstance(node, dict):
        action = node.get("action")
        if action in ("BLOCKED", "ANONYMIZED"):
            label = next((str(node[k]) for k in label_keys if node.get(k)), "policy")
            found.append((label, action))
        for value in node.values():
            _walk(value, found, label_keys)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found, label_keys)


def summarize(assessments: Any) -> tuple[bool, list[str]]:
    """Return (anything_blocked, human-readable reasons) from an ApplyGuardrail assessment."""
    found: list[tuple[str, str]] = []
    _walk(assessments, found, ("type", "name", "match"))
    blocked = any(action == "BLOCKED" for _, action in found)
    verbs = {"BLOCKED": "blocked", "ANONYMIZED": "masked"}
    reasons = sorted({f"{label} ({verbs[action]})" for label, action in found})
    return blocked, reasons


class Guardrail:
    def __init__(self, client: Any, guardrail_id: str, guardrail_version: str) -> None:
        self._client = client
        self._id = guardrail_id
        self._version = guardrail_version

    def check(self, text: str, source: str) -> Verdict:
        """Screen ``text``. source is "INPUT" (a user message or untrusted content) or "OUTPUT"."""
        if source not in ("INPUT", "OUTPUT"):
            raise ValueError("source must be INPUT or OUTPUT")

        block: dict[str, Any] = {"text": text}
        if source == "INPUT":
            # Prompt-attack detection only inspects content tagged as guard content.
            block["qualifiers"] = ["guard_content"]

        try:
            response = self._client.apply_guardrail(
                guardrailIdentifier=self._id,
                guardrailVersion=self._version,
                source=source,
                content=[{"text": block}],
            )
        except Exception as exc:  # noqa: BLE001 - fail closed on any guardrail failure
            return Verdict(BLOCK, text, [f"guardrail unavailable ({type(exc).__name__})"])

        if response.get("action") != "GUARDRAIL_INTERVENED":
            return Verdict(ALLOW, text)

        blocked, reasons = summarize(response.get("assessments", []))
        if blocked:
            return Verdict(BLOCK, text, reasons)

        masked = "".join(o.get("text", "") for o in response.get("outputs", []))
        return Verdict(MASK, masked or text, reasons)
