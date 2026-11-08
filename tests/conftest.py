"""Shared fixtures. Nothing here needs AWS credentials or network access."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from common import sink


@pytest.fixture(autouse=True)
def _fake_aws_env(monkeypatch):
    """Dummy credentials and region so no test can reach a real AWS account by accident."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def sink_table(monkeypatch):
    """An in-memory DynamoDB table laid out like the real sink table."""
    monkeypatch.setenv("SINK_TABLE", "test-sink")
    sink.reset_cache()
    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName="test-sink",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "kind", "AttributeType": "S"},
                {"AttributeName": "id", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "kind", "KeyType": "HASH"},
                {"AttributeName": "id", "KeyType": "RANGE"},
            ],
        )
        yield "test-sink"
    sink.reset_cache()
