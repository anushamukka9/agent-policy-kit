# Framework adapters

The core library decides; the adapters enforce. `agent_policy_kit.adapters`
wraps your framework's tool calls with the same YAML policies you test
with the CLI, so the policy you reviewed is the policy that runs.

Two frameworks today: the **OpenAI Agents SDK** (`pip install
agent-policy-kit[openai]`) and **CrewAI** (`pip install
agent-policy-kit[crewai]`). The framework packages are optional
dependencies; the adapters import them lazily, so a plain install
never pulls in an SDK.

## The 30-second version

One config object drives either adapter:

```python
from agent_policy_kit import ApprovalStore, load_policy
from agent_policy_kit.adapters import AdapterConfig

config = AdapterConfig(
    policy=load_policy("policy.yaml"),
    approval_store=ApprovalStore("approvals.json"),
    audit_path="audit.log",
    agent_name="billing-bot",
)
```

OpenAI Agents SDK, per tool:

```python
from agents import Agent, Runner
from agent_policy_kit.adapters.openai_agents import (
    policy_input_guardrail,
    policy_output_guardrail,
    wrap_function_tool,
)

agent = Agent(
    name="billing-bot",
    instructions="You are a careful billing assistant.",
    tools=[wrap_function_tool(docs_search, config),
           wrap_function_tool(billing_refund, config)],
    input_guardrails=[policy_input_guardrail(config)],
    output_guardrails=[policy_output_guardrail(config)],
)
Runner.run_sync(agent, "Refund $40 to customer c-1.")
```

CrewAI, whole crew at once:

```python
from agent_policy_kit.adapters.crewai import apply_policy_to_crew

crew = Crew(agents=[billing_agent], tasks=[refund_task])
apply_policy_to_crew(crew, config)
crew.kickoff()
```

Runnable examples with no API key needed: `examples/adapters/`.

## What happens on each decision

| Decision | OpenAI tool guardrail | OpenAI agent guardrail | CrewAI wrapped tool |
|---|---|---|---|
| allow | tool runs | run continues | tool runs |
| deny | tripwire exception, run halts | tripwire, run halts | `PolicyDeniedError` raised |
| approve | call rejected with a message quoting the approval ticket id; the model can ask the user, then retry | tripwire with the ticket id in `output_info` | `ApprovalRequiredError` raised; ticket queued |

Every evaluation is written to the audit log first, including the
ones that stop the tool call. Denials and approvals are visible after
the fact, not just in the moment.

## The approve loop, end to end

`approve` means "a human decides". The adapter queues the ticket in
your `ApprovalStore` and raises `ApprovalRequiredError` carrying the
ticket. The human resolves it the usual way:

```bash
policy-kit approve apr-3f9a1c --approvals approvals.json --by anusha
```

or from Python with `resolve_approval`. When the agent retries the
exact same action (same agent, tool, and args), the adapter finds the
approved, unexpired ticket and lets the call through as `allow`. No
new ticket, no second prompt. The grant lasts until the ticket
expires (30 minutes by default, configurable per adapter).

If you run without an approval store, `approve` still raises, it just
cannot queue a ticket. Point at a store before you rely on the loop.

## Policy sets

`AdapterConfig` takes `policy=` or `policy_set=` (exactly one). A set
lets the org baseline ride along with the team policy:

```python
from agent_policy_kit import load_policy_set

config = AdapterConfig(
    policy_set=load_policy_set(["org-baseline.yaml", "team.yaml"]),
    approval_store=ApprovalStore("approvals.json"),
)
```

With the default `deny_overrides` strategy the baseline stays a floor
no team policy can punch through. See [composition](composition.md).

## The OpenAI pieces in detail

- `wrap_function_tool(tool, config)` returns a copy of the
  `FunctionTool` with the policy guardrail appended. Name,
  description, and JSON schema are untouched; guardrails already on
  the tool keep running first. The guardrail reads the real tool name
  and the parsed arguments from the SDK's `ToolContext`, so your
  policy matches on what the model actually sent. Tip: the SDK
  derives tool names from function names (`billing_refund`); use
  `name_override="billing.refund"` if your policy uses dotted names.
- `policy_input_guardrail(config)` / `policy_output_guardrail(config)`
  judge the prompt and the final answer as `agent.input` /
  `agent.output` actions. Both `deny` and `approve` trip the
  guardrail; the decision, rule, and ticket id ride along in
  `output_info`.
- `PolicyRunHooks(config)` is a factory returning a `RunHooks`
  instance that checks every tool call on the run. Deny and approve
  raise out of `on_tool_start` and abort the run. It is the blunt
  instrument: no per-tool setup, but the model never learns why the
  call died. Prefer the tool guardrails when the model should see the
  denial or the ticket.

## The CrewAI pieces in detail

CrewAI has no tool middleware, so `wrap_tool` wraps each tool's
`_run` in place. The tool keeps its class, name, description, and
argument schema; only the execution path gains a policy check.
Wrapping the same tool twice is a no-op, and `apply_policy_to_crew`
uses each agent's `role` as the policy's agent name so one policy can
treat agents differently.

## Overhead

Measured locally with `benchmarks/run_adapter_overhead.py` (3,000
calls each, policy `examples/policies/agent-frameworks.yaml`):

| Path | Mean | p95 |
|---|---|---|
| bare `evaluate()` | 15.2 us | 16.0 us |
| OpenAI Agents SDK tool guardrail | 42.6 us | 50.5 us |
| CrewAI wrapped `tool.run()` | 23.7 us | 29.2 us |

The check is microseconds. Model latency dominates every real run by
four to six orders of magnitude, so guarding every tool call costs
you nothing you will ever notice. Re-run the script on your own
machine; the JSON lands in `benchmarks/adapter-overhead.json`.

## Honest limitations

- The adapters see tool names and arguments, not intent. A policy
  that matches `billing.refund` cannot tell a legitimate refund from
  a socially-engineered one with the same arguments. Name tools well
  and keep the approve gate for anything irreversible.
- Agent-level input/output guardrails judge text with the same
  deterministic matchers as everything else. They catch
  well-formed cases, not clever paraphrases.
- The approval grant matches the exact action. Change one argument
  and it is a new action needing a new ticket. That is deliberate.
- CrewAI wrapping is in-place on the tool object. If you construct
  tools once and share them across crews, wrap once and reuse.
- These adapters enforce; they do not sandbox. A tool the policy
  allows still runs with the tool's own permissions. Least privilege
  on the tool side is your job too.
