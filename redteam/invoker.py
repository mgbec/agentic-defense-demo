"""Call the deployed agent runtime."""

from __future__ import annotations

import json
import uuid
from typing import Any

import boto3
from botocore.config import Config

# An agent run makes several model calls, so allow generous time before giving up.
READ_TIMEOUT_SECONDS = 240


def make_client(region: str) -> Any:
    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _read_body(body: Any) -> bytes:
    if hasattr(body, "read"):
        return body.read()
    return b"".join(body)


def _decode(content_type: str, raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8")
    if "text/event-stream" in content_type:
        payloads = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
        text = payloads[-1] if payloads else ""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object from the agent, got {type(data).__name__}")
    return data


class RuntimeInvoker:
    """invoke(prompt, profile) -> the agent's result dict. Each call is a fresh session, so one
    attack can never influence another through conversation state."""

    def __init__(self, client: Any, runtime_arn: str) -> None:
        self._client = client
        self._arn = runtime_arn

    def __call__(self, prompt: str, profile: str) -> dict[str, Any]:
        response = self._client.invoke_agent_runtime(
            agentRuntimeArn=self._arn,
            # Session ids must be at least 33 characters.
            runtimeSessionId="redteam-" + uuid.uuid4().hex,
            payload=json.dumps({"prompt": prompt, "profile": profile}).encode("utf-8"),
        )
        raw = _read_body(response["response"])
        return _decode(str(response.get("contentType", "")), raw)
