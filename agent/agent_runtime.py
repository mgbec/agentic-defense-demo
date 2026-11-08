"""The real agent loop: Strands Agents + Amazon Bedrock + tools served by an AgentCore Gateway.

The gateway is reached over MCP with SigV4 (the runtime's own IAM role). Which gateway depends
on the profile: "open" has no policy engine, "guarded" enforces Cedar policy on every tool call.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient

from defenses.config import DefenseConfig
from defenses.guardrail import Guardrail
from defenses.hooks import IsolationHook, ToolTraceHook
from defenses.prompts import system_prompt
from defenses.trace import Trace


def final_text(result: Any) -> str:
    """The assistant's last message as plain text."""
    try:
        blocks = result.message["content"]
        text = "".join(b["text"] for b in blocks if "text" in b)
        if text:
            return text
    except (AttributeError, KeyError, TypeError):
        pass
    return str(result)


class StrandsRunner:
    """Callable matching support_agent.AgentRunner."""

    def __init__(self, env: dict[str, str], guardrail: Guardrail | None) -> None:
        self._env = env
        self._guardrail = guardrail

    def _gateway_url(self, config: DefenseConfig) -> str:
        key = "GATEWAY_URL_GUARDED" if config.gateway == "guarded" else "GATEWAY_URL_OPEN"
        return self._env[key]

    def __call__(self, prompt: str, config: DefenseConfig, trace: Trace) -> str:
        region = region_of(self._env)
        gateway_url = self._gateway_url(config)

        mcp = MCPClient(
            lambda: aws_iam_streamablehttp_client(
                endpoint=gateway_url, aws_service="bedrock-agentcore", aws_region=region
            )
        )
        model = BedrockModel(
            model_id=self._env["MODEL_ID"], region_name=region, temperature=0.2, max_tokens=1024
        )

        hooks: list[Any] = [ToolTraceHook(trace)]
        if config.isolation:
            assert self._guardrail is not None
            hooks.append(IsolationHook(self._guardrail, trace))

        with mcp:
            agent = Agent(
                model=model,
                tools=mcp.list_tools_sync(),
                system_prompt=system_prompt(hardened=config.isolation),
                hooks=hooks,
                callback_handler=None,
            )
            return final_text(agent(prompt))


def region_of(env: dict[str, str]) -> str:
    """The region to use. DEMO_REGION is set by Terraform in case the platform does not provide
    AWS_REGION itself."""
    return env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or env["DEMO_REGION"]


def build_guardrail(env: dict[str, str]) -> Guardrail:
    client = boto3.client("bedrock-runtime", region_name=region_of(env))
    return Guardrail(client, env["GUARDRAIL_ID"], env["GUARDRAIL_VERSION"])


def environment() -> dict[str, str]:
    return dict(os.environ)
