"""Load and validate the attack definitions in attacks/attacks.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from common import constants

ATTACKS_FILE = Path(__file__).resolve().parent.parent / "attacks" / "attacks.yaml"

KINDS = ("attack", "control", "benign")

# oracle type -> (parameter that names the sink key or None, required parameters)
ORACLE_PARAMS: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "response_contains": (None, ("needles",)),
    "outbox_to": ("address", ("address",)),
    "refund_over": ("order_id", ("order_id",)),
    "refund_within": ("order_id", ("order_id",)),
}

# Tokens that may appear in the YAML, filled in from the shared constants.
TOKENS = {
    "{CANARY_SYSTEM_PROMPT}": constants.CANARY_SYSTEM_PROMPT,
    "{CANARY_CUSTOMER_NOTE}": constants.CANARY_CUSTOMER_NOTE,
    "{CANARY_SSN}": constants.CANARY_SSN,
    "{POISON_ADDRESS}": constants.POISON_ADDRESS,
    "{POISON_ADDRESS_SUBTLE}": constants.POISON_ADDRESS_SUBTLE,
}


class AttackFileError(ValueError):
    """The attack file is malformed. The message lists every problem found."""


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        for token, replacement in TOKENS.items():
            value = value.replace(token, replacement)
        return value
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


@dataclass(frozen=True)
class Attack:
    id: str
    title: str
    category: str
    kind: str
    prompt: str
    oracle: dict[str, Any]
    explanation: str = ""
    hypothesis: str = ""
    quick: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def sink_key(self) -> str | None:
        """The address or order id this attack writes under, or None if it uses no sink."""
        param = ORACLE_PARAMS[self.oracle["type"]][0]
        return None if param is None else str(self.oracle[param]).strip().lower()

    @property
    def harmful_if_success(self) -> bool:
        """True when a successful run is bad (attack, control); False for benign tasks."""
        return self.kind != "benign"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "kind": self.kind,
            "prompt": self.prompt,
            "explanation": self.explanation,
            "hypothesis": self.hypothesis,
            "quick": self.quick,
        }


def _check(entry: Any, index: int, seen: set[str]) -> tuple[str, list[str]]:
    """Return (label, problems) for one entry."""
    if not isinstance(entry, dict):
        return f"#{index}", ["entry is not a mapping"]

    label = str(entry.get("id", f"#{index}"))
    problems = [f"missing '{k}'" for k in ("id", "title", "category", "kind", "prompt", "oracle")
                if not entry.get(k)]

    if entry.get("id") in seen:
        problems.append("duplicate id")
    if entry.get("kind") and entry["kind"] not in KINDS:
        problems.append(f"kind must be one of {KINDS}")

    oracle = entry.get("oracle")
    if oracle:
        otype = oracle.get("type") if isinstance(oracle, dict) else None
        if otype not in ORACLE_PARAMS:
            problems.append(f"unknown oracle type {otype!r}")
        else:
            problems += [f"oracle {otype} needs '{p}'" for p in ORACLE_PARAMS[otype][1]
                         if not oracle.get(p)]
            if otype == "response_contains" and not isinstance(oracle.get("needles"), list):
                problems.append("needles must be a list")
    return label, problems


def parse(raw: dict[str, Any]) -> list[Attack]:
    entries = raw.get("attacks") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or not entries:
        raise AttackFileError("the file must contain a non-empty 'attacks' list")

    problems: list[str] = []
    attacks: list[Attack] = []
    seen: set[str] = set()

    for index, entry in enumerate(entries):
        entry = _expand(entry)
        label, entry_problems = _check(entry, index, seen)
        if entry_problems:
            problems += [f"{label}: {p}" for p in entry_problems]
            continue

        seen.add(entry["id"])
        attacks.append(
            Attack(
                id=entry["id"],
                title=entry["title"],
                category=entry["category"],
                kind=entry["kind"],
                prompt=" ".join(str(entry["prompt"]).split()),
                oracle=entry["oracle"],
                explanation=" ".join(str(entry.get("explanation", "")).split()),
                hypothesis=" ".join(str(entry.get("hypothesis", "")).split()),
                quick=bool(entry.get("quick", False)),
            )
        )

    if problems:
        raise AttackFileError("; ".join(problems))
    return attacks

def load(path: Path = ATTACKS_FILE) -> list[Attack]:
    return parse(yaml.safe_load(path.read_text(encoding="utf-8")))


def select(
    attacks: list[Attack], *, quick: bool = False, ids: list[str] | None = None
) -> list[Attack]:
    chosen = attacks
    if ids:
        unknown = sorted(set(ids) - {a.id for a in attacks})
        if unknown:
            raise AttackFileError(f"unknown attack id(s): {', '.join(unknown)}")
        chosen = [a for a in attacks if a.id in ids]
    if quick:
        chosen = [a for a in chosen if a.quick]
    return chosen
