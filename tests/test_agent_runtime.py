from types import SimpleNamespace

import pytest
from agent_runtime import StrandsRunner, final_text

from defenses.config import for_profile


def test_final_text_joins_text_blocks_of_the_last_message():
    result = SimpleNamespace(message={"content": [{"text": "Hello "}, {"text": "there"}]})
    assert final_text(result) == "Hello there"


def test_final_text_ignores_non_text_blocks():
    result = SimpleNamespace(message={"content": [{"toolUse": {}}, {"text": "done"}]})
    assert final_text(result) == "done"


def test_final_text_falls_back_to_str_for_unexpected_results():
    class Odd:
        def __str__(self):
            return "odd result"

    assert final_text(Odd()) == "odd result"


ENV = {
    "AWS_REGION": "us-east-1",
    "MODEL_ID": "m",
    "GATEWAY_URL_OPEN": "https://open.example/mcp",
    "GATEWAY_URL_GUARDED": "https://guarded.example/mcp",
}


@pytest.mark.parametrize(
    "profile, url",
    [
        ("none", "https://open.example/mcp"),
        ("guardrail", "https://open.example/mcp"),
        ("isolation", "https://open.example/mcp"),
        ("policy", "https://guarded.example/mcp"),
    ],
)
def test_only_the_policy_profile_uses_the_guarded_gateway(profile, url):
    runner = StrandsRunner(ENV, guardrail=None)
    assert runner._gateway_url(for_profile(profile)) == url
