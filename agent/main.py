"""AgentCore Runtime entry point.

Payload:  {"prompt": "...", "profile": "none" | "guardrail" | "isolation" | "policy"}
Response: {"response": "...", "profile": ..., "blocked_by": ..., "interventions": [...],
           "trace": [...], "tool_calls": [...], "elapsed_ms": ...}
"""

from __future__ import annotations

import agent_runtime
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from support_agent import handle

from defenses.config import allowed_profiles

app = BedrockAgentCoreApp()

_env = agent_runtime.environment()
_guardrail = agent_runtime.build_guardrail(_env)
_runner = agent_runtime.StrandsRunner(_env, _guardrail)
_allowed = allowed_profiles(_env)  # Terraform narrows this to "policy" in lockdown


@app.entrypoint
def invoke(payload: dict, context=None) -> dict:
    return handle(payload, guardrail=_guardrail, run_agent=_runner, allowed=_allowed)


if __name__ == "__main__":
    app.run()
