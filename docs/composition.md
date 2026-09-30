# Policy composition

One policy is rarely enough. The usual shape is a baseline plus team
policies: the org says "no prod deploys outside business hours, ever",
the deploy team says "staging deploys need a human, nothing more". This
page is about combining those layers into one decision without hiding
the layering.

## The pieces

A `PolicySet` is an ordered list of policies plus a strategy:

```python
from agent_policy_kit import evaluate_set, load_policy_set

pset = load_policy_set(
    ["policies/org-baseline.yaml", "policies/deploy-bot.yaml"],
    strategy="deny_overrides",
)
decision = evaluate_set(pset, {"tool": "deploy", "args": {"env": "prod"}})
print(decision.decision, "-", decision.reason)
# deny - policy 'org-baseline-policy' default -> deny (deny_overrides across 2 policies)
```

The decision keeps the winning policy's name and rule, and the reason
names the strategy. The audit log shows exactly which layer decided,
which is the whole point: composition you cannot see is composition you
cannot debug.

## The strategies

- `deny_overrides` (default): the most restrictive decision wins. Any
  deny beats any approve, any approve beats allow. Use it when the
  baseline is a safety floor no team policy may punch through.
- `allow_overrides`: the most permissive decision wins. Any allow beats
  any approve, any approve beats deny. Use it when the extra policies
  are carve-outs from a strict baseline.
- `first_wins`: policies are ordered most-specific to
  least-specific, like CSS. The first policy with a matching rule
  decides; when no policy has a matching rule, the first policy's
  default decides. Use it when you think of the stack as overrides.

Ties inside `deny_overrides` and `allow_overrides` go to the earlier
policy in the list. The order is meaningful, and the result is
deterministic: the same stack and action always give the same decision.

## From the CLI

Repeat `--policy` and pick the precedence with `--combine`:

```bash
policy-kit check --policy org-baseline.yaml --policy deploy-bot.yaml \
  --action planned-action.json --combine deny_overrides
```

With a single `--policy`, `--combine` is ignored and `check` behaves
exactly as before.

## Which strategy do I want?

Ask what the extra policies are for. If they tighten a loose default,
`deny_overrides` keeps the floor under them. If they loosen a strict
baseline for trusted cases, `allow_overrides` makes the carve-outs
visible. If the policies are written as a deliberate override chain,
`first_wins` reads the most naturally.

One caution: composing two policies that both default to `deny` means
anything neither policy mentions is denied, whichever strategy you
pick. That is usually what you want from a baseline, but say it out
loud in the policy descriptions so the next reader knows it was
deliberate. `examples/policies/org-baseline.yaml` is the worked
example; `examples/policy_composition.py` runs all three strategies
over the same actions so you can compare.
