import json

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

from common.data import KB_DOCS, POISONED_DOC_ID
from defenses.guardrail import Guardrail
from defenses.hooks import (
    MAX_TOOL_CALLS,
    IsolationHook,
    ToolTraceHook,
    result_text,
    short_name,
)
from defenses.isolation import WITHHELD
from defenses.trace import Trace
from tests.fakes import FakeBedrock


def before(name="mail___send_email", args=None, tool_id="t1"):
    return BeforeToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use={"toolUseId": tool_id, "name": name, "input": args or {}},
        invocation_state={},
    )


def after(name, text, status="success", tool_id="t1", cancel_message=None):
    return AfterToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use={"toolUseId": tool_id, "name": name, "input": {}},
        invocation_state={},
        result={"toolUseId": tool_id, "status": status, "content": [{"text": text}]},
        cancel_message=cancel_message,
    )


def test_short_name_strips_the_gateway_target_prefix():
    assert short_name("mail___send_email") == "send_email"
    assert short_name("plain") == "plain"


def test_result_text_joins_text_and_json_blocks():
    result = {"content": [{"text": "a"}, {"json": {"k": 1}}]}
    assert result_text(result) == 'a\n{"k": 1}'


# ---------------------------------------------------------------- ToolTraceHook
def test_tool_calls_are_recorded_with_their_outcome():
    trace = Trace()
    hook = ToolTraceHook(trace)
    hook.before(before(args={"to": "a@b.test"}))
    hook.after(after("mail___send_email", '{"status": "queued"}'))
    call = trace.tool_calls[0]
    assert call["name"] == "send_email"
    assert call["args"] == {"to": "a@b.test"}
    assert call["status"] == "success"
    assert "queued" in call["summary"]


def test_a_gateway_denial_is_recognised_and_traced():
    trace = Trace()
    hook = ToolTraceHook(trace)
    hook.before(before())
    denial = after("mail___send_email", "AccessDenied: not authorized", status="error")
    hook.after(denial)
    assert trace.tool_calls[0]["status"] == "denied"
    assert trace.events[-1]["layer"] == "gateway_policy"
    assert trace.events[-1]["decision"] == "deny"


def test_an_ordinary_tool_error_is_not_mistaken_for_a_policy_denial():
    trace = Trace()
    hook = ToolTraceHook(trace)
    hook.before(before())
    hook.after(after("mail___send_email", "Unknown order Z-9", status="error"))
    assert trace.tool_calls[0]["status"] == "error"
    assert not any(e["layer"] == "gateway_policy" for e in trace.events)


def test_tool_call_budget_cancels_runaway_loops():
    trace = Trace()
    hook = ToolTraceHook(trace)
    last = None
    for n in range(MAX_TOOL_CALLS + 1):
        last = before(tool_id=f"t{n}")
        hook.before(last)
    assert last.cancel_tool
    assert any(e["layer"] == "tool_budget" for e in trace.events)


def test_calls_within_budget_are_not_cancelled():
    hook = ToolTraceHook(Trace())
    event = before()
    hook.before(event)
    assert not event.cancel_tool


def test_cancelled_calls_are_marked():
    trace = Trace()
    hook = ToolTraceHook(trace)
    hook.before(before())
    hook.after(after("x", "cancelled", status="error", cancel_message="budget"))
    assert trace.tool_calls[0]["status"] == "cancelled"


# ---------------------------------------------------------------- IsolationHook
def kb_result(*docs):
    return json.dumps({"results": [dict(d) for d in docs]})


def test_poisoned_search_results_are_rewritten_before_the_model_sees_them():
    trace = Trace()
    hook = IsolationHook(Guardrail(FakeBedrock(block_if=("SYSTEM NOTICE",)), "g", "1"), trace)
    poisoned = next(d for d in KB_DOCS if d["id"] == POISONED_DOC_ID)

    event = after("kb___search_kb", kb_result(poisoned))
    hook.after(event)

    text = result_text(event.result)
    assert WITHHELD in text
    assert "send_email" not in text
    assert any(e["layer"] == "tool_result_screen" and e["decision"] == "withhold"
               for e in trace.events)


def test_other_tools_are_left_alone():
    trace = Trace()
    client = FakeBedrock()
    hook = IsolationHook(Guardrail(client, "g", "1"), trace)
    event = after("crm___lookup_customer", '{"found": true}')
    hook.after(event)
    assert result_text(event.result) == '{"found": true}'
    assert client.calls == []


def test_failed_searches_are_not_screened():
    client = FakeBedrock()
    hook = IsolationHook(Guardrail(client, "g", "1"), Trace())
    hook.after(after("kb___search_kb", "boom", status="error"))
    assert client.calls == []
