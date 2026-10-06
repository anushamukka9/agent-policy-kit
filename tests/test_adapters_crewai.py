"""Tests for the CrewAI adapter, against the real crewai package.

No LLM calls are made: tools are executed directly through
``tool.run()``, and crews are constructed but never kicked off.
"""

import sys

import pytest

crewai = pytest.importorskip("crewai")

from crewai import Agent, Crew
from crewai.tools import BaseTool

from agent_policy_kit import ApprovalRequiredError, ApprovalStore, PolicyDeniedError, load_policy
from agent_policy_kit.adapters import AdapterConfig
from agent_policy_kit.adapters.crewai import apply_policy_to_crew, wrap_tool

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


class SearchTool(BaseTool):
    name: str = "docs.search"
    description: str = "Search the docs."

    def _run(self, query: str) -> str:
        return f"results for {query}"


class RefundTool(BaseTool):
    name: str = "billing.refund"
    description: str = "Refund a customer."

    def _run(self, customer: str, amount: float) -> str:
        return f"refunded {amount} to {customer}"


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


def test_wrap_tool_allows_and_delegates(config):
    tool = wrap_tool(SearchTool(), config)
    assert tool.run(query="pricing") == "results for pricing"
    assert tool.name == "docs.search"  # identity preserved
    assert type(tool).__name__ == "SearchTool"


def test_wrap_tool_denies_and_never_runs_delegate(config):
    calls = []

    class DeleteTool(BaseTool):
        name: str = "users.delete"
        description: str = "Delete a user."

        def _run(self, id: str) -> str:
            calls.append(id)
            return "deleted"

    tool = wrap_tool(DeleteTool(), config)
    with pytest.raises(PolicyDeniedError):
        tool.run(id="u-9")
    assert calls == []


def test_wrap_tool_approve_queues_ticket(config, tmp_path):
    tool = wrap_tool(RefundTool(), config)
    with pytest.raises(ApprovalRequiredError) as exc_info:
        tool.run(customer="c-1", amount=500)
    ticket = exc_info.value.approval_request
    assert ticket["rule"] == "big-refund-needs-approval"
    assert ApprovalStore(tmp_path / "approvals.json").get(ticket["id"]) is not None


def test_wrap_tool_small_refund_allowed(config):
    tool = wrap_tool(RefundTool(), config)
    assert tool.run(customer="c-1", amount=50) == "refunded 50.0 to c-1"


def test_wrap_tool_is_idempotent(config):
    decisions = []
    config.on_decision = decisions.append
    tool = wrap_tool(SearchTool(), config)
    wrap_tool(tool, config)  # second wrap is a no-op
    tool.run(query="x")
    assert len(decisions) == 1


def test_wrap_tool_uses_agent_name_override(policy):
    seen = []
    config = AdapterConfig(policy=policy, on_decision=lambda d: seen.append(d.action["agent"]))
    tool = wrap_tool(SearchTool(), config, agent_name="researcher")
    tool.run(query="x")
    assert seen == ["researcher"]


def test_apply_policy_to_crew_wraps_every_tool(config):
    agent = Agent(
        role="Researcher",
        goal="Find things",
        backstory="You search.",
        tools=[SearchTool(), RefundTool()],
        llm="gpt-4o-mini",
    )
    crew = Crew(agents=[agent], tasks=[])
    assert apply_policy_to_crew(crew, config) is crew
    search, refund = agent.tools
    assert search.run(query="x") == "results for x"
    with pytest.raises(ApprovalRequiredError):
        refund.run(customer="c-1", amount=500)


def test_apply_policy_to_crew_uses_agent_roles(policy):
    seen = []
    config = AdapterConfig(policy=policy, on_decision=lambda d: seen.append(d.action["agent"]))
    agent = Agent(
        role="BillingBot",
        goal="Refunds",
        backstory="You refund.",
        tools=[RefundTool()],
        llm="gpt-4o-mini",
    )
    crew = Crew(agents=[agent], tasks=[])
    apply_policy_to_crew(crew, config)
    agent.tools[0].run(customer="c-1", amount=10)
    assert seen == ["BillingBot"]


def test_apply_policy_to_crew_twice_stays_single_wrapped(config):
    decisions = []
    config.on_decision = decisions.append
    agent = Agent(
        role="R",
        goal="g",
        backstory="b",
        tools=[SearchTool()],
        llm="gpt-4o-mini",
    )
    crew = Crew(agents=[agent], tasks=[])
    apply_policy_to_crew(crew, config)
    apply_policy_to_crew(crew, config)
    agent.tools[0].run(query="x")
    assert len(decisions) == 1


def test_missing_crewai_raises_helpful_error(config, monkeypatch):
    monkeypatch.setitem(sys.modules, "crewai", None)
    with pytest.raises(ImportError, match=r"pip install agent-policy-kit\[crewai\]"):
        wrap_tool(SearchTool(), config)
