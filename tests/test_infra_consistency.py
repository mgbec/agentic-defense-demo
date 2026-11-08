"""Cross-checks that keep the Terraform, the Python code and the prompts from drifting apart.
These are plain text checks on infra/*.tf: they need no Terraform and no AWS."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import scripts.build as build
from common.constants import ALLOWED_EMAIL_DOMAIN, REFUND_LIMIT_DOLLARS
from defenses.prompts import system_prompt

INFRA = Path(__file__).resolve().parent.parent / "infra"
MAIN_TF = (INFRA / "main.tf").read_text()
VARIABLES_TF = (INFRA / "variables.tf").read_text()
RUNTIME_TF = (INFRA / "runtime.tf").read_text()


def tools_from_terraform() -> dict[str, str]:
    """tool name -> gateway target name, as declared in locals.tools."""
    block = MAIN_TF.split("tools = {", 1)[1].split("# Every tool is exposed", 1)[0]
    pairs = re.findall(r"^\s{4}(\w+) = \{\s*\n\s*target\s*=\s*\"(\w+)\"", block, re.MULTILINE)
    return dict(pairs)


def variable_default(name: str) -> str:
    match = re.search(rf'variable "{name}" \{{.*?default\s*=\s*"?([^"\n]+)"?\n', VARIABLES_TF, re.S)
    assert match, f"no default for variable {name}"
    return match.group(1).strip()


def test_terraform_declares_exactly_the_tools_the_build_packages():
    assert set(tools_from_terraform()) == set(build.TOOLS)


def test_every_tool_has_a_module_with_a_handler():
    root = Path(__file__).resolve().parent.parent / "tools"
    for tool in build.TOOLS:
        source = (root / tool / f"{tool}.py").read_text()
        assert re.search(r"^def handler\(", source, re.MULTILINE), tool


@pytest.mark.parametrize("hardened", [False, True])
def test_the_prompt_names_every_tool_exactly_as_the_gateway_exposes_it(hardened):
    prompt = system_prompt(hardened)
    for tool, target in tools_from_terraform().items():
        assert f"{target}___{tool}" in prompt, f"{target}___{tool} missing from the prompt"


def test_policy_limits_in_terraform_match_the_python_constants():
    assert variable_default("allowed_email_domain") == ALLOWED_EMAIL_DOMAIN
    assert int(variable_default("refund_limit_dollars")) == REFUND_LIMIT_DOLLARS


def test_refund_amount_is_an_integer_so_the_cedar_comparison_is_valid():
    """The policy uses `<=`, which Cedar allows on integers (Long) but not on decimals."""
    refund = MAIN_TF.split("issue_refund = {", 1)[1].split("}\n  }", 1)[0]
    assert re.search(r'name = "amount", type = "integer"', refund)


def test_the_runtime_python_version_matches_what_the_build_targets():
    version = build.PYTHON_VERSION.replace(".", "_")
    assert f'runtime     = "PYTHON_{version}"' in RUNTIME_TF


def test_the_runtime_gets_every_variable_the_agent_reads():
    for name in ("MODEL_ID", "GUARDRAIL_ID", "GUARDRAIL_VERSION", "GATEWAY_URL_OPEN",
                 "GATEWAY_URL_GUARDED"):
        assert re.search(rf"^\s+{name}\s+=", RUNTIME_TF, re.MULTILINE), name


def test_the_runtime_may_invoke_only_the_configured_model():
    assert "foundation-model/*" not in RUNTIME_TF
    assert "foundation-model/${var.model_id}" in RUNTIME_TF


def test_the_presenter_policy_is_narrow_and_covers_what_the_sink_code_calls():
    security = (INFRA / "security.tf").read_text()
    sink_code = (Path(__file__).resolve().parent.parent / "common" / "sink.py").read_text()
    # Whatever the runner/web page do against DynamoDB must be permitted, and nothing more.
    assert "_table().query(" in sink_code and "_table().delete_item(" in sink_code
    assert '["dynamodb:Query", "dynamodb:DeleteItem"]' in security
    assert '"*"' not in security.split('data "aws_iam_policy_document" "presenter"', 1)[1].split(
        'resource "aws_iam_policy"', 1)[0]


def test_the_budget_is_only_created_when_an_email_is_given():
    block = VARIABLES_TF.split('variable "budget_alert_email"', 1)[1]
    assert 'default     = ""' in block.split("}\n", 1)[0]
    assert 'count = var.budget_alert_email == "" ? 0 : 1' in (INFRA / "security.tf").read_text()


def test_lockdown_is_on_by_default_and_narrows_profiles_and_gateway_access():
    block = VARIABLES_TF.split('variable "lockdown"', 1)[1].split("}\n", 1)[0]
    assert "default     = true" in block
    assert 'ALLOWED_PROFILES    = var.lockdown ? "policy" :' in RUNTIME_TF
    # In lockdown the runtime role must not be able to reach the unguarded gateway.
    iam = RUNTIME_TF.split('sid     = "CallOnlyTheseGateways"', 1)[1].split("}\n", 1)[0]
    locked, unlocked = iam.split("] : [", 1)
    assert "gateway.open" not in locked and "gateway.open" in unlocked


def test_the_demo_mode_profile_list_matches_the_agent_profiles():
    from common.constants import PROFILES

    match = re.search(r'ALLOWED_PROFILES\s*=\s*var\.lockdown \? "policy" : "([^"]+)"', RUNTIME_TF)
    assert match and tuple(match.group(1).split(",")) == tuple(PROFILES)
