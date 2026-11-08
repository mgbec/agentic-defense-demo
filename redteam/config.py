"""Where the deployed demo lives: runtime ARN, region and sink table.

Taken from environment variables if set, otherwise from `terraform output` in infra/.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from common import sink

INFRA_DIR = Path(__file__).resolve().parent.parent / "infra"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class DemoConfig:
    region: str
    runtime_arn: str
    sink_table: str


def _from_env(env: Mapping[str, str]) -> DemoConfig | None:
    region = env.get("DEMO_REGION") or env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION")
    arn, table = env.get("DEMO_RUNTIME_ARN"), env.get("DEMO_SINK_TABLE")
    if region and arn and table:
        return DemoConfig(region, arn, table)
    return None


def _from_terraform(directory: Path) -> DemoConfig:
    try:
        completed = subprocess.run(
            ["terraform", f"-chdir={directory}", "output", "-json"],
            capture_output=True, text=True, check=True, timeout=60,
        )
    except FileNotFoundError:
        raise ConfigError("terraform is not installed, and DEMO_* variables are not set") from None
    except subprocess.CalledProcessError as exc:
        raise ConfigError(f"terraform output failed: {exc.stderr.strip()[:300]}") from None

    outputs = json.loads(completed.stdout or "{}")
    try:
        return DemoConfig(
            region=outputs["region"]["value"],
            runtime_arn=outputs["agent_runtime_arn"]["value"],
            sink_table=outputs["sink_table"]["value"],
        )
    except KeyError:
        raise ConfigError(
            "Nothing is deployed yet (terraform has no outputs). Deploy first; see the README."
        ) from None


def load_config(
    env: Mapping[str, str] | None = None, terraform_dir: Path = INFRA_DIR
) -> DemoConfig:
    env = os.environ if env is None else env
    return _from_env(env) or _from_terraform(terraform_dir)


def point_sink_at(config: DemoConfig) -> None:
    """Make common.sink read and write the deployed table."""
    os.environ["SINK_TABLE"] = config.sink_table
    os.environ["AWS_REGION"] = config.region
    sink.reset_cache()
