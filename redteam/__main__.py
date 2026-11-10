"""Command line for the red-team suite.

    python -m redteam list
    python -m redteam run --quick                  # the small set, all four profiles
    python -m redteam run --profiles none,policy --repeats 5
    python -m redteam run --attacks refund-manager,pii-ssn
    python -m redteam show results/run-20261107-120000.json
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from common import sink
from common.constants import PROFILES
from redteam import attacks as attack_defs
from redteam import report
from redteam.config import ConfigError, load_config, point_sink_at
from redteam.invoker import RuntimeInvoker, make_client
from redteam.runner import Result, run_suite

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
CONFIRM_ABOVE = 150  # ask before starting a run larger than this many agent invocations


def parse_profiles(text: str) -> list[str]:
    chosen = list(PROFILES) if text == "all" else [p.strip() for p in text.split(",") if p.strip()]
    unknown = [p for p in chosen if p not in PROFILES]
    if unknown:
        raise SystemExit(f"unknown profile(s) {unknown}; choose from {list(PROFILES)} or 'all'")
    return chosen


def cmd_list(_: argparse.Namespace) -> int:
    for a in attack_defs.load():
        marker = "*" if a.quick else " "
        print(f"{marker} {a.id:<22}{a.kind:<9}{a.category:<18}{a.title}")
    print("\n* = included in --quick")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    ids = [i for i in args.attacks.split(",") if i] if args.attacks else None
    try:
        chosen = attack_defs.select(attack_defs.load(), quick=args.quick, ids=ids)
        config = load_config()
    except (attack_defs.AttackFileError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not chosen:
        print("error: no attacks selected", file=sys.stderr)
        return 2

    profiles = parse_profiles(args.profiles)
    total = len(chosen) * len(profiles) * args.repeats
    print(f"{len(chosen)} attacks x {len(profiles)} profiles x {args.repeats} repeats "
          f"= {total} agent runs against {config.runtime_arn.rsplit('/', 1)[-1]}")
    print("Each run makes several Bedrock calls. The cost is small but not zero.")

    if total > CONFIRM_ABOVE and not args.yes:
        if input("Continue? [y/N] ").strip().lower() != "y":
            return 1

    point_sink_at(config)
    try:
        invoker = RuntimeInvoker(make_client(config.region), config.runtime_arn)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    def progress(result: Result, done: int, count: int) -> None:
        mark = "ERR" if result.outcome is None else ("hit" if result.outcome else " . ")
        print(f"  [{done:>3}/{count}] {mark}  {result.attack_id:<22}{result.profile}", flush=True)

    started = time.time()
    results = run_suite(chosen, profiles, args.repeats, invoker, sink,
                        workers=args.workers, on_result=progress)
    summary = report.summarize(results, chosen, profiles, args.repeats)

    print()
    print(report.render_text(summary))
    print(f"\n({time.time() - started:.0f}s)")

    out = Path(args.out) if args.out else RESULTS_DIR / f"run-{time.strftime('%Y%m%d-%H%M%S')}.json"
    report.save(out, summary, results)
    print(f"saved {out}")
    return 1 if summary["errors"] else 0


def cmd_show(args: argparse.Namespace) -> int:
    print(report.render_text(report.load(Path(args.file))))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m redteam", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list the attacks").set_defaults(func=cmd_list)

    run = sub.add_parser("run", help="run attacks against the deployed agent")
    run.add_argument("--profiles", default="all", help="comma-separated, or 'all'")
    run.add_argument("--repeats", type=int, default=3, help="runs per attack per profile")
    run.add_argument("--quick", action="store_true", help="only the attacks marked quick")
    run.add_argument("--attacks", default="", help="comma-separated attack ids")
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--out", default="", help="where to save the JSON results")
    run.add_argument("--yes", action="store_true", help="do not ask before a large run")
    run.set_defaults(func=cmd_run)

    show = sub.add_parser("show", help="print the scoreboard from a saved run")
    show.add_argument("file")
    show.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
