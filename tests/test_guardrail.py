import pytest

from defenses.guardrail import ALLOW, BLOCK, MASK, Guardrail, summarize
from tests.fakes import FakeBedrock


def make(**kwargs):
    client = FakeBedrock(**kwargs)
    return Guardrail(client, "gr-1", "3"), client


def test_clean_text_is_allowed_unchanged():
    guardrail, _ = make()
    verdict = guardrail.check("hello", "INPUT")
    assert verdict.action == ALLOW and verdict.text == "hello" and not verdict.blocked


def test_blocked_text_is_reported_with_reasons():
    guardrail, _ = make(block_if=("attack",))
    verdict = guardrail.check("an attack", "INPUT")
    assert verdict.blocked and verdict.action == BLOCK
    assert "PROMPT_ATTACK (blocked)" in verdict.detail()


def test_masked_text_is_not_blocked_and_carries_the_masked_version():
    guardrail, _ = make(mask_if={"512-44-9087": "{SSN}"})
    verdict = guardrail.check("ssn 512-44-9087", "OUTPUT")
    assert verdict.action == MASK and not verdict.blocked
    assert verdict.text == "ssn {SSN}"
    assert "US_SOCIAL_SECURITY_NUMBER (masked)" in verdict.detail()


def test_uses_the_pinned_guardrail_id_and_version():
    guardrail, client = make()
    guardrail.check("x", "INPUT")
    assert client.calls[0]["guardrailIdentifier"] == "gr-1"
    assert client.calls[0]["guardrailVersion"] == "3"


def test_input_is_tagged_so_prompt_attack_detection_runs_but_output_is_not():
    guardrail, client = make()
    guardrail.check("x", "INPUT")
    guardrail.check("x", "OUTPUT")
    assert client.calls[0]["content"][0]["text"]["qualifiers"] == ["guard_content"]
    assert "qualifiers" not in client.calls[1]["content"][0]["text"]


def test_fails_closed_when_the_guardrail_call_errors():
    guardrail, _ = make(raises=RuntimeError("throttled"))
    verdict = guardrail.check("anything", "INPUT")
    assert verdict.blocked
    assert "guardrail unavailable" in verdict.detail()


def test_rejects_an_invalid_source():
    guardrail, _ = make()
    with pytest.raises(ValueError):
        guardrail.check("x", "SIDEWAYS")


@pytest.mark.parametrize(
    "assessments, blocked",
    [
        ([], False),
        ([{"x": [{"action": "ANONYMIZED", "type": "EMAIL"}]}], False),
        ([{"x": [{"action": "BLOCKED", "type": "HATE"}]}], True),
        ([{"a": {"action": "ANONYMIZED", "type": "EMAIL"}, "b": [{"action": "BLOCKED"}]}], True),
        ([{"action": "NONE"}], False),
    ],
)
def test_summarize_tells_blocking_from_masking(assessments, blocked):
    assert summarize(assessments)[0] is blocked
