# Audit log

Every evaluation can write one JSONL line. Every approval resolution
writes one too. The log is the trail you show an incident responder or
an auditor: what the agent tried, what the policy said, who decided,
and when.

## Format

One JSON object per line. Two record kinds:

```json
{"ts": "2026-09-27T19:02:11+00:00", "kind": "decision", "policy": "deploy-bot-policy",
 "rule": "staging-deploy-needs-approval", "decision": "approve",
 "reason": "rule 'staging-deploy-needs-approval' matched (tool, args.env) -> approve",
 "approval_request_id": "apr-3f9a1c",
 "action": {"agent": "deploy-bot", "tool": "deploy", "args": {"env": "staging"}}}
{"ts": "2026-09-27T19:04:02+00:00", "kind": "resolution", "policy": "deploy-bot-policy",
 "rule": "staging-deploy-needs-approval", "decision": "approved",
 "reason": "request apr-3f9a1c approved by on-call-human",
 "approval_request_id": "apr-3f9a1c",
 "action": {"agent": "deploy-bot", "tool": "deploy", "args": {"env": "staging"}}}
```

`rule` is null when the policy default decided. `approval_request_id`
is null for straight allow/deny decisions.

## Reading it

```bash
policy-kit audit --audit audit.log --tail 20
```

Or from Python with `read_tail(path, n)`, which returns the last n
records oldest-first as dicts. The file is plain JSONL, so `jq`,
`grep`, and your log shipper all work on it directly.

## Retention and care

- The log only grows. Rotate it the way you rotate any log: daily
  files, compression, a retention policy your compliance folks agree
  with.
- It records attempted actions, which can include arguments with
  customer data. Treat the log with the same care as the data it
  describes. If your actions carry PII, redact before logging or keep
  the log in the same trust boundary as the source system.
- Ship it somewhere tamper-evident if the audit trail matters for
  compliance. This library writes the lines; it does not sign them.
  (Signing the log is on the roadmap.)
