"""Request flow for the support assistant, independent of any agent framework.

    user message -> [guardrail: input] -> agent loop (tools, hooks) -> [guardrail: output] -> reply

The agent loop itself is injected (``run_agent``), so this file can be tested with a fake one.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from defenses.config import DefenseConfig, for_profile
from defenses.guardrail import Guardrail
from defenses.trace import Trace

BLOCKED_REPLY = "I'm sorry, I can't help with that request."
WITHHELD_REPLY = "I'm sorry, I can't share that information."
ERROR_REPLY = "Sorry, something went wrong while handling your request."
NO_ANSWER_REPLY = "I'm sorry, I wasn't able to put together an answer. Could you rephrase that?"

# (prompt, config, trace) -> the agent's final text
AgentRunner = Callable[[str, DefenseConfig, Trace], str]

_QUIET_DECISIONS = {"allow", "info"}

_THINKING = re.compile(r"<thinking>.*?(?:</thinking>|\Z)", re.DOTALL | re.IGNORECASE)
_RESPONSE_TAG = re.compile(r"</?response>", re.IGNORECASE)
_UNCLOSED_RESPONSE = re.compile(r"</response(?!>)", re.IGNORECASE)


def clean_reply(text: str) -> str:
    """What the customer should see: the answer, without the model's reasoning markup.

    Some models wrap their reasoning in <thinking> tags and the answer in <response> tags. The
    reasoning is removed (an unclosed block is removed to the end, so it can never leak half of
    itself) and the answer tags are unwrapped. This runs before the output guardrail, so the
    guardrail checks exactly what the customer would read."""
    cleaned = _UNCLOSED_RESPONSE.sub("", _RESPONSE_TAG.sub("", _THINKING.sub("", text)))
    # A reply that was nothing but reasoning must not fall back to showing that reasoning.
    return cleaned.strip() or NO_ANSWER_REPLY


def _interventions(trace: Trace) -> list[str]:
    """Layers that stepped in (blocked, masked, withheld or denied something), in order."""
    seen: list[str] = []
    for event in trace.events:
        if event["decision"] not in _QUIET_DECISIONS and event["layer"] not in seen:
            seen.append(event["layer"])
    return seen


def run_request(
    prompt: str,
    config: DefenseConfig,
    *,
    guardrail: Guardrail | None,
    run_agent: AgentRunner,
) -> dict[str, Any]:
    started = time.perf_counter()
    trace = Trace()
    response = ""
    error = None

    def finish() -> dict[str, Any]:
        return {
            "profile": config.profile,
            "response": response,
            "error": error,
            "interventions": _interventions(trace),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            **trace.to_dict(),
        }

    text = prompt
    if config.guardrail:
        assert guardrail is not None, "guardrail profiles need a Guardrail"
        verdict = guardrail.check(text, "INPUT")
        if verdict.blocked:
            trace.block("guardrail_input", verdict.detail())
            response = BLOCKED_REPLY
            return finish()
        if verdict.action == "mask":
            trace.add("guardrail_input", "mask", verdict.detail())
            text = verdict.text
        else:
            trace.add("guardrail_input", "allow")

    try:
        response = clean_reply(run_agent(text, config, trace))
    except Exception as exc:  # noqa: BLE001 - report any agent failure to the caller
        error = f"{type(exc).__name__}: {exc}"
        trace.add("agent", "info", error)
        response = ERROR_REPLY
        return finish()

    if config.guardrail:
        verdict = guardrail.check(response, "OUTPUT")
        if verdict.blocked:
            trace.block("guardrail_output", verdict.detail())
            response = WITHHELD_REPLY
        elif verdict.action == "mask":
            trace.add("guardrail_output", "mask", verdict.detail())
            response = verdict.text
        else:
            trace.add("guardrail_output", "allow")

    return finish()


def handle(
    payload: dict[str, Any],
    *,
    guardrail: Guardrail | None,
    run_agent: AgentRunner,
    allowed: tuple[str, ...] | None = None,
) -> dict:
    """Validate a runtime payload and run the request.

    ``allowed`` limits which profiles this deployment will run (None means all)."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return {"error": "payload.prompt must be a non-empty string"}
    try:
        config = for_profile(payload.get("profile", "policy"))
    except ValueError as exc:
        return {"error": str(exc)}
    if allowed is not None and config.profile not in allowed:
        return {
            "error": (
                f"Profile '{config.profile}' is switched off on this deployment (lockdown). "
                f"Allowed: {', '.join(allowed)}. Turn lockdown off to demo the weaker profiles."
            )
        }
    return run_request(prompt, config, guardrail=guardrail, run_agent=run_agent)
