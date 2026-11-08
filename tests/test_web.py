"""The local web server: request gate, validation, run lifecycle, and the real HTTP surface."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from common.constants import PROFILE_DESCRIPTIONS, PROFILE_LABELS, PROFILES
from redteam import attacks as attack_defs
from web import backend as web_backend
from web.backend import OfflineBackend, RunManager, list_results, load_result
from web.server import STATIC_DIR, Api, build_server

TOKEN = "t" * 32
HOST = "127.0.0.1:8000"


class EmptySink:
    def read(self, kind, key):
        return []

    def clear(self, kind, key):
        return 0


class FakeBackend:
    offline = False

    def __init__(self, delay: float = 0.0):
        self.sink = EmptySink()
        self.delay = delay
        self.calls: list[tuple[str, str]] = []

    def info(self):
        return {"offline": False, "region": "us-east-1", "runtime": "rt"}

    def invoke(self, prompt, profile):
        self.calls.append((prompt, profile))
        time.sleep(self.delay)
        return {"response": "ok <script>x</script>", "interventions": [], "trace": [],
                "tool_calls": []}

    def sink_snapshot(self):
        return {"outbox": [], "refunds": []}

    def clear_sink(self):
        return 3


def make_api(backend=None, tmp_path=None):
    return Api(backend or FakeBackend(), attack_defs.load(), TOKEN, {HOST},
               results_dir=tmp_path or web_backend.RESULTS_DIR)


def good_headers(**extra):
    return {"host": HOST, "x-demo-token": TOKEN, "content-type": "application/json", **extra}


def post(api, path, payload, **header_overrides):
    headers = {**good_headers(), **header_overrides}
    return api.handle("POST", path, headers, json.dumps(payload).encode())


def wait_idle(api, timeout=10.0):
    end = time.time() + timeout
    while api.runs.busy and time.time() < end:
        time.sleep(0.02)
    assert not api.runs.busy


# ---------------------------------------------------------------- request gate
def test_unknown_host_is_refused():
    status, _ = make_api().handle("GET", "/api/status", {"host": "evil.example"})
    assert status == 403


def test_foreign_origin_is_refused():
    status, _ = make_api().handle(
        "GET", "/api/status", {"host": HOST, "origin": "http://evil.example"}
    )
    assert status == 403


def test_same_origin_is_accepted():
    status, body = make_api().handle(
        "GET", "/api/status", {"host": HOST, "origin": f"http://{HOST}"}
    )
    assert status == 200 and body["busy"] is False


def test_post_without_token_is_refused():
    status, _ = post(make_api(), "/api/chat", {"prompt": "hi", "profile": "none"},
                     **{"x-demo-token": ""})
    assert status == 403


def test_post_with_wrong_token_is_refused():
    status, _ = post(make_api(), "/api/chat", {"prompt": "hi", "profile": "none"},
                     **{"x-demo-token": "wrong"})
    assert status == 403


def test_post_must_be_json():
    status, _ = post(make_api(), "/api/chat", {}, **{"content-type": "text/plain"})
    assert status == 415


def test_oversized_body_is_refused():
    api = make_api()
    status, _ = api.handle("POST", "/api/chat", good_headers(), b"{" + b" " * 70_000 + b"}")
    assert status == 413


def test_malformed_json_is_a_400():
    status, _ = make_api().handle("POST", "/api/chat", good_headers(), b"{not json")
    assert status == 400


def test_json_array_body_is_a_400():
    status, _ = make_api().handle("POST", "/api/chat", good_headers(), b"[1]")
    assert status == 400


def test_unknown_routes_and_methods():
    api = make_api()
    assert api.handle("GET", "/api/nope", {"host": HOST})[0] == 404
    assert api.handle("PUT", "/api/status", good_headers(), b"{}")[0] == 405


# ---------------------------------------------------------------- chat
def test_chat_passes_prompt_and_profile_to_backend():
    backend = FakeBackend()
    status, body = post(make_api(backend), "/api/chat", {"prompt": "hello", "profile": "policy"})
    assert status == 200 and body["response"].startswith("ok")
    assert backend.calls == [("hello", "policy")]


@pytest.mark.parametrize(
    "payload",
    [
        {"profile": "policy"},
        {"prompt": "   ", "profile": "policy"},
        {"prompt": 5, "profile": "policy"},
        {"prompt": "hi", "profile": "bogus"},
        {"prompt": "hi"},
        {"prompt": "x" * 4001, "profile": "policy"},
    ],
)
def test_chat_validation(payload):
    status, _ = post(make_api(), "/api/chat", payload)
    assert status == 400


def test_offline_chat_is_503():
    api = make_api(OfflineBackend())
    assert post(api, "/api/chat", {"prompt": "hi", "profile": "none"})[0] == 503


def test_offline_run_is_503():
    api = make_api(OfflineBackend())
    assert post(api, "/api/run", {"quick": True, "repeats": 1})[0] == 503


def test_backend_crash_is_a_502_without_traceback():
    class Boom(FakeBackend):
        def invoke(self, prompt, profile):
            raise RuntimeError("secret internal detail")

    status, body = post(make_api(Boom()), "/api/chat", {"prompt": "hi", "profile": "none"})
    assert status == 502 and "Traceback" not in json.dumps(body)


# ---------------------------------------------------------------- runs
def test_run_lifecycle(tmp_path):
    api = make_api(tmp_path=tmp_path)
    status, _ = post(api, "/api/run", {"quick": True, "profiles": ["none", "policy"], "repeats": 1})
    assert status == 202
    wait_idle(api)
    _, run = api.handle("GET", "/api/run", {"host": HOST})
    assert run["state"] == "done"
    assert run["done"] == run["total"] == 8 * 2
    assert run["summary"]["profiles"] == ["none", "policy"]
    assert (tmp_path / run["saved_as"]).is_file()


def test_run_validation(tmp_path):
    api = make_api(tmp_path=tmp_path)
    assert post(api, "/api/run", {"profiles": []})[0] == 400
    assert post(api, "/api/run", {"profiles": ["nope"]})[0] == 400
    assert post(api, "/api/run", {"repeats": 0})[0] == 400
    assert post(api, "/api/run", {"repeats": 21})[0] == 400
    assert post(api, "/api/run", {"repeats": "3"})[0] == 400
    assert post(api, "/api/run", {"repeats": True})[0] == 400
    assert post(api, "/api/run", {"attacks": ["no-such-attack"]})[0] == 400
    assert post(api, "/api/run", {"attacks": "pii-ssn"})[0] == 400
    # Over the per-job cap: all 14 attacks x 4 profiles x 20 repeats
    assert post(api, "/api/run", {"repeats": 20})[0] == 400


def test_chat_and_clear_are_refused_while_a_run_is_active(tmp_path):
    api = make_api(FakeBackend(delay=0.3), tmp_path)
    assert post(api, "/api/run", {"quick": True, "profiles": ["none"], "repeats": 1})[0] == 202
    try:
        assert post(api, "/api/chat", {"prompt": "hi", "profile": "none"})[0] == 409
        assert post(api, "/api/sink/clear", {})[0] == 409
        assert post(api, "/api/run", {"quick": True, "repeats": 1})[0] == 409
    finally:
        post(api, "/api/run/stop", {})
        wait_idle(api)


def test_stop_ends_the_run_early(tmp_path):
    api = make_api(FakeBackend(delay=0.2), tmp_path)
    post(api, "/api/run", {"quick": True, "profiles": ["none", "policy"], "repeats": 3})
    time.sleep(0.1)
    post(api, "/api/run/stop", {})
    wait_idle(api, timeout=20)
    _, run = api.handle("GET", "/api/run", {"host": HOST})
    assert run["state"] == "stopped"
    assert run["done"] < run["total"]


def test_clear_sink_when_idle():
    status, body = post(make_api(), "/api/sink/clear", {})
    assert (status, body) == (200, {"cleared": 3})


def test_run_manager_reports_failure(tmp_path):
    class Broken(FakeBackend):
        def __init__(self):
            super().__init__()
            self.sink = None  # the oracles will blow up on every run

    manager = RunManager(Broken(), attack_defs.load(), tmp_path)
    manager.start(quick=True, ids=None, profiles=["none"], repeats=1)
    for _ in range(300):
        if not manager.busy:
            break
        time.sleep(0.02)
    status = manager.status()
    # Every run fails individually; they are errors, never "defense held".
    assert status["state"] == "done"
    # Attacks that read the sink fail; the ones judged from the reply text still work.
    summary = status["summary"]
    assert summary["errors"] > 0
    for per_profile in summary["cells"].values():
        for cell in per_profile.values():
            if cell["errors"]:
                assert cell["valid"] == 0 and cell["outcomes"] == 0


# ---------------------------------------------------------------- library and results
def test_attacks_endpoint_lists_profiles_in_order():
    _, body = make_api().handle("GET", "/api/attacks", {"host": HOST})
    assert [p["id"] for p in body["profiles"]] == list(PROFILES)
    assert len(body["attacks"]) == len(attack_defs.load())


def test_profile_text_covers_every_profile():
    assert set(PROFILE_DESCRIPTIONS) == set(PROFILES) == set(PROFILE_LABELS)


def test_sample_results_load_and_are_labeled_as_sample():
    summary = load_result("sample")
    assert summary is not None
    assert "SAMPLE DATA" in summary["note"]
    assert summary["profiles"] == list(PROFILES)
    assert any(r["name"] == "sample" for r in list_results())


@pytest.mark.parametrize(
    "name",
    ["../server.py", "..\\x.json", "../../etc/passwd", "a/b.json", "/abs.json", "x.txt", ""],
)
def test_result_names_cannot_escape_the_results_dir(name, tmp_path):
    assert load_result(name, tmp_path) is None


def test_results_endpoint_404s_for_unknown_run(tmp_path):
    api = make_api(tmp_path=tmp_path)
    status, _ = api.handle("GET", "/api/results/run-nope.json", {"host": HOST})
    assert status == 404


# ---------------------------------------------------------------- real HTTP
@pytest.fixture
def live_server(tmp_path):
    server, api = build_server(FakeBackend(), port=0, token=TOKEN)
    api.results_dir = tmp_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def fetch(host, path, *, method="GET", headers=None, body=None):
    request = urllib.request.Request(f"http://{host}{path}", method=method, data=body,
                                     headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def test_server_binds_to_loopback_only():
    server, _ = build_server(FakeBackend(), port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_index_has_token_csp_and_no_placeholder(live_server):
    status, headers, body = fetch(live_server, "/")
    page = body.decode()
    assert status == 200
    assert f'content="{TOKEN}"' in page and "__DEMO_TOKEN__" not in page
    csp = headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_static_page_has_no_inline_script_or_style():
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in page and "style=" not in page


def test_front_end_never_uses_innerhtml():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert banned not in js


def test_served_assets(live_server):
    for path, kind in (("/app.js", "javascript"), ("/style.css", "css")):
        status, headers, _ = fetch(live_server, path)
        assert status == 200 and kind in headers["Content-Type"]


def test_bad_host_header_is_refused_over_http(live_server):
    status, _, _ = fetch(live_server, "/", headers={"Host": "evil.example"})
    assert status == 403


def test_chat_over_http_needs_the_token(live_server):
    body = json.dumps({"prompt": "hi", "profile": "none"}).encode()
    headers = {"Content-Type": "application/json"}
    assert fetch(live_server, "/api/chat", method="POST", headers=headers, body=body)[0] == 403
    ok = fetch(live_server, "/api/chat", method="POST",
               headers={**headers, "X-Demo-Token": TOKEN}, body=body)
    assert ok[0] == 200
    assert json.loads(ok[2])["response"].startswith("ok")


def test_unknown_static_path_is_404(live_server):
    assert fetch(live_server, "/web/server.py")[0] == 404
    assert fetch(live_server, "/../pyproject.toml")[0] == 404
