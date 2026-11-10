import io
import json
import subprocess

import pytest

from redteam import config as cfg
from redteam.invoker import RuntimeInvoker

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/agent-abc"


class FakeAgentCore:
    def __init__(self, body=b'{"response": "hi"}', content_type="application/json"):
        self.body, self.content_type = body, content_type
        self.calls = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return {"contentType": self.content_type, "response": io.BytesIO(self.body)}


# ---------------------------------------------------------------- invoker
def test_the_payload_carries_the_prompt_and_profile():
    client = FakeAgentCore()
    result = RuntimeInvoker(client, ARN)("hello", "isolation")
    sent = client.calls[0]
    assert sent["agentRuntimeArn"] == ARN
    assert json.loads(sent["payload"]) == {"prompt": "hello", "profile": "isolation"}
    assert result == {"response": "hi"}


def test_every_call_gets_a_fresh_valid_session_id():
    client = FakeAgentCore()
    invoker = RuntimeInvoker(client, ARN)
    invoker("a", "none")
    invoker("b", "none")
    ids = [c["runtimeSessionId"] for c in client.calls]
    assert len(set(ids)) == 2
    assert all(len(i) >= 33 for i in ids), "AgentCore requires session ids of 33+ characters"


def test_streamed_bodies_made_of_chunks_are_joined():
    client = FakeAgentCore()
    client.invoke_agent_runtime = lambda **kw: {
        "contentType": "application/json", "response": [b'{"resp', b'onse": "x"}']}
    assert RuntimeInvoker(client, ARN)("p", "none") == {"response": "x"}


def test_event_stream_responses_use_the_last_data_line():
    body = b'data: {"partial": true}\n\ndata: {"response": "final"}\n\n'
    client = FakeAgentCore(body, "text/event-stream")
    assert RuntimeInvoker(client, ARN)("p", "none") == {"response": "final"}


@pytest.mark.parametrize("body", [b"[1, 2]", b'"text"', b"not json"])
def test_a_reply_that_is_not_a_json_object_raises(body):
    with pytest.raises(ValueError):
        RuntimeInvoker(FakeAgentCore(body), ARN)("p", "none")


# ---------------------------------------------------------------- config from environment
ENV = {"DEMO_REGION": "us-east-1", "DEMO_RUNTIME_ARN": ARN, "DEMO_SINK_TABLE": "tbl"}


def test_environment_variables_win_and_terraform_is_not_consulted(tmp_path):
    assert cfg.load_config(ENV, terraform_dir=tmp_path / "missing") == cfg.DemoConfig(
        "us-east-1", ARN, "tbl")


def test_the_region_can_come_from_the_standard_variable():
    env = {"AWS_REGION": "eu-west-1", "DEMO_RUNTIME_ARN": ARN, "DEMO_SINK_TABLE": "t"}
    assert cfg.load_config(env).region == "eu-west-1"


# ---------------------------------------------------------------- config from terraform
def fake_terraform(stdout="", raises=None):
    def run(command, **kwargs):
        if raises:
            raise raises
        assert command[:3] == ["terraform", f"-chdir={cfg.INFRA_DIR}", "output"]
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
    return run


OUTPUTS = {
    "region": {"value": "us-east-1"},
    "agent_runtime_arn": {"value": ARN},
    "sink_table": {"value": "tbl"},
}


def test_terraform_outputs_are_used_when_the_environment_is_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_terraform(json.dumps(OUTPUTS)))
    assert cfg.load_config({}).runtime_arn == ARN


def test_no_outputs_means_nothing_is_deployed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_terraform("{}"))
    with pytest.raises(cfg.ConfigError, match="Nothing is deployed"):
        cfg.load_config({})


def test_a_missing_terraform_binary_is_explained(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_terraform(raises=FileNotFoundError()))
    with pytest.raises(cfg.ConfigError, match="not installed"):
        cfg.load_config({})


def test_a_failing_terraform_is_explained(monkeypatch):
    error = subprocess.CalledProcessError(1, "terraform", stderr="no state")
    monkeypatch.setattr(subprocess, "run", fake_terraform(raises=error))
    with pytest.raises(cfg.ConfigError, match="no state"):
        cfg.load_config({})


def test_point_sink_at_sets_the_table_and_clears_the_cache(monkeypatch):
    import os

    from common import sink
    monkeypatch.setattr(sink, "_generation", 0)
    cfg.point_sink_at(cfg.DemoConfig("us-west-2", ARN, "my-table"))
    assert os.environ["SINK_TABLE"] == "my-table" and os.environ["AWS_REGION"] == "us-west-2"
    assert sink._generation == 1


def test_an_old_boto3_gets_an_actionable_error(monkeypatch):
    import boto3
    from botocore.exceptions import UnknownServiceError

    from redteam.invoker import make_client

    def old_boto3(*args, **kwargs):
        raise UnknownServiceError(service_name="bedrock-agentcore", known_service_names=["s3"])

    monkeypatch.setattr(boto3, "client", old_boto3)
    with pytest.raises(cfg.ConfigError, match="virtual environment"):
        make_client("us-east-1")
