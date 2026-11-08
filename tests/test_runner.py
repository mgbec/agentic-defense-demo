import threading
import time
from collections import defaultdict

import pytest

from common import sink
from common.constants import KIND_OUTBOX, KIND_REFUND
from redteam.attacks import Attack
from redteam.runner import run_suite

PROFILES = ["none", "policy"]


def attack(id, prompt, oracle, kind="attack"):
    return Attack(id=id, title=id, category="c", kind=kind, prompt=prompt, oracle=oracle)


MAIL = attack("mail", "send-mail", {"type": "outbox_to", "address": "m@x.test"})
REFUND = attack("refund", "do-refund", {"type": "refund_over", "order_id": "A-1001"})
LEAK = attack("leak", "leak-it", {"type": "response_contains", "needles": ["LEAK"]}, "control")
TASK = attack("task", "do-task", {"type": "response_contains", "needles": ["done"]}, "benign")


def vulnerable_unless_policy(prompt, profile):
    """A scripted stand-in for the deployed agent: only the 'policy' profile stops anything."""
    guarded = profile == "policy"
    if prompt == "send-mail" and not guarded:
        sink.write(KIND_OUTBOX, "m@x.test", to="m@x.test", body="x")
    if prompt == "do-refund" and not guarded:
        sink.write(KIND_REFUND, "A-1001", order_id="A-1001", amount=400)
    return {
        "response": "done" if prompt == "do-task" else ("LEAK" if prompt == "leak-it" else "ok"),
        "interventions": ["gateway_policy"] if guarded else [],
        "blocked_by": None,
        "trace": [], "tool_calls": [], "elapsed_ms": 5,
    }


def run(attacks, invoke, repeats=2, workers=4, **kwargs):
    return run_suite(attacks, PROFILES, repeats, invoke, sink, workers=workers, **kwargs)


# ---------------------------------------------------------------- basics
def test_every_combination_runs_the_requested_number_of_times(sink_table):
    results = run([MAIL, REFUND, TASK], vulnerable_unless_policy, repeats=3)
    assert len(results) == 3 * len(PROFILES) * 3


def test_results_come_back_in_a_stable_order(sink_table):
    results = run([MAIL, REFUND], vulnerable_unless_policy, repeats=2)
    keys = [(r.attack_id, r.profile, r.repeat) for r in results]
    assert keys == sorted(keys, key=lambda k: (["mail", "refund"].index(k[0]),
                                               PROFILES.index(k[1]), k[2]))


def test_outcomes_follow_what_the_agent_actually_did(sink_table):
    results = run([MAIL, REFUND, LEAK, TASK], vulnerable_unless_policy)
    outcome = {(r.attack_id, r.profile): r.outcome for r in results}
    # With no defense the harm happens; with policy the tools were stopped.
    assert outcome[("mail", "none")] is True and outcome[("mail", "policy")] is False
    assert outcome[("refund", "none")] is True and outcome[("refund", "policy")] is False
    # The leak happens in the reply text and this scripted agent leaks everywhere.
    assert outcome[("leak", "policy")] is True
    assert outcome[("task", "none")] is True and outcome[("task", "policy")] is True


def test_the_layer_that_intervened_is_recorded(sink_table):
    results = run([MAIL], vulnerable_unless_policy, repeats=1)
    stopped = next(r for r in results if r.profile == "policy")
    assert stopped.interventions == ["gateway_policy"]


def test_on_result_is_called_once_per_run_with_progress(sink_table):
    seen = []
    run([MAIL, TASK], vulnerable_unless_policy, repeats=2,
        on_result=lambda r, done, total: seen.append((done, total)))
    assert len(seen) == 8
    assert sorted(d for d, _ in seen) == list(range(1, 9))
    assert {t for _, t in seen} == {8}


# ---------------------------------------------------------------- isolation between runs
def test_rows_from_an_earlier_run_do_not_leak_into_the_next(sink_table):
    """The agent writes a row only the first time. The second run must NOT inherit it."""
    calls = []

    def once(prompt, profile):
        if not calls:
            sink.write(KIND_OUTBOX, "m@x.test", to="m@x.test", body="x")
        calls.append(1)
        return {"response": "ok"}

    results = run_suite([MAIL], ["none"], 2, once, sink, workers=1)
    assert [r.outcome for r in results] == [True, False]


# ---------------------------------------------------------------- failures are not "defenses held"
def test_an_invocation_that_raises_is_an_error_not_a_held_defense(sink_table):
    def boom(prompt, profile):
        raise TimeoutError("runtime unreachable")

    results = run([MAIL], boom, repeats=1)
    assert all(r.outcome is None for r in results)
    assert "TimeoutError" in results[0].error


def test_an_agent_that_reports_an_error_is_an_error_not_a_held_defense(sink_table):
    def crashed(prompt, profile):
        return {"response": "Sorry, something went wrong", "error": "RuntimeError: gateway 500"}

    results = run([MAIL, REFUND], crashed, repeats=1)
    assert all(r.outcome is None for r in results)
    assert "gateway 500" in results[0].error


def test_a_sink_failure_during_scoring_is_an_error(sink_table, monkeypatch):
    from redteam import oracles

    def broken(*args, **kwargs):
        raise RuntimeError("table unavailable")

    monkeypatch.setattr(oracles, "evaluate", broken)
    results = run([MAIL], vulnerable_unless_policy, repeats=1)
    assert all(r.outcome is None and "oracle" in r.error for r in results)


def test_one_failed_run_does_not_stop_the_others(sink_table):
    def flaky(prompt, profile):
        if profile == "none":
            raise RuntimeError("boom")
        return {"response": "ok"}

    results = run([TASK], flaky, repeats=2)
    assert sorted({r.outcome is None for r in results}) == [False, True]


# ---------------------------------------------------------------- concurrency
def test_runs_sharing_a_sink_key_never_overlap_but_others_do(sink_table):
    same_key_a = attack("a", "a", {"type": "outbox_to", "address": "shared@x.test"})
    same_key_b = attack("b", "b", {"type": "outbox_to", "address": "shared@x.test"})
    other = attack("c", "c", {"type": "outbox_to", "address": "other@x.test"})
    key_of = {"a": "shared", "b": "shared", "c": "other"}

    lock = threading.Lock()
    active: dict[str, int] = defaultdict(int)
    peak_per_key: dict[str, int] = defaultdict(int)
    peak_total = 0
    total_active = 0

    def slow(prompt, profile):
        nonlocal peak_total, total_active
        key = key_of[prompt]
        with lock:
            active[key] += 1
            total_active += 1
            peak_per_key[key] = max(peak_per_key[key], active[key])
            peak_total = max(peak_total, total_active)
        time.sleep(0.05)
        with lock:
            active[key] -= 1
            total_active -= 1
        return {"response": "ok"}

    run([same_key_a, same_key_b, other], slow, repeats=3, workers=8)
    assert peak_per_key["shared"] == 1, "same-key runs overlapped"
    assert peak_total >= 2, "different keys should have run in parallel"


def test_a_stop_request_ends_the_run_early(sink_table):
    stop = threading.Event()

    def invoke(prompt, profile):
        stop.set()
        return {"response": "ok"}

    results = run_suite([TASK], PROFILES, 10, invoke, sink, workers=1, should_stop=stop.is_set)
    assert 1 <= len(results) < 20


@pytest.mark.parametrize("workers", [0, -3])
def test_a_nonsensical_worker_count_still_runs(sink_table, workers):
    assert len(run([TASK], vulnerable_unless_policy, repeats=1, workers=workers)) == 2
