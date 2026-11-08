import pytest
from support_agent import (
    BLOCKED_REPLY,
    NO_ANSWER_REPLY,
    WITHHELD_REPLY,
    clean_reply,
    handle,
    run_request,
)

from common.constants import CANARY_SSN, CANARY_SYSTEM_PROMPT, PROFILES
from defenses.config import allowed_profiles, for_profile
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


# ---------------------------------------------------------------- reasoning markup

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<thinking>plan</thinking>\n\nHello there.", "Hello there."),
        ("<thinking>a</thinking>\n<response>Hi.</response>", "Hi."),
        ("<THINKING>\nmulti\nline\n</THINKING> Answer", "Answer"),
        ("<thinking>x</thinking>one<thinking>y</thinking> two", "one two"),
        # Truncated markup, as seen from a real model: no closing '>'.
        ("<thinking>x</thinking><response>Done.</response", "Done."),
        ("No markup at all.", "No markup at all."),
    ],
)
def test_clean_reply_removes_reasoning_markup(raw, expected):
    assert clean_reply(raw) == expected


def test_an_unclosed_thinking_block_is_removed_to_the_end():
    assert clean_reply("Answer.<thinking>half a thought") == "Answer."


def test_a_reply_that_is_only_reasoning_never_falls_back_to_showing_it():
    assert clean_reply("<thinking>secret plan</thinking>") == NO_ANSWER_REPLY
    assert clean_reply("<thinking>never closed") == NO_ANSWER_REPLY


def test_the_customer_sees_the_cleaned_reply_and_the_guardrail_checks_it():
    result = run_request(
        "hi", for_profile("guardrail"), guardrail=guardrail(block_if=("SECRET",)),
        run_agent=Runner("<thinking>SECRET reasoning</thinking>Hello."),
    )
    # The secret was only in the reasoning, which the customer never sees.
    assert result["response"] == "Hello."
    assert result["interventions"] == []


# ---------------------------------------------------------------- lockdown
def test_all_profiles_are_allowed_when_nothing_is_configured():
    assert allowed_profiles({}) == tuple(PROFILES)


def test_allowed_profiles_parses_a_list_in_canonical_order():
    assert allowed_profiles({"ALLOWED_PROFILES": " policy , none "}) == ("none", "policy")


@pytest.mark.parametrize("raw", ["", "  ", "bogus", "none-ish,"])
def test_a_setting_naming_no_valid_profile_fails_closed(raw):
    assert allowed_profiles({"ALLOWED_PROFILES": raw}) == ("policy",)


@pytest.mark.parametrize("profile", ["none", "guardrail", "isolation"])
def test_lockdown_refuses_weaker_profiles_without_running_the_agent(profile):
    runner = Runner()
    out = handle({"prompt": "hi", "profile": profile}, guardrail=None, run_agent=runner,
                 allowed=("policy",))
    assert "switched off" in out["error"] and "response" not in out
    assert runner.prompts == []


def test_lockdown_still_serves_the_defended_profile_and_the_default():
    for payload in ({"prompt": "hi", "profile": "policy"}, {"prompt": "hi"}):
        out = handle(payload, guardrail=guardrail(), run_agent=Runner(), allowed=("policy",))
        assert out["profile"] == "policy"
