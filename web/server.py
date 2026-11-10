"""A small local web server for the demo. Standard library only.

This process holds the presenter's AWS credentials and can spend money, so it is built defensively:
  * it binds to 127.0.0.1 only, never to a public interface;
  * every request's Host header must name this server, which defeats DNS-rebinding attacks;
  * every state-changing request must carry a secret token that is generated at start-up and only
    ever given to the page this server serves, so another website open in the same browser cannot
    drive it;
  * request bodies are size-limited and strictly validated;
  * the page is served with a strict Content-Security-Policy.
"""

from __future__ import annotations

import argparse
import hmac
import json
import secrets
import sys
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from common.constants import PROFILE_DESCRIPTIONS, PROFILE_LABELS, PROFILES
from redteam import attacks as attack_defs
from redteam.config import ConfigError, load_config
from web.backend import (
    RESULTS_DIR,
    AwsBackend,
    BackendUnavailable,
    OfflineBackend,
    RunError,
    RunManager,
    list_results,
    load_result,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
TOKEN_PLACEHOLDER = "__DEMO_TOKEN__"
MAX_BODY_BYTES = 64 * 1024
MAX_PROMPT_CHARS = 4000

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


class Api:
    """Maps (method, path, headers, body) to (status, json). No sockets in here."""

    def __init__(
        self,
        backend: Any,
        attacks: list[attack_defs.Attack],
        token: str,
        allowed_hosts: set[str],
        results_dir: Path = RESULTS_DIR,
    ) -> None:
        self.backend = backend
        self.attacks = attacks
        self.token = token
        self.allowed_hosts = allowed_hosts
        self.results_dir = results_dir
        self.runs = RunManager(backend, attacks, results_dir)

    # ---- request gate ----------------------------------------------------------------------
    def _check_origin(self, headers: dict[str, str]) -> tuple[int, dict] | None:
        if headers.get("host", "") not in self.allowed_hosts:
            return 403, {"error": "unexpected Host header"}
        origin = headers.get("origin")
        if origin is not None and urlsplit(origin).netloc not in self.allowed_hosts:
            return 403, {"error": "cross-origin requests are not allowed"}
        return None

    def _check_post(self, headers: dict[str, str], body: bytes) -> tuple[int, dict] | None:
        supplied = headers.get("x-demo-token", "")
        if not hmac.compare_digest(supplied.encode(), self.token.encode()):
            return 403, {"error": "missing or wrong token"}
        if not headers.get("content-type", "").startswith("application/json"):
            return 415, {"error": "send application/json"}
        if len(body) > MAX_BODY_BYTES:
            return 413, {"error": "request too large"}
        return None

    # ---- dispatch --------------------------------------------------------------------------
    def handle(
        self, method: str, path: str, headers: dict[str, str], body: bytes = b""
    ) -> tuple[int, dict]:
        refused = self._check_origin(headers)
        if refused:
            return refused
        if method == "POST":
            refused = self._check_post(headers, body)
            if refused:
                return refused

        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            return 400, {"error": "body is not valid JSON"}
        if not isinstance(payload, dict):
            return 400, {"error": "body must be a JSON object"}

        try:
            return self._route(method, path, payload)
        except RunError as exc:
            return exc.status, {"error": str(exc)}
        except BackendUnavailable as exc:
            return 503, {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - never leak a traceback to the browser
            print(f"error handling {method} {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 502, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    def _route(self, method: str, path: str, payload: dict) -> tuple[int, dict]:
        if method == "GET":
            if path == "/api/status":
                return 200, {**self.backend.info(), "busy": self.runs.busy}
            if path == "/api/attacks":
                return 200, {
                    "attacks": [a.public() for a in self.attacks],
                    "profiles": [
                        {
                            "id": p,
                            "label": PROFILE_LABELS[p],
                            "description": PROFILE_DESCRIPTIONS[p],
                        }
                        for p in PROFILES
                    ],
                }
            if path == "/api/sink":
                return 200, self.backend.sink_snapshot()
            if path == "/api/run":
                return 200, self.runs.status()
            if path == "/api/results":
                return 200, {"results": list_results(self.results_dir)}
            if path.startswith("/api/results/"):
                summary = load_result(path.removeprefix("/api/results/"), self.results_dir)
                return (200, {"summary": summary}) if summary else (404, {"error": "no such run"})
            return 404, {"error": "not found"}

        if method == "POST":
            if path == "/api/chat":
                return self._chat(payload)
            if path == "/api/run":
                return self._start_run(payload)
            if path == "/api/run/stop":
                self.runs.stop()
                return 200, {"stopping": True}
            if path == "/api/sink/clear":
                if self.runs.busy:
                    return 409, {"error": "A scoreboard run is in progress."}
                return 200, {"cleared": self.backend.clear_sink()}
            return 404, {"error": "not found"}

        return 405, {"error": "method not allowed"}

    def _chat(self, payload: dict) -> tuple[int, dict]:
        prompt, profile = payload.get("prompt"), payload.get("profile")
        if not isinstance(prompt, str) or not prompt.strip():
            return 400, {"error": "prompt must be a non-empty string"}
        if len(prompt) > MAX_PROMPT_CHARS:
            return 400, {"error": f"prompt is longer than {MAX_PROMPT_CHARS} characters"}
        if profile not in PROFILES:
            return 400, {"error": f"profile must be one of {list(PROFILES)}"}
        if self.runs.busy:
            # A manual message could write rows that a running scoreboard is about to read.
            return 409, {"error": "A scoreboard run is in progress. Wait for it to finish."}
        return 200, self.backend.invoke(prompt, profile)

    def _start_run(self, payload: dict) -> tuple[int, dict]:
        profiles = payload.get("profiles", list(PROFILES))
        ids = payload.get("attacks") or None
        repeats = payload.get("repeats", 3)
        repeats_ok = isinstance(repeats, int) and not isinstance(repeats, bool)
        if not isinstance(profiles, list) or not repeats_ok:
            return 400, {"error": "profiles must be a list and repeats an integer"}
        ids_ok = isinstance(ids, list) and all(isinstance(i, str) for i in ids)
        if ids is not None and not ids_ok:
            return 400, {"error": "attacks must be a list of ids"}
        try:
            self.runs.start(
                quick=bool(payload.get("quick", False)), ids=ids, profiles=profiles, repeats=repeats
            )
        except attack_defs.AttackFileError as exc:
            return 400, {"error": str(exc)}
        return 202, self.runs.status()


def make_handler(api: Api) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "demo"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            return  # the console is for the presenter, not request noise

        def _headers(self) -> dict[str, str]:
            return {k.lower(): v for k, v in self.headers.items()}

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, default=_jsonable).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path in STATIC_FILES:
                refused = api._check_origin(self._headers())
                if refused:
                    return self._send_json(*refused)
                name, content_type = STATIC_FILES[path]
                text = (STATIC_DIR / name).read_text(encoding="utf-8")
                if name == "index.html":
                    text = text.replace(TOKEN_PLACEHOLDER, api.token)
                return self._send(200, text.encode("utf-8"), content_type)
            status, payload = api.handle("GET", path, self._headers())
            self._send_json(status, payload)

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._send_json(400, {"error": "bad Content-Length"})
            if length > MAX_BODY_BYTES:
                return self._send_json(413, {"error": "request too large"})
            body = self.rfile.read(length) if length else b""
            status, payload = api.handle("POST", urlsplit(self.path).path, self._headers(), body)
            self._send_json(status, payload)

        def do_PUT(self) -> None:  # noqa: N802
            self._send_json(405, {"error": "method not allowed"})

        do_DELETE = do_PATCH = do_PUT  # noqa: N815

    return Handler


def build_server(backend: Any, port: int = 8000, token: str | None = None) -> tuple[
    ThreadingHTTPServer, Api
]:
    """Create the server (not yet serving). Port 0 picks a free port."""
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    actual = server.server_address[1]
    api = Api(
        backend,
        attack_defs.load(),
        token or secrets.token_urlsafe(32),
        allowed_hosts={f"127.0.0.1:{actual}", f"localhost:{actual}"},
    )
    server.RequestHandlerClass = make_handler(api)
    server.daemon_threads = True
    return server, api


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local web page for the defense-in-depth demo")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--offline", action="store_true",
        help="no AWS: only show saved scoreboards (good for rehearsing)",
    )
    args = parser.parse_args(argv)

    if args.offline:
        backend: Any = OfflineBackend()
    else:
        try:
            backend = AwsBackend(load_config())
        except ConfigError as exc:
            print(f"error: {exc}\nTo look around without AWS, run with --offline.", file=sys.stderr)
            return 2

    server, api = build_server(backend, args.port)
    url = f"http://localhost:{server.server_address[1]}/"
    mode = "OFFLINE (saved scoreboards only)" if args.offline else "connected to AWS"
    print(f"Demo page: {url}   [{mode}]\nPress Ctrl+C to stop.")
    try:
        # Serve on the main thread, with a short poll interval. On Windows an untimed
        # wait in the main thread cannot be interrupted, so Ctrl+C would do nothing.
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        api.runs.stop()  # ask any scoreboard run in progress to wind down
        server.server_close()
        print("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
