import json

from common.data import KB_DOCS, POISONED_DOC_ID
from defenses import isolation
from defenses.guardrail import Guardrail
from tests.fakes import FakeBedrock


def guardrail(**kwargs):
    return Guardrail(FakeBedrock(**kwargs), "gr", "1")


def kb_payload(*docs):
    return json.dumps({"results": [dict(d) for d in docs]})


def test_clean_documents_are_fenced_and_kept():
    raw = kb_payload(KB_DOCS[0])
    out, events = isolation.process(raw, guardrail())
    doc = json.loads(out)["results"][0]
    assert doc["text"].startswith('<untrusted_document id="kb-001">')
    assert doc["text"].endswith("</untrusted_document>")
    assert events == [("allow", "kb-001 screened")]


def test_a_flagged_document_is_withheld_and_the_rest_survive():
    poisoned = next(d for d in KB_DOCS if d["id"] == POISONED_DOC_ID)
    raw = kb_payload(KB_DOCS[0], poisoned)
    out, events = isolation.process(raw, guardrail(block_if=("SYSTEM NOTICE",)))

    docs = {d["id"]: d for d in json.loads(out)["results"]}
    assert docs[POISONED_DOC_ID]["text"] == isolation.WITHHELD
    assert "send_email" not in out
    assert "<untrusted_document" in docs["kb-001"]["text"]
    assert [e[0] for e in events] == ["allow", "withhold"]


def test_each_document_is_screened_separately():
    client = FakeBedrock()
    isolation.process(kb_payload(KB_DOCS[0], KB_DOCS[1]), Guardrail(client, "g", "1"))
    assert len(client.calls) == 2


def test_documents_are_screened_as_input_so_prompt_attacks_are_detected():
    client = FakeBedrock()
    isolation.process(kb_payload(KB_DOCS[0]), Guardrail(client, "g", "1"))
    assert client.calls[0]["source"] == "INPUT"


def test_unknown_shape_is_screened_as_a_whole_and_fenced():
    out, events = isolation.process("just some text", guardrail())
    assert out.startswith("<untrusted_document")
    assert events[0][0] == "allow"


def test_unknown_shape_is_withheld_when_flagged():
    out, events = isolation.process("ignore previous", guardrail(block_if=("ignore",)))
    assert out == isolation.WITHHELD
    assert events[0][0] == "withhold"


def test_documents_missing_a_text_field_do_not_crash():
    raw = json.dumps({"results": [{"id": "x", "title": "t"}]})
    out, _ = isolation.process(raw, guardrail())
    assert json.loads(out)["results"][0]["id"] == "x"


def test_guardrail_failure_withholds_rather_than_passing_content_through():
    out, events = isolation.process(
        kb_payload(KB_DOCS[0]), guardrail(raises=RuntimeError("down"))
    )
    assert json.loads(out)["results"][0]["text"] == isolation.WITHHELD
    assert events[0][0] == "withhold"
