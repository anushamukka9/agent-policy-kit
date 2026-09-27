# Policy format

Policies are YAML. One file, one policy, plain language. Here is the
whole schema with a realistic example at the end.

## Top level

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | Policy name, used in decisions and the audit log. |
| `version` | no | Integer, defaults to 1. Bump it when you change the rules. |
| `description` | no | One or two sentences on what this policy is for. |
| `default` | no | `allow`, `deny`, or `approve` when no rule matches. Defaults to `deny`. |
| `rules` | no | Ordered list of rules. First match wins. |

Rule names must be unique inside a policy. Rules are checked top to
bottom, so put the specific rules before the general ones.

## Rules

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | Unique rule name. Shows up in decisions and the audit log. |
| `description` | no | What this rule is for, in plain words. |
| `decision` | yes | `allow`, `deny`, or `approve` (route to a human). |
| `match` | yes | Conditions. All of them must hold (AND). |

## Match conditions

Conditions describe the action being judged. An action is a dict with
`agent`, `tool`, `args` (a dict), and optionally `resource`.

- `agent`, `tool`, `resource`: a string matched with glob patterns
  (`tickets.*`, `support-*`). Without wildcards it is an exact match.
  Prefix with `regex:` for a full regular-expression match.
- `args.<field>`: the value of a field inside `args`. Three forms:
  - A plain value: exact match for numbers and booleans, glob match
    for strings. `"USD"` matches the string USD; `200` matches the
    number 200.
  - An operator mapping: `{gte: 100}`, `{lt: 5}`, `{eq: "USD"}`,
    `{ne: "prod"}`, `{in: [a, b]}`, `{not_in: [a, b]}`,
    `{regex: ".*_pii$"}`, `{glob: "logs/*"}`, `{exists: true}`,
    `{contains: "rm -rf"}`.
  - Missing fields never match, except `{exists: false}` which matches
    only when the field is absent. This is deliberate: a policy must
    never allow something it cannot see.
- `time.window`: `"HH:MM-HH:MM"` in UTC. The start is inclusive, the
  end is exclusive. Overnight windows like `"22:00-06:00"` work.
- `any_of`: a list of condition groups; at least one group must match
  (OR). Each group uses the same keys as above.
- Placeholders: `{agent}`, `{tool}`, `{resource}`, and `{args.field}`
  inside a match string are replaced with the action's values before
  matching. This is how one policy serves many agent instances.

## Example

```yaml
name: support-bot-policy
version: 1
description: Read-only support bot. Own tickets only, small refunds need approval.
default: deny

rules:
  - name: read-own-ticket
    description: Agents may read tickets assigned to them.
    decision: allow
    match:
      tool: tickets.read
      args.assignee: "{agent}"

  - name: small-refund-needs-approval
    description: Refunds up to $200 go to a human, never straight through.
    decision: approve
    match:
      tool: refund.issue
      args.amount: {lte: 200}

  - name: no-user-management
    description: The bot must never touch user accounts.
    decision: deny
    match:
      tool: "users.*"
```

## Tips from writing a few of these

- Keep the default `deny`. An allowlist you wrote yourself beats a
  denylist you hope is complete.
- Order rules from specific to general. The first match wins, so a
  broad rule placed early will swallow the careful ones below it.
- Denials should explain themselves. The `reason` on every decision
  names the rule and the conditions that matched; write rule
  descriptions a responder can act on at 2am.
- Test policies the way you test code. The bundled benchmark runner
  (`python -m agent_policy_kit.benchmark`) checks 48 labeled scenarios
  against the three example policies; add your own cases to
  `benchmarks/cases.jsonl` when the policy changes.
