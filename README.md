# agent-policy-kit

Policy-as-code guardrails for AI agents. You write YAML rules that say
what your agents may do. Every action gets a deterministic decision:
**allow**, **deny**, or **approve** (route to a human). No models, no
network calls, no guessing. The same action always gets the same
decision.

This is the enforcement half of my agent-security work. If
[llm-sentinel](https://github.com/anushamukka9/llm-sentinel) is the
bouncer checking what goes into the model, this is the rulebook
deciding what the agent is allowed to do once it acts.

## Why this exists

I kept watching the same failure mode: an agent with a powerful tool
and nobody watching the tool call. The usual answers are either "trust
the model to behave" (no) or a full policy engine that needs a team to
operate (also no, for most of us). I wanted the middle ground: a file
I can read in two minutes, rules I can explain in one sentence each,
and a third decision besides yes and no.

That third decision is the whole point. Plenty of agent actions are
not clearly safe and not clearly forbidden. Refunding a customer,
deploying to staging, running a big warehouse scan: these are judgment
calls, and judgment calls belong to humans. So the policy says
`approve`, the action waits, a person decides, and the decision lands
in the audit log. Expired approvals mean denied. Silence is never
consent.

## Quickstart

```bash
pip install agent-policy-kit
```

Write a policy:

```yaml
# policy.yaml
name: support-bot-policy
default: deny
rules:
  - name: read-own-ticket
    decision: allow
    match:
      tool: tickets.read
      args.assignee: "{agent}"
  - name: small-refund-needs-approval
    decision: approve
    match:
      tool: refund.issue
      args.amount: {lte: 200}
  - name: no-user-management
    decision: deny
    match:
      tool: "users.*"
```

Describe the action:

```json
// action.json
{"agent": "support-bot", "tool": "refund.issue",
 "args": {"ticket": "T-1042", "amount": 45}}
```

Evaluate it:

```bash
$ policy-kit check --policy policy.yaml --action action.json \
    --approvals approvals.json --audit audit.log
decision: approve
rule:     small-refund-needs-approval
reason:   rule 'small-refund-needs-approval' matched (tool, args.amount) -> approve
approval: apr-3f9a1c (pending, expires in 30 min)
$ echo $?
3
```

Exit codes are the CI contract: 0 allow, 2 deny, 3 needs a human. Then
the human decides:

```bash
$ policy-kit approve apr-3f9a1c --approvals approvals.json --by anusha \
    --note "customer confirmed" --audit audit.log
apr-3f9a1c approved by anusha
```

Or from Python:

```python
from agent_policy_kit import evaluate, load_policy

policy = load_policy("policy.yaml")
decision = evaluate(policy, {
    "agent": "support-bot",
    "tool": "refund.issue",
    "args": {"ticket": "T-1042", "amount": 4500},
})
print(decision.decision, "-", decision.reason)
# deny - no rule matched, policy default -> deny
```

Try the bundled examples: `examples/policies/` (support bot, deploy
bot, data analyst, org baseline), `examples/check_example.py` (five
actions against the support policy), `examples/approval_flow.py` (the
full request-approve-audit loop against throwaway files),
`examples/policy_composition.py` (baseline plus team policy under each
composition strategy), and `examples/explain_dryrun.py` (a rule-by-rule
trace of a surprising denial, plus a dry run). Framework examples live
in `examples/adapters/` (OpenAI Agents SDK and CrewAI, runnable without
an API key).

## What the match language can express

- Tool, agent, and resource matching with globs (`tickets.*`) or
  `regex:` patterns.
- Argument checks: exact values, `{gte: 100}` / `{lte: 200}` style
  numeric comparisons, `{in: [...]}`, `{regex: ...}`, `{exists: true}`,
  `{contains: ...}`, `{startswith: ...}`, `{endswith: ...}`, and
  `{length: {max: 5}}` for bounding strings, lists, and dicts.
- Time windows (`time.window: "09:00-17:00"`, UTC, overnight windows
  work) for rules like "no prod deploys at night", with optional
  `time.days: [mon, tue, wed, thu, fri]` for weekday restrictions.
- `any_of` groups for OR conditions, and `not` blocks for carve-outs
  (allow `api.*` except `api.admin.*`).
- `{agent}` / `{args.field}` placeholders, so one policy serves every
  bot instance.
- Missing fields never match (except `{exists: false}`). A policy must
  never allow something it cannot see.

Full schema and tips in [docs/policies.md](docs/policies.md).
Approval flows in [docs/approvals.md](docs/approvals.md). The audit
log format in [docs/audit-log.md](docs/audit-log.md). CLI reference in
[docs/cli.md](docs/cli.md). Policy composition in
[docs/composition.md](docs/composition.md). Framework adapters in
[docs/adapters.md](docs/adapters.md).

## Policy composition

Real deployments stack policies: an org baseline under team policies.
Repeat `--policy` and pick the precedence:

```bash
policy-kit check --policy org-baseline.yaml --policy deploy-bot.yaml \
  --action planned-action.json --combine deny_overrides
```

`deny_overrides` (the default) lets the most restrictive decision win,
so a baseline stays a floor no team policy can punch through.
`allow_overrides` lets carve-outs win. `first_wins` reads the stack as
an override chain, most-specific first. The decision names the winning
policy and the strategy, so the audit log shows how the layers
combined. Full story in [docs/composition.md](docs/composition.md); the
Python API is `load_policy_set` / `evaluate_set`.

## Framework adapters

The core library decides; the adapters enforce. Wrap your framework's
tool calls and the policy you tested with the CLI is the policy that
runs in production:

```python
from agent_policy_kit import ApprovalStore, load_policy
from agent_policy_kit.adapters import AdapterConfig
from agent_policy_kit.adapters.openai_agents import wrap_function_tool
# pip install agent-policy-kit[openai]

config = AdapterConfig(
    policy=load_policy("policy.yaml"),
    approval_store=ApprovalStore("approvals.json"),
    audit_path="audit.log",
    agent_name="billing-bot",
)

agent = Agent(
    name="billing-bot",
    tools=[wrap_function_tool(billing_refund, config)],
    ...
)
```

Or guard a whole CrewAI crew in one call (`pip install
agent-policy-kit[crewai]`):

```python
from agent_policy_kit.adapters.crewai import apply_policy_to_crew

apply_policy_to_crew(crew, config)
```

Deny stops the tool call. Approve queues a human-approval ticket and
stops the call; when the human approves, the agent's retry of the same
action goes through. Every decision lands in the audit log. Per-call
overhead is microseconds (see Benchmarks). Full guide, decision
tables, and the approve loop in [docs/adapters.md](docs/adapters.md);
runnable examples in `examples/adapters/` (no API key needed).

## Debugging: explain, validate, dry-run

When a decision surprises you, ask why:

```bash
policy-kit explain --policy policy.yaml --action action.json
```

It prints the rule-by-rule trace: which rules fired, and for the ones
that did not, exactly which condition said no. The same trace is
available from Python as `explain(policy, action)`.

Before a policy ships, check it parses:

```bash
policy-kit validate policy.yaml org-baseline.yaml
```

And when you are testing a policy change, `--dry-run` computes the
decision without recording anything: no approval request, no audit
log line.

## Benchmarks

Fixtures under `benchmarks/`: 59 labeled action scenarios across the
four example policies, each with a note explaining why the expected
decision is right. Boundary cases included (refund of exactly $200,
deploy at exactly 17:00, query of exactly 100,000 rows, a Saturday
morning prod deploy, six filters against a max of five). Run them
yourself:

```bash
python -m agent_policy_kit.benchmark
```

Results on the bundled set:

| Metric | Result |
|---|---|
| Accuracy (59 labeled scenarios) | 1.00 (59/59) |
| Precision / recall / F1 per decision (allow, deny, approve) | 1.00 / 1.00 / 1.00 |
| Mean evaluation latency | ~35 us per action (median of one run; varies a little by machine) |

Take these numbers for what they are: a wiring check, not a security
certification. The engine is deterministic, so a perfect score means
the loader, the matchers, rule ordering, and time windows behave the
way the cases say they should. It says nothing about whether your
policy is wise. Writing a good policy is the real work, and no
benchmark does it for you. The labeled set is small and hand-written;
add your own cases to `benchmarks/cases.jsonl` when your policy
changes.

Adapter overhead (3,000 calls each, `benchmarks/run_adapter_overhead.py`):

| Path | Mean | p95 |
|---|---|---|
| bare `evaluate()` | 15.2 us | 16.0 us |
| OpenAI Agents SDK tool guardrail | 42.6 us | 50.5 us |
| CrewAI wrapped `tool.run()` | 23.7 us | 29.2 us |

The check is microseconds; model latency dominates every real run by
four to six orders of magnitude. Guarding every tool call costs
nothing you will notice. Numbers vary a little by machine; re-run the
script yourself.

## CI usage

Gate a deployment on policy. Exit 2 fails the job on deny, exit 3 on
needs-approval, exit 0 lets it through:

```yaml
- name: Policy check
  run: |
    policy-kit check --policy policies/deploy-bot.yaml \
      --action planned-action.json --audit audit.log
```

Copy-paste snippets in [docs/cli.md](docs/cli.md).

## Honest limitations

- The policy is only as good as its author. A bad rule enforced
  perfectly is still a bad rule. Review policies like code, because
  they are code.
- Matching is deterministic string, glob, regex, and number
  comparison. It does not understand intent, and it will not catch a
  clever rephrasing of a forbidden action. Name your tools and
  arguments so the policy can see what is happening.
- Time windows use UTC. If your team thinks in local time, convert
  when you write the policy.
- The approval store is a JSON file. It is fine for a single service
  and a handful of approvers; it is not a distributed queue. Do not
  point two writers at it over NFS and expect happiness.
- The audit log is append-only JSONL, not signed. If the trail matters
  for compliance, ship it somewhere tamper-evident. Signing the log is
  on the roadmap.
- The core library decides; the adapters enforce. If you call
  `evaluate()` directly, your code must still refuse denied actions
  and must actually wait on approvals. A policy nobody checks is a
  comment. If you want enforcement for free, use the adapters.

## Roadmap

- Signed audit log entries (Ed25519, verify at ingest)
- Multi-approver rules (two humans for destructive actions)
- Slack/email notifier hooks for pending requests
- Policy linter: unreachable rules, shadowed rules, overlapping
  approve/deny pairs

## License

MIT. See LICENSE.
