"""Measure the per-call overhead of the framework adapters.

This is a wiring benchmark, not a model benchmark: it times how long
the policy check itself takes on top of a tool call, so you know what
guarding every tool costs you. Run it yourself:

    python benchmarks/run_adapter_overhead.py

It writes benchmarks/adapter-overhead.json with the raw numbers.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from agent_policy_kit import load_policy  # noqa: E402
from agent_policy_kit.adapters import AdapterConfig, build_action  # noqa: E402
from agent_policy_kit.engine import evaluate  # noqa: E402

N = 3000
WARMUP = 200
POLICY_PATH = HERE.parent / "examples" / "policies" / "agent-frameworks.yaml"


def _percentile(values, pct):
    ordered = sorted(values)
    index = min(int(len(ordered) * pct / 100), len(ordered) - 1)
    return ordered[index]


def _timeit(label, fn, n=N):
    for _ in range(WARMUP):
        fn()
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1e6)
    result = {
        "label": label,
        "n": n,
        "mean_us": round(statistics.mean(samples), 1),
        "p95_us": round(_percentile(samples, 95), 1),
    }
    print(f"{label}: mean {result['mean_us']} us, p95 {result['p95_us']} us (n={n})")
    return result


def main():
    policy = load_policy(POLICY_PATH)
    config = AdapterConfig(policy=policy, agent_name="billing-bot")
    action = build_action("billing-bot", "billing.refund", {"customer": "c-1", "amount": 50})

    results = []
    results.append(_timeit("bare evaluate() baseline", lambda: evaluate(policy, action)))

    # OpenAI Agents SDK: the full tool-guardrail function, as the SDK calls it.
    from agents.tool_context import ToolContext
    from agents.tool_guardrails import ToolInputGuardrailData

    from agent_policy_kit.adapters.openai_agents import policy_tool_guardrail

    guardrail = policy_tool_guardrail(config)
    arguments = json.dumps({"customer": "c-1", "amount": 50})
    # One event loop for the whole run, like the SDK's runner does.
    # (asyncio.run per call would measure loop startup, not the check.)
    loop = asyncio.new_event_loop()

    def openai_call():
        data = ToolInputGuardrailData(
            context=ToolContext(
                context=None,
                tool_name="billing.refund",
                tool_call_id="bench",
                tool_arguments=arguments,
            ),
            agent=SimpleNamespace(name="billing-bot"),
        )
        loop.run_until_complete(guardrail.run(data))

    results.append(_timeit("openai-agents tool guardrail", openai_call))
    loop.close()

    # CrewAI: a wrapped tool executed through tool.run(), as crews do.
    from crewai.tools import BaseTool

    from agent_policy_kit.adapters.crewai import wrap_tool

    class BenchTool(BaseTool):
        name: str = "billing.refund"
        description: str = "Refund a customer."
        max_usage_count: int = 10_000_000

        def _run(self, customer: str, amount: float) -> str:
            return "ok"

    tool = wrap_tool(BenchTool(), config)
    results.append(_timeit("crewai wrapped tool.run()", lambda: tool.run(customer="c-1", amount=50)))

    out_path = HERE / "adapter-overhead.json"
    out_path.write_text(
        json.dumps(
            {
                "policy": str(POLICY_PATH.name),
                "python": sys.version.split()[0],
                "results": results,
                "note": (
                    "Adapter overhead per tool call, measured locally. "
                    "The policy check is microseconds; model latency "
                    "dominates every real run by orders of magnitude."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
