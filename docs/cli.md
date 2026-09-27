# CLI reference

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
