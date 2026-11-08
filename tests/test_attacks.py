import pytest

from common.constants import (
    CANARY_CUSTOMER_NOTE,
    CANARY_SSN,
    CANARY_SYSTEM_PROMPT,
    POISON_ADDRESS,
    POISON_ADDRESS_SUBTLE,
)
from common.data import KB_DOCS, ORDERS
from redteam import attacks as defs


@pytest.fixture(scope="module")
def suite():
    return defs.load()


def valid_entry(**overrides):
    entry = {
        "id": "x", "title": "t", "category": "c", "kind": "attack", "prompt": "p",
        "oracle": {"type": "response_contains", "needles": ["n"]},
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------- the real file
def test_the_shipped_attack_file_loads(suite):
    assert len(suite) >= 10
    assert {a.kind for a in suite} == {"attack", "control", "benign"}


def test_ids_are_unique(suite):
    ids = [a.id for a in suite]
    assert len(ids) == len(set(ids))


def test_tokens_are_fully_expanded(suite):
    for a in suite:
        assert "{CANARY" not in str(a.oracle) and "{POISON" not in str(a.oracle), a.id


def test_attacks_and_controls_state_a_hypothesis(suite):
    for a in suite:
        assert a.hypothesis, f"{a.id} has no hypothesis"
        assert a.explanation, f"{a.id} has no explanation"


def test_there_is_a_quick_set_with_every_kind(suite):
    quick = defs.select(suite, quick=True)
    assert {a.kind for a in quick} == {"attack", "benign"}
    assert 4 <= len(quick) <= 10


def test_no_two_attacks_share_a_sink_key_by_accident(suite):
    """Shared keys would serialize runs, and worse, let one attack's rows satisfy another."""
    keys = [a.sink_key for a in suite if a.sink_key is not None]
    assert len(keys) == len(set(keys))


def test_refund_attacks_target_real_orders(suite):
    for a in suite:
        if a.oracle["type"] in ("refund_over", "refund_within"):
            assert a.oracle["order_id"] in ORDERS, a.id


def test_the_oracle_strings_match_the_real_secrets(suite):
    needles = {
        n for a in suite if a.oracle["type"] == "response_contains" for n in a.oracle["needles"]
    }
    assert {CANARY_SSN, CANARY_CUSTOMER_NOTE, CANARY_SYSTEM_PROMPT} <= needles
    addresses = {a.oracle["address"] for a in suite if a.oracle["type"] == "outbox_to"}
    assert {POISON_ADDRESS, POISON_ADDRESS_SUBTLE} <= addresses


def test_the_benign_question_can_be_answered_from_the_knowledge_base(suite):
    question = next(a for a in suite if a.id == "legit-question")
    needle = question.oracle["needles"][0].lower()
    assert any(needle in doc["text"].lower() for doc in KB_DOCS)


def test_benign_tasks_are_not_harmful_when_they_succeed(suite):
    assert all(a.harmful_if_success == (a.kind != "benign") for a in suite)


def test_prompts_are_collapsed_to_single_lines(suite):
    assert all("\n" not in a.prompt and "  " not in a.prompt for a in suite)


# ---------------------------------------------------------------- validation
def test_a_minimal_valid_entry_parses():
    [attack] = defs.parse({"attacks": [valid_entry()]})
    assert attack.sink_key is None


def test_sink_keys_are_normalized():
    entry = valid_entry(oracle={"type": "outbox_to", "address": "  Evil@X.Test "})
    assert defs.parse({"attacks": [entry]})[0].sink_key == "evil@x.test"


@pytest.mark.parametrize(
    "bad, fragment",
    [
        (valid_entry(kind="nonsense"), "kind must be one of"),
        (valid_entry(oracle={"type": "telepathy"}), "unknown oracle type"),
        (valid_entry(oracle={"type": "outbox_to"}), "needs 'address'"),
        (valid_entry(oracle={"type": "refund_over"}), "needs 'order_id'"),
        (valid_entry(oracle={"type": "response_contains", "needles": "x"}), "must be a list"),
        (valid_entry(prompt=""), "missing 'prompt'"),
        ("not a mapping", "not a mapping"),
    ],
)
def test_malformed_entries_are_rejected_with_a_clear_message(bad, fragment):
    with pytest.raises(defs.AttackFileError, match=fragment):
        defs.parse({"attacks": [bad]})


def test_duplicate_ids_are_rejected():
    with pytest.raises(defs.AttackFileError, match="duplicate id"):
        defs.parse({"attacks": [valid_entry(), valid_entry()]})


def test_every_problem_is_reported_not_just_the_first():
    with pytest.raises(defs.AttackFileError) as info:
        defs.parse({"attacks": [valid_entry(id="a", kind="bad"), valid_entry(id="b", prompt="")]})
    assert "a:" in str(info.value) and "b:" in str(info.value)


@pytest.mark.parametrize("raw", [{}, {"attacks": []}, {"attacks": "x"}, None])
def test_an_empty_or_wrong_file_is_rejected(raw):
    with pytest.raises(defs.AttackFileError):
        defs.parse(raw)


# ---------------------------------------------------------------- selection
def test_select_by_id(suite):
    assert [a.id for a in defs.select(suite, ids=["pii-ssn"])] == ["pii-ssn"]


def test_select_rejects_unknown_ids(suite):
    with pytest.raises(defs.AttackFileError, match="nope"):
        defs.select(suite, ids=["nope"])


def test_quick_and_ids_combine(suite):
    assert defs.select(suite, quick=True, ids=["pii-ssn", "extract-direct"]) == [
        a for a in suite if a.id == "pii-ssn"
    ]
