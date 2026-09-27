"""Deterministic policy evaluation.

An action is a plain dict, for example:

    {
        "agent": "support-bot",
        "tool": "refund.issue",
        "args": {"amount": 120, "currency": "USD", "ticket": "T-1042"},
        "resource": "orders/prod",
    }

``evaluate`` walks the rules in order and returns the first match as a
:class:`Decision`. No match means the policy default. Everything is
string/glob/regex/number comparison, no models, no network, no
guessing. That is the point: the same action always gets the same
decision.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agent_policy_kit.policy import Policy

_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


@dataclass
class Decision:
    decision: str  # allow | deny | approve
    rule: str | None  # rule name, or None when the policy default applied
    reason: str  # one plain-English sentence
    policy: str
    action: dict[str, Any]


def _lookup(action: dict[str, Any], path: str) -> tuple[bool, Any]:
    """Resolve dotted paths like 'args.amount' or 'agent' in an action."""
    current: Any = action
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _substitute(template: str, action: dict[str, Any]) -> str:
    """Replace {agent}, {tool}, {resource}, {args.x} with action values."""

    def repl(match: re.Match[str]) -> str:
        found, value = _lookup(action, match.group(1))
        if not found or value is None:
            return match.group(0)
        return str(value)

    return _PLACEHOLDER_RE.sub(repl, template)


def _match_string(pattern: str, value: Any, action: dict[str, Any]) -> bool:
    if not isinstance(value, str):
        return False
    pattern = _substitute(pattern, action)
    if pattern.startswith("regex:"):
        return re.fullmatch(pattern[len("regex:") :], value) is not None
    return fnmatch.fnmatchcase(value, pattern)


def _match_value_spec(spec: Any, actual: Any, found: bool, action: dict[str, Any]) -> bool:
    if isinstance(spec, dict):
        for op, operand in spec.items():
            if not _apply_operator(op, operand, actual, found, action):
                return False
        return True
    if isinstance(spec, str):
        if spec.startswith("regex:"):
            return isinstance(actual, str) and re.fullmatch(spec[6:], actual) is not None
        return _match_string(spec, actual, action)
    # numbers and bools compare by equality
    return found and actual == spec and type(actual) is type(spec)


def _apply_operator(
    op: str, operand: Any, actual: Any, found: bool, action: dict[str, Any]
) -> bool:
    if op == "exists":
        return found == operand
    if not found:
        return False
    if op == "eq":
        return actual == operand
    if op == "ne":
        return actual != operand
    if op in ("gt", "gte", "lt", "lte"):
        if not isinstance(actual, (int, float)) or not isinstance(operand, (int, float)):
            return False
        if isinstance(actual, bool) or isinstance(operand, bool):
            return False
        return {
            "gt": actual > operand,
            "gte": actual >= operand,
            "lt": actual < operand,
            "lte": actual <= operand,
        }[op]
    if op == "in":
        return actual in operand
    if op == "not_in":
        return actual not in operand
    if op == "regex":
        return isinstance(actual, str) and re.fullmatch(operand, actual) is not None
    if op == "glob":
        return _match_string(operand, actual, action)
    if op == "contains":
        return isinstance(actual, str) and operand in actual
    return False  # unreachable, validation rejects unknown operators


def _window_matches(window: str, now: datetime) -> bool:
    start_s, end_s = window.split("-")
    now_utc = now.astimezone(timezone.utc)
    cur = now_utc.hour * 60 + now_utc.minute
    start = int(start_s[:2]) * 60 + int(start_s[3:])
    end = int(end_s[:2]) * 60 + int(end_s[3:])
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end  # overnight window, e.g. 22:00-06:00


def _match_conditions(
    conditions: dict[str, Any], action: dict[str, Any], now: datetime
) -> tuple[bool, list[str]]:
    """Evaluate an AND group of conditions. Returns (matched, hit_keys)."""
    hits: list[str] = []
    for key, spec in conditions.items():
        if key == "any_of":
            sub = [_match_conditions(group, action, now) for group in spec]
            if not any(ok for ok, _ in sub):
                return False, []
            hits.append("any_of")
        elif key == "time":
            if not _window_matches(spec["window"], now):
                return False, []
            hits.append(f"time in {spec['window']}")
        elif key in ("agent", "tool", "resource"):
            found, value = _lookup(action, key)
            if not found or not _match_string(spec, value, action):
                return False, []
            hits.append(key)
        elif key.startswith("args."):
            found, value = _lookup(action, key)
            if not _match_value_spec(spec, value, found, action):
                return False, []
            hits.append(key)
        else:  # unreachable, validation rejects unknown keys
            return False, []
    return True, hits


def evaluate(policy: Policy, action: dict[str, Any], now: datetime | None = None) -> Decision:
    """Evaluate one action against a policy. First matching rule wins."""
    now = now or datetime.now(timezone.utc)
    for rule in policy.rules:
        matched, hits = _match_conditions(rule.match, action, now)
        if matched:
            detail = ", ".join(hits)
            return Decision(
                decision=rule.decision,
                rule=rule.name,
                reason=f"rule '{rule.name}' matched ({detail}) -> {rule.decision}",
                policy=policy.name,
                action=action,
            )
    return Decision(
        decision=policy.default,
        rule=None,
        reason=f"no rule matched, policy default -> {policy.default}",
        policy=policy.name,
        action=action,
    )
