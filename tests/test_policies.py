"""Run the real Cedar policy files through a real Cedar engine (cedarpy).

infra/policies/*.cedar.tftpl are rendered with the same placeholders Terraform fills in, then
asked to authorize concrete tool calls. This tests the policy LOGIC. It does not test AgentCore's
own enforcement, which only a deployment can.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

import cedarpy
import pytest

from common.constants import ALLOWED_EMAIL_DOMAIN, REFUND_LIMIT_DOLLARS

POLICY_DIR = Path(__file__).resolve().parent.parent / "infra" / "policies"

AGENT_ROLE = "arn:aws:sts::123456789012:assumed-role/agentic-defense-agent-runtime"
GATEWAY = "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/guarded-abc123"

ACTIONS = {
    "search_kb": "kb___search_kb",
    "lookup_customer": "crm___lookup_customer",
    "send_email": "mail___send_email",
    "issue_refund": "billing___issue_refund",
}

# policy file -> the tool it governs (mirrors local.policies in infra/gateway.tf)
POLICY_FILES = {
    "permit_search_kb": "search_kb",
    "permit_lookup_customer": "lookup_customer",
    "permit_send_email_internal": "send_email",
    "permit_refund_up_to_limit": "issue_refund",
}


def render(policy: str, tool: str) -> str:
    values = {
        "principal": f'AgentCore::IamEntity::"{AGENT_ROLE}"',
        "resource": f'AgentCore::Gateway::"{GATEWAY}"',
        "action": f'AgentCore::Action::"{ACTIONS[tool]}"',
        "email_domain": ALLOWED_EMAIL_DOMAIN,
        "refund_limit": REFUND_LIMIT_DOLLARS,
    }
    # Terraform's templatefile and Python's string.Template both use ${name}.
    return Template((POLICY_DIR / f"{policy}.cedar.tftpl").read_text()).substitute(values)


ALL_POLICIES = "\n".join(render(p, t) for p, t in POLICY_FILES.items())


def allowed(action: str, tool_input: dict, principal: str = AGENT_ROLE) -> bool:
    request = {
        "principal": f'AgentCore::IamEntity::"{principal}"',
        "action": f'AgentCore::Action::"{action}"',
        "resource": f'AgentCore::Gateway::"{GATEWAY}"',
        "context": {"input": tool_input},
    }
    return cedarpy.is_authorized(request, ALL_POLICIES, []).allowed


def email(to: str) -> bool:
    return allowed(ACTIONS["send_email"], {"to": to, "subject": "s", "body": "b"})


def refund(amount: int) -> bool:
    return allowed(ACTIONS["issue_refund"], {"order_id": "A-1001", "amount": amount, "reason": "r"})


# ---------------------------------------------------------------- the files themselves
@pytest.mark.parametrize("policy, tool", POLICY_FILES.items())
def test_every_policy_file_is_valid_cedar(policy, tool):
    # format_policies raises if the text does not parse.
    assert cedarpy.format_policies(render(policy, tool)).strip()


def test_every_policy_file_in_the_directory_is_accounted_for():
    on_disk = {p.name.removesuffix(".cedar.tftpl") for p in POLICY_DIR.glob("*.cedar.tftpl")}
    assert on_disk == set(POLICY_FILES)


# ---------------------------------------------------------------- default deny
def test_read_tools_are_allowed_for_the_agent():
    assert allowed(ACTIONS["search_kb"], {"query": "returns"})
    assert allowed(ACTIONS["lookup_customer"], {"customer_id": "C-1001"})


def test_other_callers_are_denied_everything():
    other = "arn:aws:sts::123456789012:assumed-role/some-other-role"
    assert not allowed(ACTIONS["search_kb"], {"query": "x"}, principal=other)
    assert not allowed(ACTIONS["issue_refund"], {"amount": 1}, principal=other)


def test_a_tool_nobody_wrote_a_policy_for_is_denied():
    assert not allowed("admin___delete_everything", {})


# ---------------------------------------------------------------- refunds
@pytest.mark.parametrize("amount", [1, 25, REFUND_LIMIT_DOLLARS])
def test_refunds_up_to_the_limit_are_allowed(amount):
    assert refund(amount)


@pytest.mark.parametrize("amount", [REFUND_LIMIT_DOLLARS + 1, 120, 250, 400, 100000])
def test_refunds_over_the_limit_are_denied(amount):
    assert not refund(amount)


# ---------------------------------------------------------------- email recipients
def test_internal_addresses_are_allowed():
    assert email(f"jordan.rivera@{ALLOWED_EMAIL_DOMAIN}")


@pytest.mark.parametrize(
    "recipient",
    [
        "audit-relay@evil.test",
        "jordan.new@evil.test",
        "",
        "no-at-sign",
        # Tricks that defeat a naive suffix match:
        f"evil@bad.test,ok@{ALLOWED_EMAIL_DOMAIN}",
        f"evil@bad.test, ok@{ALLOWED_EMAIL_DOMAIN}",
        f"evil@bad.test;ok@{ALLOWED_EMAIL_DOMAIN}",
        f"evil@bad.test ok@{ALLOWED_EMAIL_DOMAIN}",
        f"evil@bad.test@{ALLOWED_EMAIL_DOMAIN}",
        f"ok@{ALLOWED_EMAIL_DOMAIN}.evil.test",
        f"ok@sub.{ALLOWED_EMAIL_DOMAIN}",
        f"ok@not{ALLOWED_EMAIL_DOMAIN}".replace("@not", "@not-"),
    ],
)
def test_external_and_tricky_recipients_are_denied(recipient):
    assert not email(recipient)


def test_a_naive_suffix_only_policy_would_have_been_fooled():
    """Documents why the policy has extra conditions: the bare suffix rule lets a list through."""
    naive = (
        "permit (principal, action, resource) "
        f'when {{ context.input.to like "*@{ALLOWED_EMAIL_DOMAIN}" }};'
    )
    request = {
        "principal": f'AgentCore::IamEntity::"{AGENT_ROLE}"',
        "action": f'AgentCore::Action::"{ACTIONS["send_email"]}"',
        "resource": f'AgentCore::Gateway::"{GATEWAY}"',
        "context": {"input": {"to": f"evil@bad.test,ok@{ALLOWED_EMAIL_DOMAIN}"}},
    }
    assert cedarpy.is_authorized(request, naive, []).allowed
    assert not email(f"evil@bad.test,ok@{ALLOWED_EMAIL_DOMAIN}")
