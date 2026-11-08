"""Request flow for the support assistant, independent of any agent framework.

    user message -> [guardrail: input] -> agent loop (tools, hooks) -> [guardrail: output] -> reply

The agent loop itself is injected (``run_agent``), so this file can be tested with a fake one.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from defenses.config import DefenseConfig, for_profile
from defenses.guardrail import Guardrail
from defenses.trace import Trace

BLOCKED_REPLY = "I'm sorry, I can't help with that request."
WITHHELD_REPLY = "I'm sorry, I can't share that information."
ERROR_REPLY = "Sorry, something went wrong while handling your request."

# (prompt, config, trace) -> the agent's final text
AgentRunner = Callable[[str, DefenseConfig, Trace], str]

_QUIET_DECISIONS = {"allow", "info"}


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
        response = run_agent(text, config, trace)
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


def handle(payload: dict[str, Any], *, guardrail: Guardrail | None, run_agent: AgentRunner) -> dict:
    """Validate a runtime payload and run the request."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return {"error": "payload.prompt must be a non-empty string"}
    try:
        config = for_profile(payload.get("profile", "policy"))
    except ValueError as exc:
        return {"error": str(exc)}
    return run_request(prompt, config, guardrail=guardrail, run_agent=run_agent)
