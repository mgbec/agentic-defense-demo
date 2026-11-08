"""Defense profiles. Each profile adds one layer on top of the previous one, so the scoreboard
shows what each layer buys:

    none       no defenses
    guardrail  + Bedrock Guardrails on the user's message and on the final answer
    isolation  + retrieved documents are screened and fenced off as untrusted data
    policy     + tool calls go through a gateway that enforces Cedar policy
"""

from __future__ import annotations

from dataclasses import dataclass

from common.constants import PROFILES


@dataclass(frozen=True)
class DefenseConfig:
    profile: str
    guardrail: bool  # screen the user's message and the final answer
    isolation: bool  # screen retrieved documents and use the hardened system prompt
    gateway: str  # "open" (no policy engine) or "guarded" (Cedar policy engine, ENFORCE)


_BY_PROFILE = {
    "none": DefenseConfig("none", guardrail=False, isolation=False, gateway="open"),
    "guardrail": DefenseConfig("guardrail", guardrail=True, isolation=False, gateway="open"),
    "isolation": DefenseConfig("isolation", guardrail=True, isolation=True, gateway="open"),
    "policy": DefenseConfig("policy", guardrail=True, isolation=True, gateway="guarded"),
}

assert tuple(_BY_PROFILE) == tuple(PROFILES)


SAFE_PROFILE = "policy"


def allowed_profiles(env: dict[str, str]) -> tuple[str, ...]:
    """Profiles this deployment will run, from the comma-separated ALLOWED_PROFILES setting.

    Unset means all of them (local development). Terraform sets it to just the fully defended
    profile in lockdown. A setting that names no valid profile fails closed to that profile
    rather than open to all of them."""
    raw = env.get("ALLOWED_PROFILES")
    if raw is None:
        return tuple(PROFILES)
    named = {part.strip() for part in raw.split(",")}
    chosen = tuple(p for p in PROFILES if p in named)
    return chosen or (SAFE_PROFILE,)


def for_profile(profile: str) -> DefenseConfig:
    try:
        return _BY_PROFILE[profile]
    except KeyError:
        raise ValueError(f"Unknown profile {profile!r}; expected one of {list(PROFILES)}") from None
