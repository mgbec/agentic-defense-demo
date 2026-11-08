from common import sink


def test_write_then_read_by_key(sink_table):
    sink.write("outbox", "Alice@Example.com", to="alice@example.com", body="hi")
    rows = sink.read("outbox", "alice@example.com")
    assert len(rows) == 1
    assert rows[0]["body"] == "hi"


def test_keys_are_case_and_whitespace_insensitive(sink_table):
    sink.write("outbox", "  Bob@Example.COM ", body="x")
    assert len(sink.read("outbox", "bob@example.com")) == 1
    assert len(sink.read("outbox", " BOB@example.com")) == 1


def test_read_only_returns_the_requested_key(sink_table):
    sink.write("outbox", "a@x.test", body="1")
    sink.write("outbox", "b@x.test", body="2")
    assert [r["body"] for r in sink.read("outbox", "a@x.test")] == ["1"]


def test_prefix_query_does_not_confuse_similar_keys(sink_table):
    """'a@x.test' must not match 'a@x.test.evil' or 'a@x.testing'."""
    sink.write("outbox", "a@x.test", body="exact")
    sink.write("outbox", "a@x.testing", body="longer")
    assert [r["body"] for r in sink.read("outbox", "a@x.test")] == ["exact"]


def test_kinds_are_separate(sink_table):
    sink.write("outbox", "k", body="mail")
    sink.write("refund", "k", amount=5)
    assert len(sink.read("outbox", "k")) == 1
    assert len(sink.read("refund", "k")) == 1


def test_rows_come_back_in_time_order(sink_table):
    for n in range(3):
        sink.write("refund", "A-1001", amount=n + 1)
    assert [r["amount"] for r in sink.read("refund", "A-1001")] == [1, 2, 3]


def test_clear_removes_only_that_key(sink_table):
    sink.write("outbox", "a@x.test", body="1")
    sink.write("outbox", "a@x.test", body="2")
    sink.write("outbox", "b@x.test", body="3")
    assert sink.clear("outbox", "a@x.test") == 2
    assert sink.read("outbox", "a@x.test") == []
    assert len(sink.read("outbox", "b@x.test")) == 1


def test_clear_of_empty_key_is_harmless(sink_table):
    assert sink.clear("outbox", "nobody@x.test") == 0


def test_read_all_and_clear_all(sink_table):
    sink.write("outbox", "a@x.test", body="1")
    sink.write("refund", "A-1001", amount=5)
    assert len(sink.read_all("outbox")) == 1
    assert sink.clear_all() == 2
    assert sink.read_all("outbox") == [] and sink.read_all("refund") == []
