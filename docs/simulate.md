# Simulating tool calls

`check` is for live traffic. `simulate` is for rehearsals: policy
edits, what-if questions, and regression tests for your rules. It
prints the decision and the exact rule that decided it, and it
records nothing. There is deliberately no `--approvals` and no
`--audit` option on this command, so a simulation can never create
an approval request or an audit line by accident.

## One tool call, inline

The fastest what-if needs no files at all:

```bash
policy-kit simulate --policy policy.yaml --tool refund.issue \
  --agent support-bot --args '{"ticket": "T-1042", "amount": 50}'
```

```
decision: approve
rule:     small-refund-needs-approval
reason:   rule 'small-refund-needs-approval' matched (tool, args.amount) -> approve
simulate: nothing recorded, no approval requested, no audit line written
```

Exit codes match `check`: 0 allow, 2 deny, 3 approve. `--resource`
sets the action's resource field. `--args` must be a JSON object.

## One tool call, from a file

```bash
policy-kit simulate --policy policy.yaml --action action.json
```

Same output shape as the inline form. Use this when the action is
already on disk, for example a planned deployment step your pipeline
wrote out.

## A batch of scenarios

This is where simulation earns its keep. A scenario file names a set
of tool calls and, optionally, the decision each one should get:

```bash
policy-kit simulate --policy policy.yaml --actions scenarios.json
```

```json
{"scenarios": [
  {"name": "small refund needs a human",
   "action": {"agent": "support-bot", "tool": "refund.issue",
              "args": {"ticket": "T-1042", "amount": 50}},
   "expect": "approve"},
  {"name": "refund one dollar over the ceiling",
   "action": {"agent": "support-bot", "tool": "refund.issue",
              "args": {"ticket": "T-1042", "amount": 201}},
   "expect": "deny"}
]}
```

```
small refund needs a human: approve via small-refund-needs-approval [expected approve: PASS]
refund one dollar over the ceiling: deny via large-refund-denied [expected deny: PASS]
summary: 2 scenarios, 0 allow, 1 deny, 1 approve
simulate: nothing recorded, no approval requested, no audit line written
```

Accepted file shapes: a JSON list of entries, a JSON object with a
`scenarios` list, or JSONL with one entry per line. An entry is
either wrapped (`name`, `action`, optional `expect`) or flat, with
the action fields (`agent`, `tool`, `args`, `resource`) sitting next
to `name` and `expect`.

### Expectations are a regression test for your policy

When any scenario carries an `expect`, the exit code changes
meaning: 0 when every expectation held, 2 when one failed. The
decision codes no longer apply, because the question changed from
"what would happen" to "did my policy edit break anything". Run the
batch in CI against a proposed policy change and a moved decision
fails the job, naming the scenario and the rule that decided it.

With no expectations anywhere, batch mode exits with the most
restrictive decision present (deny beats approve beats allow), using
the usual 0 / 2 / 3 codes.

## Composed policies

Repeat `--policy` and simulation evaluates the composed set, with
`--combine` choosing the precedence, exactly like `check`:

```bash
policy-kit simulate --policy org-baseline.yaml --policy deploy-bot.yaml \
  --actions scenarios.json --combine deny_overrides
```

The rule and policy named in each result are the winning layer's, so
you can see which policy in the stack did the deciding.

## From Python

```python
from agent_policy_kit import load_policy, load_scenarios, simulate, simulate_many

policy = load_policy("policy.yaml")

decision = simulate(policy, {"agent": "support-bot", "tool": "kb.search"})
print(decision.decision, decision.rule)

report = simulate_many(policy, load_scenarios("scenarios.json"))
print(report.counts)   # {"allow": ..., "deny": ..., "approve": ...}
print(report.ok)       # True when every expectation held
for failure in report.failures:
    print(failure.name, failure.decision.decision, failure.decision.rule)
```

`simulate_set` does the same against a composed `PolicySet`.

## How this differs from check --dry-run

`check --dry-run` is one action, evaluated live, with the recording
switched off. `simulate` is built for the rehearsal workflow around
it: inline tool calls with no action file to write, batches of named
scenarios, expectations that turn the batch into a policy regression
test, and a hard guarantee that recording is not merely off but
impossible, because the options for it do not exist here. If you are
asking "what happens for this one real call, right now", use
`check`. If you are asking "what would my policy do across the cases
I care about", use `simulate`.

## Walkthrough

`examples/simulate_walkthrough.py` runs all three forms against the
support-bot policy, and `examples/simulate-scenarios.json` is the
batch it checks: six scenarios, including the refund at exactly the
$200 approval ceiling and the one a dollar over it.
