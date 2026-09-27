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
bot, data analyst), `examples/check_example.py` (five actions against
the support policy), and `examples/approval_flow.py` (the full
request-approve-audit loop against throwaway files).

## What the match language can express

- Tool, agent, and resource matching with globs (`tickets.*`) or
  `regex:` patterns.
- Argument checks: exact values, `{gte: 100}` / `{lte: 200}` style
  numeric comparisons, `{in: [...]}`, `{regex: ...}`, `{exists: true}`,
  `{contains: ...}`.
- Time windows (`time.window: "09:00-17:00"`, UTC, overnight windows
  work) for rules like "no prod deploys at night".
- `any_of` groups for OR conditions.
- `{agent}` / `{args.field}` placeholders, so one policy serves every
  bot instance.
- Missing fields never match (except `{exists: false}`). A policy must
  never allow something it cannot see.

Full schema and tips in [docs/policies.md](docs/policies.md).
Approval flows in [docs/approvals.md](docs/approvals.md). The audit
log format in [docs/audit-log.md](docs/audit-log.md). CLI reference in
[docs/cli.md](docs/cli.md).

## Benchmarks

Fixtures under `benchmarks/`: 48 labeled action scenarios across the
three example policies, each with a note explaining why the expected
decision is right. Boundary cases included (refund of exactly $200,
deploy at exactly 17:00, query of exactly 100,000 rows). Run them
yourself:

```bash
python -m agent_policy_kit.benchmark
```

Results on the bundled set:

| Metric | Result |
|---|---|
| Accuracy (48 labeled scenarios) | 1.00 (48/48) |
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
- This library decides; it does not enforce. Your code must actually
  refuse to run denied actions and must actually wait on approvals. A
  policy nobody checks is a comment.

## Roadmap

- Signed audit log entries (Ed25519, verify at ingest)
- Multi-approver rules (two humans for destructive actions)
- Slack/email notifier hooks for pending requests
- Policy linter: unreachable rules, shadowed rules, overlapping
  approve/deny pairs
- Framework adapters (OpenAI Agents SDK, CrewAI) so guarding a tool
  call is one wrapper

## License

MIT. See LICENSE.
