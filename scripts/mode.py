"""Switch the deployed demo between lockdown and demo mode, without tearing it down.

    python scripts/mode.py status    show the current mode
    python scripts/mode.py lock      only the fully defended profile is served
    python scripts/mode.py demo      all four profiles are served (weak ones included)

This runs `terraform apply`, so Terraform shows you its plan and asks before changing anything.
The change updates the existing runtime's settings and one IAM policy; nothing is destroyed.
Demo mode is the Terraform default, so a plain `terraform apply` returns to it. Lockdown only
lasts until the next apply without `-var lockdown=true`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra"
USAGE = __doc__


def terraform(*args: str) -> int:
    return subprocess.call(["terraform", f"-chdir={INFRA}", *args])


def current_mode() -> str:
    result = subprocess.run(
        ["terraform", f"-chdir={INFRA}", "output", "-raw", "lockdown"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return "unknown (is the stack deployed? run terraform apply first)"
    return "lockdown" if result.stdout.strip() == "true" else "demo (weak profiles ON)"


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in {"status", "lock", "demo"}:
        print(USAGE)
        return 2
    action = argv[0]
    if action == "status":
        print(f"Current mode: {current_mode()}")
        return 0

    # Terraform compares the build output with what is deployed; without it the plan would
    # try to change the code too.
    if not (ROOT / "dist" / "agent.zip").exists():
        print("dist/agent.zip is missing. Run `python scripts/build.py` first.", file=sys.stderr)
        return 1

    value = "true" if action == "lock" else "false"
    if terraform("apply", "-var", f"lockdown={value}") != 0:
        return 1
    print(f"\nMode is now: {current_mode()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
