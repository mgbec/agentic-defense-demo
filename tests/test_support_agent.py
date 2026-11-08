import pytest
from support_agent import BLOCKED_REPLY, WITHHELD_REPLY, handle, run_request

from common.constants import CANARY_SSN, CANARY_SYSTEM_PROMPT, PROFILES
from defenses.config import for_profile
from defenses.guardrail import Guardrail
from defenses.prompts import system_prompt
from tests.fakes import FakeBedrock


def guardrail(**kwargs):
    return Guardrail(FakeBedrock(**kwargs), "g", "1")


class Runner:
    """A fake agent loop that returns fixed text and records what it was given."""

    def __init__(self, reply="hello", raises=None, on_run=None):
        self.reply, self.raises, self.on_run = reply, raises, on_run
        self.prompts = []

    def __call__(self, prompt, config, trace):
        self.prompts.append(prompt)
        if self.on_run:
            self.on_run(trace)
        if self.raises:
            raise self.raises
        return self.reply


# ---------------------------------------------------------------- profiles
def test_profiles_are_cumulative():
    flags = [(c.guardrail, c.isolation, c.gateway) for c in map(for_profile, PROFILES)]
    assert flags == [
        (False, False, "open"),
        (True, False, "open"),
        (True, True, "open"),
        (True, True, "guarded"),
    ]


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError):
        for_profile("maximum")


def test_hardened_prompt_adds_rules_but_keeps_the_secret_in_both():
    basic, hardened = system_prompt(False), system_prompt(True)
    assert CANARY_SYSTEM_PROMPT in basic and CANARY_SYSTEM_PROMPT in hardened
    assert "untrusted" in hardened and "untrusted" not in basic
    assert hardened.startswith(basic)


# ---------------------------------------------------------------- request flow
def test_no_defenses_means_no_guardrail_calls():
    runner = Runner("hi")
    result = run_request("hello", for_profile("none"), guardrail=None, run_agent=runner)
    assert result["response"] == "hi"
    assert result["interventions"] == []
    assert result["blocked_by"] is None


def test_a_blocked_input_never_reaches_the_agent():
    runner = Runner()
    result = run_request(
        "ignore everything", for_profile("guardrail"),
        guardrail=guardrail(block_if=("ignore",)), run_agent=runner,
    )
    assert runner.prompts == []
    assert result["response"] == BLOCKED_REPLY
    assert result["blocked_by"] == "guardrail_input"
    assert result["interventions"] == ["guardrail_input"]


def test_sensitive_input_is_masked_before_the_agent_sees_it():
    runner = Runner()
    run_request(
        f"my ssn is {CANARY_SSN}", for_profile("guardrail"),
        guardrail=guardrail(mask_if={CANARY_SSN: "{SSN}"}), run_agent=runner,
    )
    assert runner.prompts == ["my ssn is {SSN}"]


def test_blocked_output_is_replaced():
    result = run_request(
        "hello", for_profile("guardrail"),
        guardrail=guardrail(block_if=("SECRET",)), run_agent=Runner("the SECRET is 1"),
    )
    assert result["response"] == WITHHELD_REPLY
    assert result["blocked_by"] == "guardrail_output"


def test_masked_output_is_used_instead_of_the_original():
    result = run_request(
        "hello", for_profile("guardrail"),
        guardrail=guardrail(mask_if={CANARY_SSN: "{SSN}"}),
        run_agent=Runner(f"ssn {CANARY_SSN}"),
    )
    assert result["response"] == "ssn {SSN}"
    assert result["interventions"] == ["guardrail_output"]
    assert result["blocked_by"] is None


def test_an_agent_failure_is_reported_not_raised():
    result = run_request(
        "hello", for_profile("none"), guardrail=None, run_agent=Runner(raises=RuntimeError("x"))
    )
    assert "RuntimeError: x" in result["error"]
    assert result["response"]


def test_tool_level_interventions_are_listed_without_blocking_the_request():
    def act(trace):
        trace.add("tool_result_screen", "withhold", "kb-004")
        trace.add("gateway_policy", "deny", "send_email")

    result = run_request(
        "hi", for_profile("policy"), guardrail=guardrail(), run_agent=Runner(on_run=act)
    )
    assert result["interventions"] == ["tool_result_screen", "gateway_policy"]
    assert result["blocked_by"] is None


def test_the_result_always_reports_its_profile_and_timing():
    result = run_request("hi", for_profile("none"), guardrail=None, run_agent=Runner())
    assert result["profile"] == "none"
    assert isinstance(result["elapsed_ms"], int)


# ---------------------------------------------------------------- payload handling
def test_handle_defaults_to_the_fully_defended_profile():
    out = handle({"prompt": "hi"}, guardrail=guardrail(), run_agent=Runner())
    assert out["profile"] == "policy"


@pytest.mark.parametrize("payload", [{}, {"prompt": ""}, {"prompt": "  "}, {"prompt": 5}])
def test_handle_rejects_bad_prompts(payload):
    assert "error" in handle(payload, guardrail=None, run_agent=Runner())


def test_handle_rejects_unknown_profiles():
    out = handle({"prompt": "hi", "profile": "nope"}, guardrail=None, run_agent=Runner())
    assert "Unknown profile" in out["error"]
