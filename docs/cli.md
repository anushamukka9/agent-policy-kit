# CLI reference

## validate

Check policy files for schema errors without evaluating anything.
Useful in CI and in an editor hook, before a broken policy ships.

```bash
policy-kit validate policy.yaml org-baseline.yaml
```

Prints one line per file (`valid` with the policy name and rule
count, or `INVALID` with the reason) and exits 0 only when every file
is valid.

## explain

Show the rule-by-rule trace for one action: which rules fired, which
did not, and exactly which condition said no. This is the debugging
companion to `check`.

```bash
policy-kit explain --policy policy.yaml --action action.json
```

```
decision: deny
rule:     (policy default)
reason:   no rule matched, policy default -> deny

rule trace (6 rules, first match wins):
  x kb-search (allow)
      - tool 'users.delete' did not match 'kb.search'
  ...
```

`--format json` gives the same trace machine-readable, for editor
integrations or policy test harnesses.

## check

Evaluate one action against a policy.

```bash
policy-kit check --policy policy.yaml --action action.json
policy-kit check --policy policy.yaml --action action.json --format json
policy-kit check --policy policy.yaml --action action.json \
  --approvals approvals.json --audit audit.log --ttl-minutes 15
```

The action file is a JSON object with `agent`, `tool`, `args`, and
optionally `resource`.

Repeat `--policy` to compose several policies into one decision, with
`--combine` choosing the precedence (`deny_overrides`,
`allow_overrides`, or `first_wins`; see
[composition](composition.md)):

```bash
policy-kit check --policy org-baseline.yaml --policy deploy-bot.yaml \
  --action planned-action.json --combine deny_overrides
```

Add `--dry-run` to compute the decision without recording anything:
no approval request is created, nothing is written to the audit log.
Use it when you are testing a policy or previewing what a change would
do.

Exit codes, which are the contract your CI relies on:

| Code | Meaning |
|---|---|
| 0 | allow: the action may proceed |
| 2 | deny: the action must not proceed (also used for CLI errors) |
| 3 | approve: a human must decide; the request id is printed |

With `--approvals`, an `approve` decision registers the request in the
store and the id is printed. With `--audit`, every decision is
appended to the audit log. `--ttl-minutes` sets the approval TTL
(default 30).

## pending

List approval requests still waiting for a human. Expired ones are
swept first, so the list is always actionable.

```bash
policy-kit pending --approvals approvals.json
policy-kit pending --approvals approvals.json --format json
```

## approve / deny

Resolve a pending request. Only pending requests resolve; anything
else is an error.

```bash
policy-kit approve apr-3f9a1c --approvals approvals.json --by anusha \
  --note "change window is fine" --audit audit.log
policy-kit deny apr-9b2e77 --approvals approvals.json --by on-call \
  --note "freeze is on" --audit audit.log
```

## audit

Show recent audit log entries, newest last.

```bash
policy-kit audit --audit audit.log --tail 20
```

## CI usage

Gate a deployment on policy: evaluate the planned action and fail the
job unless it is explicitly allowed.

```yaml
- name: Policy check
  run: |
    policy-kit check --policy policies/deploy-bot.yaml \
      --action planned-action.json --audit audit.log
```

Exit 2 fails the step on deny, exit 3 fails it on needs-approval (a
human decides out of band, then re-runs). Exit 0 lets the job
continue. If your pipeline needs the approval to happen inside the
run, register the request with `--approvals` and have a second job
poll `policy-kit pending` until the request resolves.
