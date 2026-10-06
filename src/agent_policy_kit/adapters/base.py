"""Shared machinery for the framework adapters.

Both adapters (OpenAI Agents SDK, CrewAI) do the same three things:
turn a framework tool call into a plain action dict, run it through
``decide`` / ``check_action``, and translate the decision into
something the framework understands. The framework-specific modules
only handle the translation. Everything else lives here so the two
adapters cannot drift apart.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_policy_kit.approvals import (
    STATUS_APPROVED,
    ApprovalStore,
    request_approval,
)
from agent_policy_kit.audit import log_decision
from agent_policy_kit.compose import PolicySet, evaluate_set
from agent_policy_kit.engine import Decision, evaluate
from agent_policy_kit.policy import Policy


class PolicyDeniedError(Exception):
    """Raised when a policy denies a framework tool call.

    The winning :class:`Decision` is on ``self.decision``. Catch this
    around your agent run if you want to handle denials yourself
    instead of letting the framework abort.
    """

    def __init__(self, decision: Decision):
        self.decision = decision
        super().__init__(f"policy denied action: {decision.reason}")


class ApprovalRequiredError(Exception):
    """Raised when a policy routes a framework tool call to a human.

    The approval ticket is already queued in the adapter's
    ``ApprovalStore`` (when one is configured) and is on
    ``self.approval_request``. Resolve it with
    ``policy-kit approve <id>`` (or ``resolve_approval``), then retry
    the tool call.
    """

    def __init__(self, decision: Decision, approval_request: dict[str, Any] | None):
        self.decision = decision
        self.approval_request = approval_request
        ticket = approval_request["id"] if approval_request else "no ticket (no store configured)"
        super().__init__(f"policy requires human approval: {decision.reason} [{ticket}]")


@dataclass
class AdapterConfig:
    """One config object drives either adapter.

    Exactly one of ``policy`` / ``policy_set`` is required. Everything
    else is optional: without an approval store, ``approve`` decisions
    still raise, they just cannot queue a ticket. Without an audit
    path, decisions are not logged.
    """

    policy: Policy | None = None
    policy_set: PolicySet | None = None
    approval_store: ApprovalStore | None = None
    audit_path: str | Path | None = None
    agent_name: str = "agent"
    approval_ttl_minutes: int = 30
    on_decision: Callable[[Decision], None] | None = None

    def __post_init__(self) -> None:
        if (self.policy is None) == (self.policy_set is None):
            raise ValueError("AdapterConfig needs exactly one of 'policy' or 'policy_set'")


def build_action(
    agent: str,
    tool: str,
    args: Any,
    resource: str | None = None,
) -> dict[str, Any]:
    """Build the action dict the engine evaluates.

    ``args`` should be a mapping; anything else is tucked under
    ``{"args": ...}`` so the engine always sees a dict.
    """
    if not isinstance(args, dict):
        args = {"args": args}
    action: dict[str, Any] = {"agent": agent, "tool": tool, "args": args}
    if resource is not None:
        action["resource"] = resource
    return action


def _jsonable(value: Any) -> Any:
    """Make an action safe for the JSONL audit log."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, default=str))


def _evaluate(config: AdapterConfig, action: dict[str, Any]) -> Decision:
    now = datetime.now(timezone.utc)
    if config.policy_set is not None:
        return evaluate_set(config.policy_set, action, now)
    assert config.policy is not None  # checked in __post_init__
    return evaluate(config.policy, action, now)


def decide(config: AdapterConfig, action: dict[str, Any]) -> Decision:
    """Evaluate an action: audit it, fire the hook, return the decision.

    Never raises for the decision itself. Use :func:`check_action`
    when you want deny/approve to stop the tool call.
    """
    decision = _evaluate(config, action)
    if config.audit_path is not None:
        log_decision(config.audit_path, decision, _jsonable(action))
    if config.on_decision is not None:
        config.on_decision(decision)
    return decision


def _find_fresh_approval(store: ApprovalStore, action: dict[str, Any]) -> dict[str, Any] | None:
    """Find an approved, unexpired ticket for exactly this action.

    An approval covers the exact action it was requested for (same
    agent, tool, and args) until the ticket expires. A human approving
    ticket X and the agent retrying the same call is the whole point
    of the approve decision; without this, every retry would queue a
    new ticket forever.
    """
    now = datetime.now(timezone.utc)
    needle = _jsonable(action)
    for request in store.all():
        if request.get("status") != STATUS_APPROVED:
            continue
        if request.get("action") != needle:
            continue
        try:
            if datetime.fromisoformat(request["expires_at"]) <= now:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        return request
    return None


def _queue_approval(
    config: AdapterConfig, action: dict[str, Any], decision: Decision
) -> dict[str, Any] | None:
    """Queue an approval ticket. Returns the ticket, or None with no store."""
    if config.approval_store is None:
        return None
    return request_approval(
        config.approval_store,
        _jsonable(action),
        decision.policy,
        decision.rule or "(policy default)",
        ttl_minutes=config.approval_ttl_minutes,
    )


def check_action(config: AdapterConfig, action: dict[str, Any]) -> Decision:
    """Evaluate an action and enforce the decision.

    Returns the decision on ``allow``. Raises :class:`PolicyDeniedError`
    on ``deny``. On ``approve`` it first looks for a fresh approval
    grant: if a human already approved exactly this action and the
    ticket has not expired, the call goes through as ``allow``. (That
    is what makes "ask, approve, retry" work.) Otherwise it queues a
    ticket in the approval store (when configured) and raises
    :class:`ApprovalRequiredError`. Every evaluation is audited first,
    so the log shows the decision even when the tool call never runs.
    """
    decision = _evaluate(config, action)
    if decision.decision == "approve" and config.approval_store is not None:
        grant = _find_fresh_approval(config.approval_store, action)
        if grant is not None:
            decision = Decision(
                decision="allow",
                rule=decision.rule,
                reason=(f"approved by {grant.get('resolved_by')} (ticket {grant['id']}) -> allow"),
                policy=decision.policy,
                action=action,
            )
    approval_request = None
    approval_request_id = None
    if decision.decision == "approve":
        approval_request = _queue_approval(config, action, decision)
        if approval_request is not None:
            approval_request_id = approval_request["id"]
    if config.audit_path is not None:
        log_decision(
            config.audit_path,
            decision,
            _jsonable(action),
            approval_request_id=approval_request_id,
        )
    if config.on_decision is not None:
        config.on_decision(decision)
    if decision.decision == "allow":
        return decision
    if decision.decision == "deny":
        raise PolicyDeniedError(decision)
    raise ApprovalRequiredError(decision, approval_request)


__all__ = [
    "AdapterConfig",
    "ApprovalRequiredError",
    "PolicyDeniedError",
    "build_action",
    "check_action",
    "decide",
]
