"""Enforce policies inside the OpenAI Agents SDK.

Three integration points, pick the ones you need:

- :func:`wrap_function_tool`: attach a policy tool-guardrail to a
  ``FunctionTool``. The guardrail runs before every invocation and
  sees the real tool name and parsed arguments. This is the one most
  people want.
- :func:`policy_input_guardrail` / :func:`policy_output_guardrail`:
  agent-level guardrails that judge the prompt going in and the final
  answer going out (actions ``agent.input`` / ``agent.output``).
- :func:`PolicyRunHooks`: a ``RunHooks`` subclass that checks every
  tool call for every agent on the run. Blunt, no per-tool setup.
  Raising aborts the run, so prefer the tool guardrails when you want
  the model to see what happened.

Decision mapping for the tool guardrail: ``allow`` lets the call
through, ``deny`` raises the SDK's tripwire exception and halts the
run, ``approve`` rejects the call with a message telling the model a
human must approve first (the ticket is already queued in your
approval store, so the model can quote its id).

The ``agents`` package is imported lazily, so installing
agent-policy-kit never pulls in the SDK. You need it only to use this
module: ``pip install agent-policy-kit[openai]``.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from agent_policy_kit.adapters.base import (
    AdapterConfig,
    ApprovalRequiredError,
    PolicyDeniedError,
    build_action,
    check_action,
)
from agent_policy_kit.engine import Decision


def _require_agents():
    """Import the SDK or raise a helpful error."""
    try:
        import agents
        from agents import tool_guardrails
    except ImportError as exc:
        raise ImportError(
            "the OpenAI Agents SDK adapter needs the 'agents' package: "
            "pip install agent-policy-kit[openai]"
        ) from exc
    return agents, tool_guardrails


def _parse_tool_arguments(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"args": parsed}


def _info(decision: Decision, approval_request: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "decision": decision.decision,
        "policy": decision.policy,
        "rule": decision.rule,
        "reason": decision.reason,
        "approval_request_id": approval_request["id"] if approval_request else None,
    }


def policy_tool_guardrail(config: AdapterConfig, *, name: str | None = None):
    """Build a ``ToolInputGuardrail`` that enforces the policy.

    Attach it to a ``FunctionTool`` with :func:`wrap_function_tool`,
    or pass it straight into ``FunctionTool(tool_input_guardrails=[...])``.
    """
    _, tool_guardrails = _require_agents()

    async def _guardrail(data):
        tool_name = data.context.tool_name
        args = _parse_tool_arguments(data.context.tool_arguments)
        agent_name = getattr(data.agent, "name", None) or config.agent_name
        action = build_action(agent_name, tool_name, args)
        try:
            decision = check_action(config, action)
        except PolicyDeniedError as exc:
            return tool_guardrails.ToolGuardrailFunctionOutput.raise_exception(
                output_info=_info(exc.decision, None)
            )
        except ApprovalRequiredError as exc:
            ticket = exc.approval_request["id"] if exc.approval_request else "no ticket queued"
            message = (
                f"Policy '{exc.decision.policy}' requires human approval before "
                f"this tool call runs (ticket {ticket}). Ask the user to approve "
                f"it, then retry the call."
            )
            return tool_guardrails.ToolGuardrailFunctionOutput.reject_content(
                message, output_info=_info(exc.decision, exc.approval_request)
            )
        return tool_guardrails.ToolGuardrailFunctionOutput.allow(output_info=_info(decision, None))

    _guardrail.__name__ = name or "policy_tool_guardrail"
    return tool_guardrails.ToolInputGuardrail(guardrail_function=_guardrail, name=name)


def wrap_function_tool(tool, config: AdapterConfig, *, name: str | None = None):
    """Return a copy of ``tool`` with the policy guardrail attached.

    Name, description, and JSON schema are untouched, so the model
    sees the same tool. Any guardrails already on the tool are kept
    and run before the policy one.
    """
    _require_agents()  # fail fast with the helpful error
    guardrail = policy_tool_guardrail(config, name=name)
    existing = list(getattr(tool, "tool_input_guardrails", None) or [])
    return dataclasses.replace(tool, tool_input_guardrails=existing + [guardrail])


def _agent_text_guardrail(config: AdapterConfig, tool_name: str, arg_key: str, name: str | None):
    """Shared builder for the agent-level input/output guardrails."""
    agents, _ = _require_agents()
    from agents.guardrail import GuardrailFunctionOutput

    async def _guardrail(ctx, agent, content):
        text = content if isinstance(content, str) else json.dumps(content, default=str)[:4000]
        agent_name = getattr(agent, "name", None) or config.agent_name
        action = build_action(agent_name, tool_name, {arg_key: text})
        try:
            decision = check_action(config, action)
        except PolicyDeniedError as exc:
            return GuardrailFunctionOutput(
                output_info=_info(exc.decision, None), tripwire_triggered=True
            )
        except ApprovalRequiredError as exc:
            return GuardrailFunctionOutput(
                output_info=_info(exc.decision, exc.approval_request), tripwire_triggered=True
            )
        return GuardrailFunctionOutput(output_info=_info(decision, None), tripwire_triggered=False)

    _guardrail.__name__ = name or f"policy_{tool_name.replace('.', '_')}_guardrail"
    return _guardrail


def policy_input_guardrail(config: AdapterConfig, *, name: str | None = None):
    """Build an ``InputGuardrail`` judging the prompt (action ``agent.input``).

    ``deny`` and ``approve`` both trip the guardrail and halt the run;
    the ``output_info`` carries the decision, the rule, and the approval
    ticket id when there is one.
    """
    agents, _ = _require_agents()
    from agents.guardrail import InputGuardrail

    return InputGuardrail(
        guardrail_function=_agent_text_guardrail(config, "agent.input", "input", name),
        name=name,
    )


def policy_output_guardrail(config: AdapterConfig, *, name: str | None = None):
    """Build an ``OutputGuardrail`` judging the final answer (``agent.output``).

    Same tripwire mapping as :func:`policy_input_guardrail`.
    """
    agents, _ = _require_agents()
    from agents.guardrail import OutputGuardrail

    return OutputGuardrail(
        guardrail_function=_agent_text_guardrail(config, "agent.output", "output", name),
        name=name,
    )


def PolicyRunHooks(config: AdapterConfig):
    """Build a ``RunHooks`` instance that policy-checks every tool call.

    This is a factory (the SDK base class is imported lazily): it
    returns an instance of a ``RunHooks`` subclass. Pass it as
    ``hooks=`` to ``Runner.run``. ``deny`` and ``approve`` raise
    :class:`PolicyDeniedError` / :class:`ApprovalRequiredError` out of
    ``on_tool_start``, which aborts the run. Prefer
    :func:`wrap_function_tool` when you want the model to see the
    denial or the approval ticket instead of a stack trace.
    """
    agents, _ = _require_agents()

    class _PolicyRunHooks(agents.RunHooks):
        async def on_tool_start(self, context, agent, tool):
            args = _parse_tool_arguments(context.tool_arguments)
            agent_name = getattr(agent, "name", None) or config.agent_name
            action = build_action(agent_name, tool.name, args)
            check_action(config, action)

    return _PolicyRunHooks()


__all__ = [
    "PolicyRunHooks",
    "policy_input_guardrail",
    "policy_output_guardrail",
    "policy_tool_guardrail",
    "wrap_function_tool",
]
