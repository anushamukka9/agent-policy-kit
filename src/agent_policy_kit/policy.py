"""Policy loading and validation.

A policy is a YAML document like this:

    name: support-bot-policy
    version: 1
    description: Read-only support bot with refund approval gates.
    default: deny
    rules:
      - name: read-own-ticket
        description: Agents may read tickets assigned to them.
        decision: allow
        match:
          tool: tickets.read
          args.assignee: "{agent}"

Rules are evaluated in order; the first matching rule wins. If no rule
matches, the policy ``default`` applies (``deny`` when omitted).

Match conditions are ANDed together. ``any_of`` holds a list of
condition groups that are ORed. See docs/policies.md for the full
schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DECISIONS = ("allow", "deny", "approve")

_CONDITION_KEYS = {"agent", "tool", "resource", "time", "any_of"}


class PolicyError(ValueError):
    """Raised when a policy file is malformed."""


@dataclass
class Rule:
    name: str
    decision: str
    description: str = ""
    match: dict[str, Any] = field(default_factory=dict)


@dataclass
class Policy:
    name: str
    version: int
    description: str = ""
    default: str = "deny"
    rules: list[Rule] = field(default_factory=list)


def _check_match_block(match: Any, where: str) -> None:
    if not isinstance(match, dict) or not match:
        raise PolicyError(f"{where}: 'match' must be a non-empty mapping")
    for key, value in match.items():
        if key == "any_of":
            if not isinstance(value, list) or not value:
                raise PolicyError(f"{where}: 'any_of' must be a non-empty list")
            for i, group in enumerate(value):
                _check_match_block(group, f"{where}.any_of[{i}]")
        elif key == "time":
            if not isinstance(value, dict) or set(value) != {"window"}:
                raise PolicyError(f"{where}: 'time' needs exactly a 'window' key")
            _check_window(value["window"], where)
        elif key in ("agent", "tool", "resource"):
            if not isinstance(value, str) or not value:
                raise PolicyError(f"{where}: '{key}' must be a non-empty string")
        elif key.startswith("args."):
            _check_value_spec(value, f"{where}.{key}")
        else:
            raise PolicyError(f"{where}: unknown match condition '{key}'")


def _check_window(window: Any, where: str) -> None:
    if not isinstance(window, str):
        raise PolicyError(f"{where}: time window must be a string like '09:00-17:00'")
    parts = window.split("-")
    if len(parts) != 2 or not all(_is_hhmm(p) for p in parts):
        raise PolicyError(f"{where}: bad time window '{window}', want 'HH:MM-HH:MM'")


def _is_hhmm(value: str) -> bool:
    try:
        hh, mm = value.split(":")
        return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59 and len(hh) == 2 and len(mm) == 2
    except (ValueError, AttributeError):
        return False


def _check_value_spec(spec: Any, where: str) -> None:
    if isinstance(spec, dict):
        allowed = {
            "eq",
            "ne",
            "gt",
            "gte",
            "lt",
            "lte",
            "in",
            "not_in",
            "regex",
            "glob",
            "exists",
            "contains",
        }
        unknown = set(spec) - allowed
        if unknown:
            raise PolicyError(f"{where}: unknown operators {sorted(unknown)}")
        if not spec:
            raise PolicyError(f"{where}: operator mapping must not be empty")
        if "in" in spec and not isinstance(spec["in"], list):
            raise PolicyError(f"{where}: 'in' needs a list")
        if "not_in" in spec and not isinstance(spec["not_in"], list):
            raise PolicyError(f"{where}: 'not_in' needs a list")
        if "exists" in spec and not isinstance(spec["exists"], bool):
            raise PolicyError(f"{where}: 'exists' needs true or false")
        for op in ("regex", "glob", "contains"):
            if op in spec and not isinstance(spec[op], str):
                raise PolicyError(f"{where}: '{op}' needs a string")
    elif not isinstance(spec, (str, int, float, bool)) or spec is None:
        raise PolicyError(f"{where}: value must be a string, number, bool, or operator mapping")


def load_policy(path: str | Path) -> Policy:
    """Load and validate a policy YAML file."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{path}: policy must be a mapping")
    name = data.get("name")
    if not name or not isinstance(name, str):
        raise PolicyError(f"{path}: policy needs a string 'name'")
    version = data.get("version", 1)
    if not isinstance(version, int):
        raise PolicyError(f"{path}: 'version' must be an integer")
    default = data.get("default", "deny")
    if default not in DECISIONS:
        raise PolicyError(f"{path}: 'default' must be one of {DECISIONS}")
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise PolicyError(f"{path}: 'rules' must be a list")
    rules = []
    seen = set()
    for i, raw in enumerate(raw_rules):
        where = f"{path} rule[{i}]"
        if not isinstance(raw, dict):
            raise PolicyError(f"{where}: rule must be a mapping")
        rname = raw.get("name")
        if not rname or not isinstance(rname, str):
            raise PolicyError(f"{where}: rule needs a string 'name'")
        if rname in seen:
            raise PolicyError(f"{where}: duplicate rule name '{rname}'")
        seen.add(rname)
        decision = raw.get("decision")
        if decision not in DECISIONS:
            raise PolicyError(f"{where}: 'decision' must be one of {DECISIONS}")
        match = raw.get("match")
        _check_match_block(match, where)
        rules.append(
            Rule(
                name=rname,
                decision=decision,
                description=str(raw.get("description", "")),
                match=match,
            )
        )
    return Policy(
        name=name,
        version=version,
        description=str(data.get("description", "")),
        default=default,
        rules=rules,
    )
