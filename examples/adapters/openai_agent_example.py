"""Guard an OpenAI Agents SDK agent with a YAML policy.

The policy lives in ../policies/agent-frameworks.yaml: docs.search is
free, billing.refund up to $100 is free, anything bigger needs a human.

Run it:

    pip install agent-policy-kit[openai]
    python examples/adapters/openai_agent_example.py

Without OPENAI_API_KEY it runs the policy demos only (no model calls):
each tool is invoked through its guardrail directly so you can see
allow, approve, and deny without spending a cent. With a key it runs
the real agent end to end.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "src"))

from agent_policy_kit import ApprovalStore, load_policy  # noqa: E402
from agent_policy_kit.adapters import AdapterConfig  # noqa: E402
from agent_policy_kit.adapters.openai_agents import (  # noqa: E402
    policy_input_guardrail,
    policy_output_guardrail,
    wrap_function_tool,
)

POLICY_PATH = HERE.parent / "policies" / "agent-frameworks.yaml"


def build_tools():
    from agents import function_tool

    # name_override keeps the dotted names the policy expects; the SDK
    # would otherwise derive docs_search from the function name.
    @function_tool(name_override="docs.search")
    def docs_search(query: str) -> str:
        """Search the product docs."""
        return f"Docs say: '{query}' is covered on page 42."

    @function_tool(name_override="billing.refund")
    def billing_refund(customer: str, amount: float) -> str:
        """Refund a customer."""
        return f"Refunded ${amount} to {customer}."

    return docs_search, billing_refund


def make_config(tmp: Path) -> AdapterConfig:
    return AdapterConfig(
        policy=load_policy(POLICY_PATH),
        approval_store=ApprovalStore(tmp / "approvals.json"),
        audit_path=tmp / "audit.log",
        agent_name="billing-bot",
    )


def policy_only_demo() -> None:
    """Exercise the guardrails directly, no model, no key, no network."""
    import tempfile

    from agents.tool_context import ToolContext
    from agents.tool_guardrails import ToolInputGuardrailData

    with tempfile.TemporaryDirectory() as tmp:
        config = make_config(Path(tmp))
        docs_search, billing_refund = build_tools()
        guarded_search = wrap_function_tool(docs_search, config)
        guarded_refund = wrap_function_tool(billing_refund, config)

        def call(tool, args: dict):
            data = ToolInputGuardrailData(
                context=ToolContext(
                    context=None,
                    tool_name=tool.name,
                    tool_call_id="demo",
                    tool_arguments=json.dumps(args),
                ),
                agent=SimpleNamespace(name="billing-bot"),
            )
            return asyncio.run(tool.tool_input_guardrails[0].run(data))

        print("docs.search (allow):")
        out = call(guarded_search, {"query": "pricing"})
        print(f"  -> {out.behavior['type']} ({out.output_info['reason']})")

        print("billing.refund $50 (allow):")
        out = call(guarded_refund, {"customer": "c-1", "amount": 50})
        print(f"  -> {out.behavior['type']} ({out.output_info['reason']})")

        print("billing.refund $500 (approve -> reject_content with ticket):")
        out = call(guarded_refund, {"customer": "c-1", "amount": 500})
        print(f"  -> {out.behavior['type']}")
        print(f"  -> model sees: {out.behavior['message'][:100]}...")

        print("users.delete (deny -> tripwire):")
        data = ToolInputGuardrailData(
            context=ToolContext(
                context=None,
                tool_name="users.delete",
                tool_call_id="demo",
                tool_arguments=json.dumps({"id": "u-9"}),
            ),
            agent=SimpleNamespace(name="billing-bot"),
        )
        out = asyncio.run(guarded_search.tool_input_guardrails[0].run(data))
        print(f"  -> {out.behavior['type']} ({out.output_info['reason']})")


def live_agent_demo() -> None:
    """Run the real agent. Needs OPENAI_API_KEY."""
    import tempfile

    from agents import Agent, Runner

    with tempfile.TemporaryDirectory() as tmp:
        config = make_config(Path(tmp))
        docs_search, billing_refund = build_tools()
        agent = Agent(
            name="billing-bot",
            instructions=(
                "You are a careful billing assistant. Use docs_search for "
                "questions and billing_refund for refunds."
            ),
            tools=[
                wrap_function_tool(docs_search, config),
                wrap_function_tool(billing_refund, config),
            ],
            input_guardrails=[policy_input_guardrail(config)],
            output_guardrails=[policy_output_guardrail(config)],
        )
        result = Runner.run_sync(agent, "Refund $40 to customer c-1 for the outage.")
        print(result.final_output)


if __name__ == "__main__":
    policy_only_demo()
    print()
    if os.environ.get("OPENAI_API_KEY"):
        live_agent_demo()
    else:
        print("(Set OPENAI_API_KEY to run the live agent demo too.)")
