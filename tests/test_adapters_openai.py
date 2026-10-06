"""Tests for the OpenAI Agents SDK adapter, against the real SDK.

No network is used: the guardrail functions and hooks are invoked
directly with stub contexts, and wrapping never calls the model.
"""

import asyncio
import dataclasses
import json
import sys
from types import SimpleNamespace

import pytest

agents = pytest.importorskip("agents")

from agents import function_tool
from agents.guardrail import GuardrailFunctionOutput
from agents.tool_context import ToolContext
from agents.tool_guardrails import ToolInputGuardrailData

from agent_policy_kit import ApprovalRequiredError, ApprovalStore, PolicyDeniedError, load_policy
from agent_policy_kit.adapters import AdapterConfig
from agent_policy_kit.adapters.openai_agents import (
    PolicyRunHooks,
    policy_input_guardrail,
    policy_output_guardrail,
    policy_tool_guardrail,
    wrap_function_tool,
)

POLICY_YAML = """
name: adapter-test-policy
default: deny
rules:
  - name: allow-search
    decision: allow
    match:
      tool: docs.search
  - name: big-refund-needs-approval
    decision: approve
    match:
      tool: billing.refund
      args.amount: {gt: 100}
  - name: small-refund-ok
    decision: allow
    match:
      tool: billing.refund
      args.amount: {lte: 100}
"""


@pytest.fixture()
def policy(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(POLICY_YAML, encoding="utf-8")
    return load_policy(path)


@pytest.fixture()
def config(policy, tmp_path):
    return AdapterConfig(
        policy=policy,
        approval_store=ApprovalStore(tmp_path / "approvals.json"),
        audit_path=tmp_path / "audit.log",
        agent_name="billing-bot",
    )


def _tool_data(tool_name, arguments, agent_name="billing-bot"):
    ctx = ToolContext(
        context=None,
        tool_name=tool_name,
        tool_call_id="call-1",
        tool_arguments=json.dumps(arguments),
    )
    return ToolInputGuardrailData(context=ctx, agent=SimpleNamespace(name=agent_name))


def _run_guardrail(guardrail, data):
    return asyncio.run(guardrail.run(data))


@function_tool
def docs_search(query: str) -> str:
    """Search the docs."""
    return f"results for {query}"


def test_wrap_function_tool_preserves_identity(config):
    wrapped = wrap_function_tool(docs_search, config)
    assert wrapped.name == docs_search.name
    assert wrapped.description == docs_search.description
    assert wrapped.params_json_schema == docs_search.params_json_schema
    assert wrapped is not docs_search
    # the original is untouched
    assert not docs_search.tool_input_guardrails


def test_wrap_function_tool_keeps_existing_guardrails(config):
    existing = policy_tool_guardrail(config, name="first")
    tool = dataclasses.replace(docs_search, tool_input_guardrails=[existing])
    wrapped = wrap_function_tool(tool, config, name="second")
    assert len(wrapped.tool_input_guardrails) == 2


def test_tool_guardrail_allows(config):
    guardrail = policy_tool_guardrail(config)
    out = _run_guardrail(guardrail, _tool_data("docs.search", {"query": "pricing"}))
    assert out.behavior["type"] == "allow"
    assert out.output_info["decision"] == "allow"
    assert out.output_info["rule"] == "allow-search"


def test_tool_guardrail_denies_with_tripwire(config):
    guardrail = policy_tool_guardrail(config)
    out = _run_guardrail(guardrail, _tool_data("users.delete", {"id": "u-9"}))
    assert out.behavior["type"] == "raise_exception"
    assert out.output_info["decision"] == "deny"


def test_tool_guardrail_approve_rejects_with_ticket(config, tmp_path):
    guardrail = policy_tool_guardrail(config)
    out = _run_guardrail(guardrail, _tool_data("billing.refund", {"amount": 500}))
    assert out.behavior["type"] == "reject_content"
    ticket_id = out.output_info["approval_request_id"]
    assert ticket_id
    assert ticket_id in out.behavior["message"]
    assert ApprovalStore(tmp_path / "approvals.json").get(ticket_id)["status"] == "pending"


def test_tool_guardrail_uses_agent_name_from_sdk(config):
    seen = []
    config.on_decision = lambda d: seen.append(d.action["agent"])
    guardrail = policy_tool_guardrail(config)
    _run_guardrail(guardrail, _tool_data("docs.search", {}, agent_name="sdk-agent"))
    assert seen == ["sdk-agent"]


def test_tool_guardrail_handles_non_json_arguments(config):
    guardrail = policy_tool_guardrail(config)
    ctx = ToolContext(
        context=None, tool_name="docs.search", tool_call_id="c", tool_arguments="not-json{{{"
    )
    data = ToolInputGuardrailData(context=ctx, agent=SimpleNamespace(name="b"))
    out = asyncio.run(guardrail.run(data))
    assert out.behavior["type"] == "allow"  # unparseable args still match docs.search


def test_input_guardrail_trips_on_deny(config):
    guardrail = policy_input_guardrail(config)
    out = asyncio.run(
        guardrail.guardrail_function(SimpleNamespace(), SimpleNamespace(name="b"), "hello")
    )
    assert isinstance(out, GuardrailFunctionOutput)
    # "hello" matches no rule, default deny trips the guardrail
    assert out.tripwire_triggered is True
    assert out.output_info["decision"] == "deny"


def test_output_guardrail_passes_allowed_output(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text(
        "name: o\ndefault: deny\nrules:\n"
        "  - name: allow-output\n    decision: allow\n"
        "    match:\n      tool: agent.output\n",
        encoding="utf-8",
    )
    config = AdapterConfig(policy=load_policy(path))
    guardrail = policy_output_guardrail(config)
    out = asyncio.run(
        guardrail.guardrail_function(SimpleNamespace(), SimpleNamespace(name="b"), "done")
    )
    assert out.tripwire_triggered is False
    assert out.output_info["decision"] == "allow"


def test_run_hooks_allow_passes(config):
    hooks = PolicyRunHooks(config)
    ctx = SimpleNamespace(tool_arguments=json.dumps({"query": "x"}))
    tool = SimpleNamespace(name="docs.search")
    asyncio.run(hooks.on_tool_start(ctx, SimpleNamespace(name="b"), tool))  # no raise


def test_run_hooks_deny_raises(config):
    hooks = PolicyRunHooks(config)
    ctx = SimpleNamespace(tool_arguments=json.dumps({"id": "u-9"}))
    tool = SimpleNamespace(name="users.delete")
    with pytest.raises(PolicyDeniedError):
        asyncio.run(hooks.on_tool_start(ctx, SimpleNamespace(name="b"), tool))


def test_run_hooks_approve_raises_with_ticket(config):
    hooks = PolicyRunHooks(config)
    ctx = SimpleNamespace(tool_arguments=json.dumps({"amount": 900}))
    tool = SimpleNamespace(name="billing.refund")
    with pytest.raises(ApprovalRequiredError) as exc_info:
        asyncio.run(hooks.on_tool_start(ctx, SimpleNamespace(name="b"), tool))
    assert exc_info.value.approval_request is not None


def test_missing_sdk_raises_helpful_error(config, monkeypatch):
    monkeypatch.setitem(sys.modules, "agents", None)
    with pytest.raises(ImportError, match=r"pip install agent-policy-kit\[openai\]"):
        policy_tool_guardrail(config)
    with pytest.raises(ImportError, match=r"pip install agent-policy-kit\[openai\]"):
        wrap_function_tool(docs_search, config)
