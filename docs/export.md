# Exporting policies to OPA/Rego and Cedar

Plenty of teams already run a policy engine. If yours runs OPA or
Cedar, you should not have to rewrite your agent policies by hand to
use them there, and if you start with this kit you should not be
locked into it. The exporter translates a YAML policy into Rego (for
Open Policy Agent) or Cedar (for Amazon Verified Permissions and
other Cedar authorizers).

```bash
policy-kit export --policy support-bot.yaml --format rego --output support-bot.rego
policy-kit export --policy support-bot.yaml --format cedar --output support-bot.cedar
```

```python
from agent_policy_kit import export_policy, load_policy

result = export_policy(load_policy("support-bot.yaml"), "cedar")
print(result.text)
for warning in result.warnings:
    print("warning:", warning)
```

One command per policy. Composed policy sets export one file at a
time; the composition strategies in this kit (deny_overrides and
friends) are not OPA or Cedar concepts, so export each layer and let
the target engine combine them its own way.

Every export returns the text plus a list of warnings. Warnings are
the contract: anything the target language could not say out loud is
listed there, never silently dropped. The CLI prints warnings to
stderr and still writes the file, so read them before you ship the
export.

Generated examples for the support bot live in `examples/exports/`.

## Rego: the faithful export

The Rego module preserves the source semantics, including the parts
most exporters lose:

- Rule order is preserved. Rules become entries in a `matched` set
  and the result takes the smallest index, so first-match-wins works
  exactly like the built-in engine.
- All three decisions survive. The result is an object:
  `{"decision": "allow" | "deny" | "approve", "rule": <name or null>}`.
  The policy default becomes the Rego `default result`.
- Every match construct maps: globs become `glob.match` (with a
  `null` delimiter set, so `*` crosses dots the way fnmatch does in
  the Python engine), `regex:` patterns are anchored to a full
  match, numeric and membership operators map directly, `exists`
  becomes an `object.keys` check, `length` becomes `count`,
  `any_of` becomes alternate rule bodies, and `not` blocks become
  helper rules referenced with `not`.

The action goes in as `input` in the same shape this kit uses
(`input.agent`, `input.tool`, `input.resource`, `input.args`), and
the output targets OPA 1.0 syntax:

```bash
opa eval --data support-bot.rego --input action.json \
  'data.agentpolicykit.support_bot_policy.result'
```

Two deliberate differences to know about:

- Time windows read the wall clock. The Python engine takes a
  caller-supplied `now`; the exported module calls `time.now_ns()`
  in UTC. Near a window edge the two can disagree for a moment.
- Numbers compare the Rego way. The Python engine is strict about
  types (a bool is not a number); Rego is a little looser. Policies
  that lean on bool-versus-number distinctions are rare, but that is
  the one corner where the engines can differ.

How much do I trust this? I ran it against the real thing: all 59
bundled benchmark scenarios were evaluated in OPA 1.9 and in the
Python engine, and every one produced the same decision and the
same rule name. A second synthetic policy exercising `any_of`,
`not_in`, `exists: false`, embedded placeholders, `length`, and
`contains` matched on 12 of 12 actions as well. That is strong
evidence for the constructs the benchmark covers; it is not a proof
for every policy you might write, so run your own actions through
both engines before you cut over.

## Cedar: the honest, lossy export

Cedar is a different shape of language, and pretending otherwise
would produce wrong policies. The exporter tells you exactly what
changed, in the warnings and in comments inside the file:

- `allow` becomes `permit`, `deny` becomes `forbid`.
- `approve` becomes `forbid`, with a warning per rule. Cedar has no
  third decision. Failing closed is the safe translation, but the
  human step now has to happen outside Cedar: treat that forbid as
  "needs a human", not as a hard no. If you need real approval
  semantics in the loop, keep that rule in this kit.
- There is no rule order in Cedar. Any matching `forbid` beats
  every `permit`. When a policy mixes permits and forbids you get a
  warning, because a source policy whose early allow rule was meant
  to shadow a later deny rule will come out stricter than the
  original.
- Rules Cedar cannot express are left out, as `NOT EXPORTED`
  comments naming the rule and the reason, plus a warning. Today
  that list is: time windows and day restrictions (no clock),
  regex matches (no regular expressions), the `length` operator
  (no size operation for strings or sets), glob character classes
  and `?` wildcards (Cedar `like` only has `*`), and placeholders
  embedded inside a longer string (no string interpolation).
- Default handling: a `deny` default needs no policy (Cedar forbids
  whatever no permit matches). An `allow` default becomes a
  trailing unconditional `permit`, with a warning that forbids
  still override it. An `approve` default cannot be expressed;
  nothing is emitted and unmatched actions are implicitly
  forbidden, which the warning calls out.

The generated policies read the action from Cedar `context`, so
your authorizer call needs to supply it:

| Context field | Meaning |
|---|---|
| `context.agent` | The acting agent's name |
| `context.tool` | The tool being called |
| `context.resource` | The resource, when the action has one |
| `context.args` | A record mirroring the action's `args` |

One encoding note: Cedar has no list type. Argument values that are
lists in the action should be passed as Cedar sets, and numbers as
Cedar longs. Argument accesses are guarded with `has` checks
(`context.args has "amount" && ...`) because Cedar raises an error
on a missing record attribute where the Python engine simply calls
it a non-match; the guards keep the exported behavior aligned.

The Cedar output was verified to parse and evaluate in a real Cedar
authorizer across all five bundled example policies, including the
approve-as-forbid refund rule and the `not` carve-out in the org
baseline. Semantic equivalence beyond those checks is yours to test,
especially where warnings fired.

## Which one should you pick?

If your platform team already runs OPA, export Rego and you keep
everything, including approvals. If you are on AWS and Verified
Permissions is the house standard, export Cedar and accept the
approval and ordering caveats above, or keep the approval-heavy
policies in this kit and export only the allow/deny layer. Either
way, the YAML stays the source of truth: change the policy, export
again, and diff the output like you would any generated code.
