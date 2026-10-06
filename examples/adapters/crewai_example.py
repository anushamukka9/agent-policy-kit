"""Guard a CrewAI crew with a YAML policy.

The policy lives in ../policies/agent-frameworks.yaml: docs.search is
free, billing.refund up to $100 is free, anything bigger needs a human.

Run it:

    pip install agent-policy-kit[crewai]
    python examples/adapters/crewai_example.py

One call to apply_policy_to_crew wraps every tool of every agent. The
demo below runs the tools directly (no LLM calls, no key needed) so
you can see allow, approve, and deny. Set ANTHROPIC_API_KEY or
OPENAI_API_KEY and uncomment the kickoff line at the bottom to watch
the crew run for real.
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "src"))

from agent_policy_kit import (  # noqa: E402
    ApprovalRequiredError,
    ApprovalStore,
    PolicyDeniedError,
    load_policy,
    resolve_approval,
)
from agent_policy_kit.adapters import AdapterConfig  # noqa: E402
from agent_policy_kit.adapters.crewai import apply_policy_to_crew  # noqa: E402

POLICY_PATH = HERE.parent / "policies" / "agent-frameworks.yaml"


def build_crew():
    from crewai import Agent, Crew, Task
    from crewai.tools import BaseTool

    class DocsSearch(BaseTool):
        name: str = "docs.search"
        description: str = "Search the product docs."

        def _run(self, query: str) -> str:
            return f"Docs say: '{query}' is covered on page 42."

    class BillingRefund(BaseTool):
        name: str = "billing.refund"
        description: str = "Refund a customer."

        def _run(self, customer: str, amount: float) -> str:
            return f"Refunded ${amount} to {customer}."

    agent = Agent(
        role="BillingBot",
        goal="Answer billing questions and issue refunds per policy.",
        backstory="You are careful with other people's money.",
        tools=[DocsSearch(), BillingRefund()],
        llm="gpt-4o-mini",
        verbose=False,
    )
    task = Task(
        description="Refund $40 to customer c-1 for the outage.",
        expected_output="A confirmation of the refund.",
        agent=agent,
    )
    return Crew(agents=[agent], tasks=[task], verbose=False)


def main() -> None:
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = AdapterConfig(
            policy=load_policy(POLICY_PATH),
            approval_store=ApprovalStore(tmp_path / "approvals.json"),
            audit_path=tmp_path / "audit.log",
            agent_name="BillingBot",
        )
        crew = build_crew()
        apply_policy_to_crew(crew, config)
        search, refund = crew.agents[0].tools

        print("docs.search (allow):")
        print(f"  -> {search.run(query='pricing')}")

        print("billing.refund $40 (allow):")
        print(f"  -> {refund.run(customer='c-1', amount=40)}")

        print("billing.refund $500 (approve -> ticket queued):")
        try:
            refund.run(customer="c-1", amount=500)
        except ApprovalRequiredError as exc:
            ticket = exc.approval_request
            print(f"  -> ApprovalRequiredError, ticket {ticket['id']}")
            resolve_approval(config.approval_store, ticket["id"], True, by="anusha")
            print("  -> human approved; retrying:")
            print(f"  -> {refund.run(customer='c-1', amount=500)}")

        print("users.delete (deny):")
        from crewai.tools import BaseTool

        class UserDelete(BaseTool):
            name: str = "users.delete"
            description: str = "Delete a user."

            def _run(self, id: str) -> str:
                return "deleted"

        from agent_policy_kit.adapters.crewai import wrap_tool

        deleter = wrap_tool(UserDelete(), config)
        try:
            deleter.run(id="u-9")
        except PolicyDeniedError as exc:
            print(f"  -> PolicyDeniedError: {exc.decision.reason}")

        # To watch the crew run for real (needs an LLM key):
        # crew.kickoff()


if __name__ == "__main__":
    main()
