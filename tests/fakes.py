"""Scriptable stand-ins for AWS clients, so defense logic can be tested without AWS."""

from __future__ import annotations

from typing import Any


class FakeBedrock:
    """Stands in for the bedrock-runtime client's apply_guardrail.

    block_if: if the text contains any of these substrings, the guardrail blocks it.
    mask_if:  mapping of substring -> replacement; the guardrail masks instead of blocking.
    raises:   if set, apply_guardrail raises this exception.
    """

    def __init__(
        self,
        block_if: tuple[str, ...] = (),
        mask_if: dict[str, str] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.block_if = block_if
        self.mask_if = mask_if or {}
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def apply_guardrail(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises

        text = kwargs["content"][0]["text"]["text"]
        if any(s in text for s in self.block_if):
            return {
                "action": "GUARDRAIL_INTERVENED",
                "outputs": [{"text": "blocked"}],
                "assessments": [
                    {"contentPolicy": {"filters": [{"type": "PROMPT_ATTACK", "action": "BLOCKED"}]}}
                ],
            }
        for needle, replacement in self.mask_if.items():
            if needle in text:
                return {
                    "action": "GUARDRAIL_INTERVENED",
                    "outputs": [{"text": text.replace(needle, replacement)}],
                    "assessments": [
                        {
                            "sensitiveInformationPolicy": {
                                "piiEntities": [
                                    {"type": "US_SOCIAL_SECURITY_NUMBER", "action": "ANONYMIZED"}
                                ]
                            }
                        }
                    ],
                }
        return {"action": "NONE", "outputs": [], "assessments": []}
