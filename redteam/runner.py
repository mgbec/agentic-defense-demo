"""Run attacks against the deployed agent, in parallel where that is safe.

Every (attack, profile, repeat) is one independent agent run in a fresh session. Runs that touch
the same sink key (the same recipient address or order id) are serialized behind a lock, because
they read and clear the same rows. Everything else runs concurrently.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from typing import Any

from redteam import oracles
from redteam.attacks import Attack

# (prompt, profile) -> the agent's result dict
Invoke = Callable[[str, str], dict[str, Any]]

MAX_RESPONSE_CHARS = 600


@dataclass
class Result:
    attack_id: str
    profile: str
    repeat: int
    # True/False once the oracle has judged the run. None means the run itself failed (an
    # infrastructure or agent error), which says nothing about whether a defense held.
    outcome: bool | None
    error: str | None = None
    blocked_by: str | None = None
    interventions: list[str] = field(default_factory=list)
    response: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_one(
    attack: Attack, profile: str, repeat: int, invoke: Invoke, sink: oracles.SinkReader
) -> Result:
    started = time.perf_counter()

    def failed(message: str) -> Result:
        return Result(
            attack.id, profile, repeat, outcome=None, error=message,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )

    try:
        oracles.reset(attack, sink)
        raw = invoke(attack.prompt, profile)
    except Exception as exc:  # noqa: BLE001 - any failure is reported, never counted as "held"
        return failed(f"{type(exc).__name__}: {exc}")

    # The agent reports its own failures in-band. An agent that crashed produced no evidence
    # about the defenses, so it must not be scored as a successful defense.
    if raw.get("error"):
        return failed(f"agent error: {raw['error']}")

    response = str(raw.get("response", ""))
    try:
        outcome = oracles.evaluate(attack, response, sink)
    except Exception as exc:  # noqa: BLE001
        return failed(f"oracle {type(exc).__name__}: {exc}")

    return Result(
        attack_id=attack.id,
        profile=profile,
        repeat=repeat,
        outcome=outcome,
        blocked_by=raw.get("blocked_by"),
        interventions=list(raw.get("interventions", [])),
        response=response[:MAX_RESPONSE_CHARS],
        tool_calls=list(raw.get("tool_calls", [])),
        trace=list(raw.get("trace", [])),
        elapsed_ms=int(raw.get("elapsed_ms") or round((time.perf_counter() - started) * 1000)),
    )


def run_suite(
    attacks: list[Attack],
    profiles: list[str],
    repeats: int,
    invoke: Invoke,
    sink: oracles.SinkReader,
    *,
    workers: int = 8,
    on_result: Callable[[Result, int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Result]:
    """Run every attack against every profile ``repeats`` times and return the results in a
    stable order (attack, then profile, then repeat)."""
    # Interleave repeats so runs of the same attack are spread out, not queued back to back.
    tasks = [
        (attack, profile, repeat)
        for repeat in range(repeats)
        for attack in attacks
        for profile in profiles
    ]
    total = len(tasks)
    locks = {a.sink_key: threading.Lock() for a in attacks if a.sink_key is not None}

    def work(task: tuple[Attack, str, int]) -> Result | None:
        attack, profile, repeat = task
        if should_stop and should_stop():
            return None
        guard = locks[attack.sink_key] if attack.sink_key in locks else nullcontext()
        with guard:
            return _run_one(attack, profile, repeat, invoke, sink)

    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(work, task) for task in tasks]
        for future in as_completed(futures):
            result = future.result()
            if result is None:
                continue
            results.append(result)
            if on_result:
                on_result(result, len(results), total)

    attack_order = {a.id: i for i, a in enumerate(attacks)}
    profile_order = {p: i for i, p in enumerate(profiles)}
    results.sort(key=lambda r: (attack_order[r.attack_id], profile_order[r.profile], r.repeat))
    return results
