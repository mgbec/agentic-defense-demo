"""What the web page talks to: the deployed agent, the sink table, and background scoreboard runs.

Two backends: AwsBackend (the real thing) and OfflineBackend (no AWS at all; the page can only show
saved scoreboards, which is handy for rehearsing).
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from common import sink
from common.constants import KIND_OUTBOX, KIND_REFUND, PROFILES
from redteam import report
from redteam.attacks import Attack, select
from redteam.config import DemoConfig, point_sink_at
from redteam.invoker import RuntimeInvoker, make_client
from redteam.runner import Result, run_suite

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
SAMPLE_FILE = Path(__file__).resolve().parent / "sample_results.json"
SAMPLE_NAME = "sample"

MAX_RUNS_PER_JOB = 600  # a guard against an accidental, expensive click
SINK_ROWS_SHOWN = 25
_RESULT_NAME = re.compile(r"^[\w.-]+\.json$")


class BackendUnavailable(RuntimeError):
    """The requested action needs a deployment, and this backend has none."""


class RunError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class AwsBackend:
    offline = False

    def __init__(self, config: DemoConfig) -> None:
        self.config = config
        point_sink_at(config)
        self._invoker = RuntimeInvoker(make_client(config.region), config.runtime_arn)
        self.sink = sink

    def info(self) -> dict[str, Any]:
        return {
            "offline": False,
            "region": self.config.region,
            "runtime": self.config.runtime_arn.rsplit("/", 1)[-1],
        }

    def invoke(self, prompt: str, profile: str) -> dict[str, Any]:
        return self._invoker(prompt, profile)

    def sink_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        def newest(kind: str) -> list[dict[str, Any]]:
            rows = sorted(self.sink.read_all(kind), key=lambda r: r.get("ts", 0), reverse=True)
            return rows[:SINK_ROWS_SHOWN]

        return {"outbox": newest(KIND_OUTBOX), "refunds": newest(KIND_REFUND)}

    def clear_sink(self) -> int:
        return self.sink.clear_all()


class OfflineBackend:
    offline = True
    sink = None

    def info(self) -> dict[str, Any]:
        return {"offline": True, "region": None, "runtime": None}

    def invoke(self, prompt: str, profile: str) -> dict[str, Any]:
        raise BackendUnavailable("Offline mode: nothing is deployed, so there is no agent to ask.")

    def sink_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return {"outbox": [], "refunds": []}

    def clear_sink(self) -> int:
        return 0


class RunManager:
    """Runs one scoreboard job at a time in a background thread and reports live progress."""

    def __init__(self, backend: Any, attacks: list[Attack], results_dir: Path = RESULTS_DIR):
        self._backend = backend
        self._attacks = attacks
        self._results_dir = results_dir
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reset()

    def _reset(self) -> None:
        self._state = "idle"  # idle | running | done | stopped | failed
        self._results: list[Result] = []
        self._chosen: list[Attack] = []
        self._profiles: list[str] = []
        self._repeats = 0
        self._total = 0
        self._error: str | None = None
        self._saved_as: str | None = None

    @property
    def busy(self) -> bool:
        return self._state == "running"

    def start(
        self, *, quick: bool, ids: list[str] | None, profiles: list[str], repeats: int
    ) -> None:
        if self._backend.offline:
            raise RunError("Offline mode: there is nothing to run attacks against.", 503)
        chosen = select(self._attacks, quick=quick, ids=ids or None)
        if not chosen:
            raise RunError("No attacks selected.")
        if not profiles or any(p not in PROFILES for p in profiles):
            raise RunError(f"profiles must be a non-empty subset of {list(PROFILES)}")
        if not 1 <= repeats <= 20:
            raise RunError("repeats must be between 1 and 20.")
        total = len(chosen) * len(profiles) * repeats
        if total > MAX_RUNS_PER_JOB:
            raise RunError(
                f"That would be {total} agent runs; the limit per job is {MAX_RUNS_PER_JOB}."
            )

        with self._lock:
            if self.busy:
                raise RunError("A scoreboard run is already in progress.", 409)
            self._reset()
            self._state, self._chosen, self._profiles = "running", chosen, profiles
            self._repeats, self._total = repeats, total
            self._stop.clear()
            self._thread = threading.Thread(target=self._work, daemon=True)
            self._thread.start()

    def _work(self) -> None:
        def on_result(result: Result, done: int, total: int) -> None:
            with self._lock:
                self._results.append(result)

        try:
            results = run_suite(
                self._chosen, self._profiles, self._repeats, self._backend.invoke,
                self._backend.sink, workers=8, on_result=on_result,
                should_stop=self._stop.is_set,
            )
            summary = report.summarize(results, self._chosen, self._profiles, self._repeats)
            name = f"run-{time.strftime('%Y%m%d-%H%M%S')}.json"
            report.save(self._results_dir / name, summary, results)
            with self._lock:
                self._saved_as = name
                self._state = "stopped" if self._stop.is_set() else "done"
        except Exception as exc:  # noqa: BLE001 - surface any failure to the page
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
                self._state = "failed"

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            results = list(self._results)
            payload: dict[str, Any] = {
                "state": self._state,
                "done": len(results),
                "total": self._total,
                "error": self._error,
                "saved_as": self._saved_as,
                "summary": None,
            }
            chosen, profiles, repeats = self._chosen, self._profiles, self._repeats
        if results:
            payload["summary"] = report.summarize(results, chosen, profiles, repeats)
        return payload


# ---------------------------------------------------------------------------------------------
# Saved scoreboards
# ---------------------------------------------------------------------------------------------
def list_results(results_dir: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    entries = []
    if SAMPLE_FILE.exists():
        entries.append({"name": SAMPLE_NAME, "label": "Sample data (not a measurement)"})
    if results_dir.exists():
        for path in sorted(results_dir.glob("run-*.json"), reverse=True)[:20]:
            entries.append({"name": path.name, "label": path.stem.removeprefix("run-")})
    return entries


def load_result(name: str, results_dir: Path = RESULTS_DIR) -> dict[str, Any] | None:
    """The summary for a saved run, or None. Only names from the listing are accepted."""
    if name == SAMPLE_NAME:
        return report.load(SAMPLE_FILE) if SAMPLE_FILE.exists() else None
    if not _RESULT_NAME.match(name):
        return None
    path = results_dir / name
    if path.parent.resolve() != results_dir.resolve() or not path.is_file():
        return None
    return report.load(path)
