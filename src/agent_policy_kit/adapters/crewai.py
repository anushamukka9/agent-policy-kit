"""Enforce policies inside CrewAI.

CrewAI has no tool middleware hook, so the adapter wraps each tool's
``_run`` in place: the tool keeps its type, name, description, and
argument schema (the model sees the same tool), but every execution
goes through the policy first.

- :func:`wrap_tool`: wrap one tool. Returns the same tool object.
- :func:`apply_policy_to_crew`: wrap every tool of every agent in a
  crew, using each agent's role as the agent name. One call and the
  whole crew is guarded.

Decision mapping: ``allow`` runs the tool, ``deny`` raises
:class:`PolicyDeniedError`, ``approve`` queues a ticket in your
approval store and raises :class:`ApprovalRequiredError`. Both
exceptions propagate out of ``tool.run()``, so the crew run stops at
the violation instead of sailing past it.

``crewai`` is imported lazily; you only need it to use this module:
``pip install agent-policy-kit[crewai]``.
"""

from __future__ import annotations

from typing import Any

from agent_policy_kit.adapters.base import (
    AdapterConfig,
    build_action,
    check_action,
)


def _require_crewai():
    """Import crewai or raise a helpful error."""
    try:
        import crewai  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "the CrewAI adapter needs the 'crewai' package: pip install agent-policy-kit[crewai]"
        ) from exc


def _coerce_args(args: tuple, kwargs: dict) -> dict[str, Any]:
    if kwargs:
        return dict(kwargs)
    if len(args) == 1 and isinstance(args[0], dict):
        return dict(args[0])
    if args:
        return {"args": list(args)}
    return {}


def wrap_tool(tool, config: AdapterConfig, *, agent_name: str | None = None):
    """Wrap ``tool._run`` with a policy check, in place.

    Returns the same tool object, so it can stay in the agent's tool
    list untouched. Wrapping twice is a no-op.
    """
    _require_crewai()  # fail fast with the helpful error
    # A marker attribute keeps apply_policy_to_crew idempotent and a
    # tool shared by two agents checked once per call. (CrewAI tools
    # are pydantic models and unhashable, so no set of tools.)
    if getattr(tool, "_policy_checked", False):
        return tool
    original_run = tool._run
    name = agent_name or config.agent_name
    tool_name = getattr(tool, "name", "unknown-tool")

    def _checked_run(*args: Any, **kwargs: Any):
        action = build_action(name, tool_name, _coerce_args(args, kwargs))
        check_action(config, action)
        return original_run(*args, **kwargs)

    tool._run = _checked_run
    tool._policy_checked = True
    return tool


def apply_policy_to_crew(crew, config: AdapterConfig):
    """Wrap every tool of every agent in ``crew``.

    Each agent's ``role`` becomes the policy's agent name, so one
    policy can treat the researcher and the deploy bot differently.
    Returns the same crew object.
    """
    _require_crewai()  # fail fast with the helpful error
    for agent in getattr(crew, "agents", None) or []:
        role = getattr(agent, "role", None) or config.agent_name
        for tool in getattr(agent, "tools", None) or []:
            wrap_tool(tool, config, agent_name=role)
    return crew


__all__ = ["apply_policy_to_crew", "wrap_tool"]
