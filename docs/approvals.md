# Approval gates

Some actions are not clearly safe or clearly forbidden. Refunding a
customer, deploying to staging, running a large warehouse scan: these
are judgment calls. The `approve` decision exists for exactly that
case. It means "not yet": the action waits for a human to approve or
deny it.

## How a request flows

1. Your code (or the CLI) evaluates an action. The decision comes back
   `approve` with a rule name and a reason.
2. The request is recorded in an approval store with a TTL (30 minutes
   by default). The action must not run until the request is approved.
3. A human approves or denies it. The resolution is written to the
   audit log alongside the original decision.
4. If nobody decides before the TTL, the request expires. Expired means
   denied: silence is never consent.

## The store

The store is a JSON file. It survives restarts, it is trivial to back
up, and you can read it with any tool. Each request carries the full
action that was judged, so the approver sees exactly what they are
deciding on.

Keep the store file where only your service and your approvers can
read it. It contains the actions your agents attempted, which is
operational detail you do not want lying around in world-readable
files.

## CLI flow

```bash
# evaluate; a needs-approval decision registers a request and exits 3
policy-kit check --policy deploy.yaml --action action.json \
  --approvals approvals.json --audit audit.log

# see what is waiting
policy-kit pending --approvals approvals.json

# decide
policy-kit approve apr-3f9a1c --approvals approvals.json --by anusha \
  --note "change window is fine" --audit audit.log
policy-kit deny apr-9b2e77 --approvals approvals.json --by on-call \
  --note "freeze is on" --audit audit.log
```

## Programmatic flow

```python
from agent_policy_kit import (
    ApprovalStore, evaluate, list_pending, load_policy,
    log_decision, log_resolution, request_approval, resolve_approval,
)

policy = load_policy("deploy.yaml")
decision = evaluate(policy, action)

if decision.decision == "approve":
    store = ApprovalStore("approvals.json")
    req = request_approval(store, action, policy.name,
                           decision.rule or "", ttl_minutes=30)
    log_decision("audit.log", decision, action, req["id"])
    notify_human(req)          # your Slack/email/pager hook goes here
elif decision.decision == "deny":
    log_decision("audit.log", decision, action)
    raise PermissionError(decision.reason)
```

To resolve:

```python
req = resolve_approval(store, request_id, approved=True,
                       by="on-call-human", note="change window is fine")
log_resolution("audit.log", req)
```

`resolve_approval` raises `KeyError` for unknown ids and `ValueError`
if the request is already resolved or expired. Design your notifier
around those: an expired request is a deny, not a retry.

## TTLs and expiry

The default TTL is 30 minutes. Pick it the way you pick any timeout:
long enough that a human can actually respond, short enough that a
stale approval cannot authorize something the world has moved past.
`list_pending` sweeps expired requests first, so what you see waiting
is always actionable. `sweep_expired` does the same without listing.

## When not to use approval gates

Approval gates are for actions where a human adds real judgment.
Routing every read through a human trains people to click approve
without reading, and then the gate is theater. Keep the approve set
small: money moving, production changing, data leaving. Everything else
should be allow or deny.
